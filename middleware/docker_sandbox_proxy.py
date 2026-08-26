# -*- coding: utf-8 -*-

"""Docker 沙箱代理中间件 — 将工具执行转发到沙箱容器

当 sandbox.backend="docker" 时启用。拦截文件操作和命令执行类工具，
通过 Docker SDK 在沙箱容器中执行，而非在 app 容器/本机执行。

与 path_guard / command_guard / tool_guard 并列:
  - tool_guard          → 工具名级 (Bash, Read, Write ...)
  - command_guard       → 命令内容级 (rm -rf, curl|bash ...)
  - path_guard          → 路径级 (限制文件操作在沙箱目录内)
  - docker_sandbox_proxy → 执行环境级 (工具在沙箱容器内执行)

配置 (configs/dev.yaml):
    sandbox:
      backend: "docker"        # "local" | "docker"
      container: "platform-sandbox"
      project_root: "/workspace"
      extra_mounts:
        - host: "skills"
          container: "/workspace/skills"
          readonly: true
"""

from __future__ import annotations

import json
import logging
import os
import shlex
import textwrap
from typing import Any

import docker

from agentscope.middleware import MiddlewareBase
from agentscope.message import TextBlock, ToolResultState
from agentscope.tool import ToolResponse

from core.config.schemas import SandboxConfig

logger = logging.getLogger(__name__)

# 需要拦截转发到沙箱的工具
_INTERCEPT_TOOLS = frozenset({
    "Bash", "PowerShell",
    "Read", "Write", "Edit",
    "Glob", "Grep",
    "TaskCreate", "TaskList", "TaskGet", "TaskUpdate",
})

# 工具名 → 文件路径参数名
_TOOL_PATH_KEYS: dict[str, str] = {
    "Read": "file_path",
    "Write": "file_path",
    "Edit": "file_path",
    "Glob": "path",
    "Grep": "path",
}

# 工具名 → agentscope 模块路径
_TOOL_IMPORT_MAP: dict[str, str] = {
    "Read": "agentscope.tool._built_in.Read",
    "Write": "agentscope.tool._built_in.Write",
    "Edit": "agentscope.tool._built_in.Edit",
    "Glob": "agentscope.tool._built_in.Glob",
    "Grep": "agentscope.tool._built_in.Grep",
    "TaskCreate": "agentscope.tool._built_in.TaskCreate",
    "TaskList": "agentscope.tool._built_in.TaskList",
    "TaskGet": "agentscope.tool._built_in.TaskGet",
    "TaskUpdate": "agentscope.tool._built_in.TaskUpdate",
}


