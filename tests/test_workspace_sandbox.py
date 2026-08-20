# -*- coding: utf-8 -*-

"""工作区沙箱路径安全测试

覆盖 core/workspace.py 的 Workspace.resolve_path 与 LocalWorkspaceManager
的 user_id / session_id 净化逻辑。仅依赖文件系统（temp dir / symlink），
不依赖 Redis / Postgres / agentscope 运行时。

注意：LocalWorkspaceManager 内部使用 loguru，缺少 loguru 的环境中无法
import，属预期（与平台其它模块一致）。
"""

import asyncio
import os
import tempfile

import pytest

from core.workspace import LocalWorkspaceManager, Workspace


def test_resolve_path_normal_relative_stays_inside():
    with tempfile.TemporaryDirectory() as d:
        ws = Workspace(workdir=d, user_id="u", session_id="s")
        target = ws.resolve_path("sub/dir/file.txt")
        assert os.path.isabs(target)
        assert target.startswith(os.path.normpath(d) + os.sep)


def test_resolve_path_dotdot_escape_raises():
    with tempfile.TemporaryDirectory() as d:
        ws = Workspace(workdir=d, user_id="u", session_id="s")
        with pytest.raises(ValueError):
            ws.resolve_path("../escape.txt")
        with pytest.raises(ValueError):
            ws.resolve_path("a/../../escape.txt")


def test_resolve_path_absolute_outside_raises():
    with tempfile.TemporaryDirectory() as d:
        ws = Workspace(workdir=d, user_id="u", session_id="s")
        with pytest.raises(ValueError):
            ws.resolve_path("/etc/passwd")
        with pytest.raises(ValueError):
            ws.resolve_path(os.path.abspath("/etc/passwd"))


def test_resolve_path_symlink_escape_does_not_escape():
    """软链接指向沙箱外时，必须不能越界。

    正确（realpath 修复后）行为有两种可能：
      A. resolve_path 直接抛 ValueError；
      B. resolve_path 不抛异常，但解析后的真实路径仍落在 workdir 内。
    两种都视为「不越界」。当前 normpath 实现无法识别软链接越界，
    本测试预期会捕获该缺陷 —— 修复后应通过。
    """
    with tempfile.TemporaryDirectory() as base:
        outside = os.path.join(base, "outside")
        os.makedirs(outside)
        workdir = os.path.join(base, "work")
        os.makedirs(workdir)
        link = os.path.join(workdir, "escape_link")
        os.symlink(outside, link)

        ws = Workspace(workdir=workdir, user_id="u", session_id="s")

        try:
            resolved = ws.resolve_path("escape_link")
        except ValueError:
            # 修复形态 A：显式拒绝
            return
        # 修复形态 B：未抛异常则真实路径必须仍在沙箱内
        real = os.path.realpath(resolved)
        assert real == os.path.normpath(workdir) or real.startswith(
            os.path.normpath(workdir) + os.sep
        ), f"软链接越界: {resolved} -> {real}"


def test_get_workspace_sanitizes_invalid_ids():
    """无效 user_id / session_id 必须被净化为 'anonymous' 且不越界 base_dir。

    正确行为：get_workspace 内部应使用 validators.coerce_id，
    使 user_id='..' / session_id='../evil' 不会把工作目录拼出 base_dir。
    当前实现未净化，预期本测试会捕获该缺陷 —— 修复后应通过。
    """
    with tempfile.TemporaryDirectory() as root:
        base = os.path.join(root, "ws")
        manager = LocalWorkspaceManager(base_dir=base)

        # 无效 user_id（相对穿越）
        ws1 = asyncio.run(manager.get_workspace(user_id="..", session_id="s1"))
        assert ws1.user_id == "anonymous"
        assert ws1.workdir.startswith(os.path.abspath(base))

        # 无效 session_id（相对穿越）
        ws2 = asyncio.run(manager.get_workspace(user_id="u1", session_id="../evil"))
        assert ws2.session_id == "anonymous"
        assert ws2.workdir.startswith(os.path.abspath(base))

        # 合法 id 保持不变
        ws3 = asyncio.run(manager.get_workspace(user_id="user_1", session_id="sess_2"))
        assert ws3.user_id == "user_1"
        assert ws3.session_id == "sess_2"
