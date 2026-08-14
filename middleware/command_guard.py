# -*- coding: utf-8 -*-

"""命令内容安全守卫

解析 tool_call.input JSON 中的实际命令，按黑白名单模式匹配。
与 tool_guard.py 并列:
  - tool_guard  → 工具名级 (Bash, Read, Write ...)
  - command_guard → 命令内容级 (rm -rf, curl|bash ...)

配置 (configs/dev.yaml):
    agent:
      command_guard:
        enabled: true
        mode: "blocklist"        # allowlist | blocklist
        rules:
          - "rm -rf /"           # 精确匹配
          - "rm -rf *"           # 通配符匹配
          - "curl *|*bash*"      # 管道执行
          - "*/dev/tcp/*"        # Bash 反弹 shell
          - "Invoke-Expression*" # PowerShell 远程执行
          - "certutil*"          # Windows 下载
"""

from __future__ import annotations

import fnmatch
import json
import logging
from typing import Any

from agentscope.middleware import MiddlewareBase
from agentscope.message import TextBlock, ToolResultState
from agentscope.tool import ToolResponse

logger = logging.getLogger(__name__)


class CommandGuardMiddleware(MiddlewareBase):
    """命令内容安全守卫 — 按黑白名单模式匹配命令内容"""

    def __init__(
        self,
        enabled: bool = True,
        mode: str = "blocklist",
        rules: list[str] | None = None,
    ) -> None:
        super().__init__()
        self._enabled = enabled
        self._mode = mode  # "allowlist" | "blocklist"
        self._rules = rules or []

    # ------------------------------------------------------------------
    # MiddlewareBase 钩子
    # ------------------------------------------------------------------

    async def on_acting(
        self,
        agent: Any,
        input_kwargs: dict,
        next_handler: Any,
    ):
        """拦截工具执行 — 检查命令内容是否命中名单规则"""
        if not self._enabled:
            async for item in next_handler(**input_kwargs):
                yield item
            return

        tool_call = input_kwargs["tool_call"]
        command = self._extract_command(tool_call.input)

        if not self._is_blocked(command):
            async for item in next_handler(**input_kwargs):
                yield item
            return

        logger.info(
            "CommandGuard [%s] 拦截 %s: %s",
            self._mode, tool_call.name, command[:200],
        )
        yield ToolResponse(
            id=tool_call.id,
            content=[TextBlock(
                text=f"[CommandGuard] 命令被拦截 ({self._mode}): {command[:200]}",
            )],
            state=ToolResultState.DENIED,
        )

    # ------------------------------------------------------------------
    # 命令提取
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_command(tool_input: str) -> str:
        """从 tool_call.input JSON 中提取实际命令文本

        支持的 input 格式:
          - Bash/PowerShell: {"command": "ls -la"}
          - Write: {"path": "/tmp/x", "content": "..."}
          - Read: {"file_path": "/tmp/x"}
          - MCP: 各种格式, 取 command/content/path 字段
          - 非 JSON: 原样返回
        """
        if not tool_input:
            return ""

        try:
            data = json.loads(tool_input)
        except (json.JSONDecodeError, TypeError):
            return tool_input

        if not isinstance(data, dict):
            return tool_input

        for key in ("command", "content", "path", "file_path"):
            if key in data and isinstance(data[key], str):
                return data[key]

        return tool_input

    # ------------------------------------------------------------------
    # 规则匹配
    # ------------------------------------------------------------------

    def _is_blocked(self, command: str) -> bool:
        """判断命令内容是否被拦截

        Args:
            command: 提取后的命令文本

        Returns:
            True 表示应被拦截
        """
        if not command:
            # 空命令: allowlist 模式下拦截, blocklist 模式下放行
            return self._mode == "allowlist"

        matched = any(
            fnmatch.fnmatch(command, pattern)
            for pattern in self._rules
        )

        if self._mode == "blocklist":
            return matched  # 命中黑名单 → 拦截
        else:
            return not matched  # 未命中白名单 → 拦截