class DockerSandboxProxy(MiddlewareBase):
    """Docker 沙箱代理 — 将工具执行转发到沙箱容器"""

    def __init__(
        self,
        sandbox_config: SandboxConfig,
        host_project_root: str,
    ) -> None:
        super().__init__()
        self._container_name = sandbox_config.container
        self._container_project_root = sandbox_config.project_root
        self._host_project_root = host_project_root

        # 通过 Docker Socket 连接 Docker daemon
        self._docker_client: docker.DockerClient | None = None
        self._container: docker.models.containers.Container | None = None
        try:
            self._docker_client = docker.from_env()
            self._container = self._docker_client.containers.get(
                self._container_name,
            )
            logger.info(
                "DockerSandboxProxy 已初始化: container=%s (status=%s), "
                "host_root=%s, container_root=%s",
                self._container_name,
                self._container.status,
                self._host_project_root,
                self._container_project_root,
            )
        except docker.errors.NotFound:
            logger.warning(
                "DockerSandboxProxy: 沙箱容器 '%s' 不存在，"
                "沙箱执行将降级到本地。请先启动沙箱容器: "
                "docker compose -f docker-compose.sandbox.yml up -d sandbox",
                self._container_name,
            )
        except docker.errors.DockerException as e:
            logger.warning(
                "DockerSandboxProxy: 无法连接 Docker daemon: %s。"
                "沙箱执行将降级到本地。",
                e,
            )

    # ------------------------------------------------------------------
    # 系统提示词注入
    # ------------------------------------------------------------------

    async def on_system_prompt(
        self,
        agent: Any,
        current_prompt: str,
    ) -> str:
        hint = (
            f"\n\n## 沙箱执行环境\n"
            f"你的文件操作和命令在隔离的沙箱容器 `{self._container_name}` 中执行。\n"
            f"容器项目根目录: `{self._container_project_root}`\n"
            f"请使用容器内的路径进行操作。"
        )
        return current_prompt + hint

    # ------------------------------------------------------------------
    # MiddlewareBase 钩子
    # ------------------------------------------------------------------

    async def on_acting(
        self,
        agent: Any,
        input_kwargs: dict,
        next_handler: Any,
    ):
        """拦截工具执行 — 在沙箱容器中执行"""
        tool_call = input_kwargs["tool_call"]
        tool_name = tool_call.name

        if tool_name not in _INTERCEPT_TOOLS:
            async for item in next_handler(**input_kwargs):
                yield item
            return

        # 沙箱容器不可用时降级到本地执行
        if self._container is None:
            logger.warning("沙箱容器不可用，降级到本地执行: %s", tool_name)
            async for item in next_handler(**input_kwargs):
                yield item
            return

        try:
            response = self._exec_in_sandbox(tool_name, tool_call)
            yield response
        except Exception as e:
            logger.error("沙箱执行失败，降级到本地执行: %s", e)
            async for item in next_handler(**input_kwargs):
                yield item

    # ------------------------------------------------------------------
    # 沙箱执行
    # ------------------------------------------------------------------

    def _exec_in_sandbox(self, tool_name: str, tool_call: Any) -> ToolResponse:
        """在沙箱容器中执行工具"""
        if tool_name in ("Bash", "PowerShell"):
            return self._exec_bash(tool_name, tool_call)
        return self._exec_python_tool(tool_name, tool_call)

    def _exec_bash(self, tool_name: str, tool_call: Any) -> ToolResponse:
        """在沙箱容器中执行 Bash 命令"""
        command = self._extract_field(tool_call.input, "command")
        if not command:
            return ToolResponse(
                id=tool_call.id,
                content=[TextBlock(text="[Sandbox] 命令为空")],
                state=ToolResultState.DENIED,
            )

        # 翻译命令中的绝对路径
        command = self._translate_command_paths(command)

        logger.debug("Sandbox Bash: %s", command[:200])
        exit_code, output = self._container.exec_run(  # type: ignore
            ["bash", "-c", command],
            workdir=self._container_project_root,
            demux=True,
        )

        stdout = (output[0] or b"").decode("utf-8", errors="replace")
        stderr = (output[1] or b"").decode("utf-8", errors="replace")

        result_text = stdout
        if exit_code != 0:
            result_text += f"\n[stderr]\n{stderr}"

        return ToolResponse(
            id=tool_call.id,
            content=[TextBlock(text=result_text or "(无输出)")],
            state=ToolResultState.OK,
        )

    def _exec_python_tool(self, tool_name: str, tool_call: Any) -> ToolResponse:
        """在沙箱容器中执行 Python 工具"""
        path_key = _TOOL_PATH_KEYS.get(tool_name)
        import_path = _TOOL_IMPORT_MAP.get(tool_name)

        if not import_path:
            return ToolResponse(
                id=tool_call.id,
                content=[TextBlock(text=f"[Sandbox] 不支持的工具: {tool_name}")],
                state=ToolResultState.DENIED,
            )

        # 翻译文件路径
        input_str = tool_call.input
        if path_key:
            path_val = self._extract_field(input_str, path_key)
            if path_val:
                translated = self._translate_path(path_val)
                try:
                    data = json.loads(input_str) if isinstance(input_str, str) else input_str
                    data[path_key] = translated
                    input_str = json.dumps(data, ensure_ascii=False)
                except (json.JSONDecodeError, TypeError):
                    pass

        # 构建 Python 执行脚本
        module_path, class_name = import_path.rsplit(".", 1)
        script = textwrap.dedent(f"""\
            import json, sys
            sys.path.insert(0, '{self._container_project_root}')
            from {module_path} import {class_name}
            tool = {class_name}()
            result = tool(**json.loads({json.dumps(input_str)}))
            print(json.dumps(result, ensure_ascii=False, default=str))
        """)

        logger.debug("Sandbox %s: input=%s", tool_name, input_str[:200])
        exit_code, output = self._container.exec_run(  # type: ignore
            ["python3", "-c", script],
            workdir=self._container_project_root,
            demux=True,
        )

        stdout = (output[0] or b"").decode("utf-8", errors="replace")
        stderr = (output[1] or b"").decode("utf-8", errors="replace")

        if exit_code == 0:
            return ToolResponse(
                id=tool_call.id,
                content=[TextBlock(text=stdout.strip() or "OK")],
                state=ToolResultState.OK,
            )

        logger.warning("Sandbox %s 失败: %s", tool_name, stderr[:500])
        return ToolResponse(
            id=tool_call.id,
            content=[TextBlock(text=stderr.strip() or "执行失败")],
            state=ToolResultState.ERROR,
        )

    # ------------------------------------------------------------------
    # 路径翻译
    # ------------------------------------------------------------------

    def _translate_path(self, path_str: str) -> str:
        """将宿主机路径翻译为容器路径"""
        if not path_str:
            return path_str

        abs_path = os.path.abspath(path_str)
        if abs_path.startswith(self._host_project_root):
            rel = os.path.relpath(abs_path, self._host_project_root)
            return os.path.join(self._container_project_root, rel).replace("\\", "/")

        return path_str

    def _translate_command_paths(self, command: str) -> str:
        """翻译 Bash 命令中的绝对路径"""
        try:
            tokens = shlex.split(command)
        except ValueError:
            tokens = command.split()

        translated = []
        for tok in tokens:
            if os.path.isabs(tok) and tok.startswith(self._host_project_root):
                translated.append(self._translate_path(tok))
            else:
                translated.append(tok)

        return " ".join(shlex.quote(t) for t in translated)

    # ------------------------------------------------------------------
    # 工具方法
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_field(tool_input: Any, field: str) -> str | None:
        """从 tool_call.input 中提取指定字段"""
        if not tool_input:
            return None
        try:
            if isinstance(tool_input, str):
                data = json.loads(tool_input)
            else:
                data = tool_input
            if isinstance(data, dict):
                val = data.get(field)
                return str(val) if val is not None else None
        except (json.JSONDecodeError, TypeError):
            pass
        return None
