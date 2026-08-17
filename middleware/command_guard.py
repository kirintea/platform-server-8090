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
import re
from typing import Any

from agentscope.middleware import MiddlewareBase
from agentscope.message import TextBlock, ToolResultState
from agentscope.tool import ToolResponse

logger = logging.getLogger(__name__)


_HIGH_RISK_PATTERNS = tuple(
    re.compile(pattern)
    for pattern in (
        r"^rm\s+-rf\s+/",
        r"^rm\s+-rf\s+\.",
        r"^rm\s+-rf\s+\*",
        r"^rm\s+-rf\s+~",
        r"git\s+clean\s+-fdx",
        r"(?:curl|wget).+\|\s*(?:bash|sh)",
        r"^sudo(?:\s|$)",
        r"^chmod\s+-R\s+",
        r"^chown\s+",
        r"^dd\s+if=/dev/zero",
        r"^mkfs(?:\s|$)",
        r"^fdisk(?:\s|$)",
        r"^eval\b",
        r"^(?:python|python3)\s+-c\b",
        r"^node\s+-e\b",
        r"^ruby\s+-e\b",
        r"^perl\s+-e\b",
        r"^shutdown(?:\s|$)",
        r"^reboot(?:\s|$)",
        r"^kill\s+-9\s+1\b",
        r"^pkill\s+-9\b",
        r"^systemctl\s+(?:stop|disable|mask)\b",
    )
)
_SAFE_REDIRECT_RE = re.compile(r"\d*>&\d+")
_SHELL_META_CHARS = (";", "&&", "||", "|", "$(", "`", ">", "<", "\n")
_LOW_RISK_PREFIXES = (
    "ls", "cat", "grep", "find", "sed", "pwd", "echo", "head", "tail", "wc",
    "mkdir", "touch", "cd", "npm test", "npm run build", "npm run lint",
    "npm run dev", "npm run start", "cargo check", "cargo build", "cargo test",
    "cargo clippy", "cargo fmt", "git status", "git log", "git diff", "git show",
    "git branch", "python --version", "node --version", "rustc --version",
)
_MEDIUM_RISK_PREFIXES = (
    "npm install", "npm ci", "npm uninstall", "git push", "git pull", "git merge",
    "git rebase", "docker", "pip install", "pip uninstall", "cargo install",
    "yarn add", "yarn remove", "pnpm install", "pnpm add",
)
_DANGEROUS_FLAGS = (
    "-exec", "-delete", "-ok", "-execdir", "-okdir", "-fprint", "-fprint0",
    "-fprintf", "-fls", "-i",
)
_UNICODE_META_TRANSLATION = str.maketrans({
    "＆": "&", "｜": "|", "﹔": ";", "；": ";", "＞": ">", "＜": "<",
    "＄": "$", "｀": "`", "（": "(", "）": ")", "｛": "{", "｝": "}",
    "！": "!",
})


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

        # Keep the configured glob rules, then apply the stronger command
        # inspection borrowed from KSI_RUST's policy engine.
        matched = matched or self._matches_high_risk(command)

        if self._mode == "blocklist":
            return matched  # 命中黑名单 → 拦截
        else:
            return not matched  # 未命中白名单 → 拦截

    @classmethod
    def _matches_high_risk(cls, command: str) -> bool:
        """Detect high-risk commands and shell injection constructs.

        AgentScope has no confirmation callback at this middleware boundary,
        so KSI_RUST's ``RequiresConfirmation`` outcomes are conservatively
        represented as blocked commands here.
        """
        normalized = command.strip().translate(_UNICODE_META_TRANSLATION)
        if any(pattern.search(normalized) for pattern in _HIGH_RISK_PATTERNS):
            return True

        parts = cls._split_compound_command(normalized)
        if parts is not None:
            if cls._all_low_risk(parts) and not any(
                cls._has_dangerous_meta(part) or cls._has_dangerous_flag(part)
                for part in parts
            ):
                return False
            return True

        if cls._is_low_risk(normalized):
            return cls._has_dangerous_flag(normalized) or cls._has_dangerous_meta(normalized)

        # Medium-risk and unknown commands are left to the configured glob
        # policy; this preserves the existing blocklist contract.
        return False

    @staticmethod
    def _split_compound_command(command: str) -> list[str] | None:
        if not any(separator in command for separator in ("&&", "||", ";", "\n")):
            return None
        parts = [command]
        for separator in ("&&", "||", ";", "\n"):
            parts = [piece for part in parts for piece in part.split(separator)]
        parts = [part.strip() for part in parts if part.strip()]
        return parts if len(parts) > 1 else None

    @staticmethod
    def _is_low_risk(command: str) -> bool:
        return any(command == prefix or command.startswith(f"{prefix} ") for prefix in _LOW_RISK_PREFIXES)

    @classmethod
    def _all_low_risk(cls, commands: list[str]) -> bool:
        return all(cls._is_low_risk(command) for command in commands)

    @staticmethod
    def _has_dangerous_flag(command: str) -> bool:
        return any(flag in command for flag in _DANGEROUS_FLAGS)

    @staticmethod
    def _has_dangerous_meta(command: str) -> bool:
        sanitized = _SAFE_REDIRECT_RE.sub("", command)
        normalized = sanitized.translate(_UNICODE_META_TRANSLATION)
        return any(marker in sanitized or marker in normalized for marker in _SHELL_META_CHARS)
