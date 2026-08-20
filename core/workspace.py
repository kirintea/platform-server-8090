# -*- coding: utf-8 -*-

"""本地工作区管理器 — 为每个 session 提供隔离的文件系统目录

参考 AgentScope 的 LocalWorkspaceManager，简化为平台所需功能。

使用方式：
    manager = LocalWorkspaceManager(base_dir="./workspaces")
    workspace = await manager.get_workspace(user_id, session_id)
    # workspace.workdir 是该 session 的工作目录
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger

from core.validators import coerce_id


@dataclass
class Workspace:
    """工作区实例"""
    workdir: str
    """工作区根目录的绝对路径"""

    user_id: str
    """所属用户 ID"""

    session_id: str
    """所属会话 ID"""

    def resolve_path(self, relative_path: str) -> str:
        """将相对路径解析为绝对路径，确保不越界沙箱

        Args:
            relative_path: 相对于工作区根目录的路径

        Returns:
            绝对路径

        Raises:
            ValueError: 路径越界（超出工作区根目录）
        """
        # 使用 realpath 解析符号链接，防止通过软链接逃逸沙箱（fix #1）
        target = os.path.realpath(os.path.join(self.workdir, relative_path))
        workdir_norm = os.path.realpath(self.workdir)
        if not (target == workdir_norm or target.startswith(workdir_norm + os.sep)):
            raise ValueError(
                f"路径越界: '{relative_path}' 超出工作区范围 ({self.workdir})"
            )
        return target

    def list_files(self, sub_path: str = ".") -> list[dict]:
        """列出工作区中的文件

        Args:
            sub_path: 子目录路径（相对于工作区根目录）

        Returns:
            文件信息列表 [{"name": ..., "path": ..., "is_dir": ..., "size": ...}]
        """
        target = self.resolve_path(sub_path)
        if not os.path.isdir(target):
            return []

        result = []
        try:
            for entry in os.scandir(target):
                stat = entry.stat()
                result.append({
                    "name": entry.name,
                    "path": os.path.relpath(entry.path, self.workdir),
                    "is_dir": entry.is_dir(),
                    "size": stat.st_size if entry.is_file() else 0,
                    "modified": stat.st_mtime,
                })
        except PermissionError:
            pass

        result.sort(key=lambda x: (not x["is_dir"], x["name"]))
        return result

    def file_exists(self, relative_path: str) -> bool:
        """检查文件是否存在"""
        return os.path.isfile(self.resolve_path(relative_path))

    def dir_exists(self, relative_path: str) -> bool:
        """检查目录是否存在"""
        return os.path.isdir(self.resolve_path(relative_path))


class LocalWorkspaceManager:
    """本地工作区管理器

    为每个 (user_id, session_id) 提供独立的工作区目录。

    Args:
        base_dir: 工作区根目录（所有 session 工作区的父目录）
    """

    def __init__(self, base_dir: str = "./workspaces") -> None:
        self._base_dir = os.path.abspath(base_dir)
        os.makedirs(self._base_dir, exist_ok=True)
        logger.info("工作区管理器已初始化: {}", self._base_dir)

    async def get_workspace(
        self,
        user_id: str,
        session_id: str,
    ) -> Workspace:
        """获取或创建工作区

        Args:
            user_id: 用户 ID
            session_id: 会话 ID

        Returns:
            Workspace 实例
        """
        user_id = coerce_id(user_id)
        session_id = coerce_id(session_id)
        workdir = self._get_workdir(user_id, session_id)
        os.makedirs(workdir, exist_ok=True)
        return Workspace(
            workdir=workdir,
            user_id=user_id,
            session_id=session_id,
        )

    async def delete_workspace(
        self,
        user_id: str,
        session_id: str,
    ) -> bool:
        """删除工作区

        Args:
            user_id: 用户 ID
            session_id: 会话 ID

        Returns:
            是否成功删除
        """
        user_id = coerce_id(user_id)
        session_id = coerce_id(session_id)
        workdir = self._get_workdir(user_id, session_id)
        deleted = os.path.isdir(workdir)
        if deleted:
            shutil.rmtree(workdir, ignore_errors=True)
            logger.info("已删除工作区: {}", workdir)
        # 同时清理该 session 的 offload 卸载目录（fix #3），缺失不报错
        self._cleanup_offload_dir(user_id, session_id)
        return deleted

    async def workspace_exists(
        self,
        user_id: str,
        session_id: str,
    ) -> bool:
        """检查工作区是否存在"""
        workdir = self._get_workdir(user_id, session_id)
        return os.path.isdir(workdir)

    def list_workspaces(self, user_id: str) -> list[str]:
        """列出用户的所有工作区 session ID

        Args:
            user_id: 用户 ID

        Returns:
            session_id 列表
        """
        user_dir = os.path.join(self._base_dir, coerce_id(user_id))
        if not os.path.isdir(user_dir):
            return []
        return [
            name for name in os.listdir(user_dir)
            if os.path.isdir(os.path.join(user_dir, name))
        ]

    def _get_workdir(self, user_id: str, session_id: str) -> str:
        """获取工作区目录路径

        对 user_id / session_id 进行标识符规范化，防止 ``"/"``、``"../"``
        等被拼接到文件系统路径造成越界（fix #4）。
        """
        return os.path.join(
            self._base_dir, coerce_id(user_id), coerce_id(session_id)
        )

    # ------------------------------------------------------------------
    # Offloader 卸载目录布局约定（fix #2 / fix #3）
    #
    # 卸载文件（上下文 / 工具结果）落在 ``<base_dir>/sessions/...`` 下。
    # 读取方（api/chat.py）必须与下方布局严格一致，否则会出现读取路径
    # 不匹配或越界。两种布局：
    #   * per-user 管理器（base_dir == workspaces/{user_id}，即
    #     core/agent/factory.py 在 sandbox_per_user=True 时构造）：
    #       写入  workspaces/{user_id}/sessions/{session_id}
    #       读取  workspaces/{user_id}/sessions/{session_id}
    #   * 全局管理器（base_dir == workspaces，sandbox_per_user=False 默认值）：
    #       写入  workspaces/sessions/{user_id}/{session_id}   （offload 传入 user_id 时）
    #       写入  workspaces/sessions/{session_id}             （未传 user_id 时，向后兼容）
    #       读取  同上对应布局
    #
    # 为彻底避免跨用户泄露，建议部署时启用 sandbox_per_user=True，
    # 或在构造 LocalWorkspaceManager 时始终传入 user_id。
    # 读取路径的 user_id / session_id 也必须使用 core.validators.coerce_id 规范化。
    # ------------------------------------------------------------------

    def _sessions_base(self) -> Path:
        """卸载根目录 ``<base_dir>/sessions``"""
        return Path(self._base_dir) / "sessions"

    def _offload_dir(
        self,
        session_id: str,
        user_id: str | None = None,
    ) -> Path:
        """返回某 session 的卸载目录。

        Args:
            session_id: 会话 ID（会被 coerce_id 规范化）。
            user_id: 可选。提供时表示全局管理器下的按用户隔离布局
                ``<base_dir>/sessions/<user_id>/<session_id>``；
                不提供时（per-user 管理器或向后兼容）为
                ``<base_dir>/sessions/<session_id>``。
        """
        sid = coerce_id(session_id)
        if user_id:
            return self._sessions_base() / coerce_id(user_id) / sid
        return self._sessions_base() / sid

    def _cleanup_offload_dir(self, user_id: str, session_id: str) -> None:
        """删除某 session 的卸载目录。

        同时覆盖三种可能布局（per-user / 全局带 user_id / 全局不带 user_id），
        缺失时不报错（fix #3）。
        """
        sid = coerce_id(session_id)
        uid = coerce_id(user_id)
        candidates = [
            self._sessions_base() / sid,                       # 全局 / 未传 user_id
            self._sessions_base() / uid / sid,                # 全局 / 传 user_id
            Path(self._base_dir) / uid / "sessions" / sid,    # per-user 管理器布局
        ]
        for d in candidates:
            if d.is_dir():
                shutil.rmtree(d, ignore_errors=True)
                logger.info("已删除 offload 目录: {}", d)

    # ------------------------------------------------------------------
    # Offloader 协议实现 — 上下文卸载
    # ------------------------------------------------------------------

    async def offload_context(
        self,
        session_id: str,
        msgs: list,
        user_id: str | None = None,
        **kwargs,
    ) -> str:
        """持久化被压缩的消息到 JSONL 文件（Offloader 协议）

        压缩时由 AgentScope 框架自动调用，将被移除的消息写入文件。
        Agent 后续可通过 Read/Grep 工具回查被卸载的内容。

        Args:
            session_id: 会话 ID
            msgs: 被压缩移除的消息列表
            user_id: 可选。传入时对全局管理器按用户隔离卸载目录
                （见 ``_offload_dir`` 与 Offloader 布局说明）。

        Returns:
            写入的文件路径
        """
        session_dir = self._offload_dir(session_id, user_id=user_id)
        session_dir.mkdir(parents=True, exist_ok=True)

        filepath = session_dir / "context.jsonl"
        with open(filepath, "a", encoding="utf-8") as f:
            for msg in msgs:
                content = getattr(msg, "content", [])
                record = {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "role": getattr(msg, "role", "unknown"),
                    "name": getattr(msg, "name", ""),
                    "content": [
                        block.model_dump() if hasattr(block, "model_dump")
                        else {"type": "text", "text": str(block)}
                        for block in (content if isinstance(content, list) else [content])
                    ],
                }
                f.write(json.dumps(record, ensure_ascii=False) + "\n")

        logger.info("Offloaded {} messages to {}", len(msgs), filepath)
        return str(filepath)

    async def offload_tool_result(
        self,
        session_id: str,
        tool_result,
        user_id: str | None = None,
        **kwargs,
    ) -> str:
        """持久化被截断的工具结果到独立文件（Offloader 协议）

        截断时由 AgentScope 框架自动调用，保留完整工具结果。
        截断标记会包含文件路径提示，Agent 可按需读取完整内容。

        Args:
            session_id: 会话 ID
            tool_result: 被截断的工具结果
            user_id: 可选。传入时对全局管理器按用户隔离卸载目录
                （见 ``_offload_dir`` 与 Offloader 布局说明）。

        Returns:
            写入的文件路径
        """
        session_dir = self._offload_dir(session_id, user_id=user_id)
        session_dir.mkdir(parents=True, exist_ok=True)

        tool_id = getattr(tool_result, "id", "unknown")
        filepath = session_dir / f"tool_result-{tool_id}.txt"
        content = getattr(tool_result, "content", "")
        if not isinstance(content, str):
            content = str(content)
        filepath.write_text(content, encoding="utf-8")

        logger.info("Offloaded tool result {} to {}", tool_id, filepath)
        return str(filepath)
