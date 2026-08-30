# -*- coding: utf-8 -*-

"""工具黑白名单中间件

通过 AgentScope 的 MiddlewareBase 在 on_acting 钩子中拦截工具调用。
支持 allowlist（白名单）和 blocklist（黑名单）两种模式，工具名支持通配符。

仅做工具名级匹配，不解析命令内容。
命令内容的安全检测由 command_guard.py 负责。

配置示例 (configs/dev.yaml):
    agent:
      tool_guard:
        enabled: true
        mode: "allowlist"        # allowlist | blocklist
        tools:
          - "Read"               # 按工具名匹配
          - "Glob"
          - "Grep"
"""

from __future__ import annotations

import fnmatch
from typing import Any

from loguru import logger

from agentscope.middleware import MiddlewareBase
from agentscope.message import TextBlock, ToolResultState
from agentscope.tool import ToolResponse

from core.config.schemas import ToolGuardConfig


class ToolGuardMiddleware(MiddlewareBase):
    """工具调用守卫 — 根据黑白名单决定是否允许工具执行"""

    def __init__(self, config: ToolGuardConfig) -> None:
        super().__init__()
        self._enabled = config.enabled
        self._mode = config.mode  # "allowlist" | "blocklist"
        self._rules = config.tools

    # ------------------------------------------------------------------
    # MiddlewareBase 钩子
    # ------------------------------------------------------------------

    async def on_acting(
        self,
        agent: Any,
        input_kwargs: dict,
        next_handler: Any,
    ):
        """拦截工具执行阶段

        on_acting 是逐个工具调用的洋葱钩子，input_kwargs 中包含:
          - tool_call: 单个 ToolCallBlock（.name, .input）

        如果被拦截，直接 yield 一个 DENIED 状态的 ToolResponse，
        不调用 next_handler，从而阻止工具实际执行。
        """
        if not self._enabled:
            async for item in next_handler(**input_kwargs):
                yield item
            return

        tool_call = input_kwargs["tool_call"]
        tool_name: str = tool_call.name

        if not self._is_blocked(tool_name):
            async for item in next_handler(**input_kwargs):
                yield item
            return

        logger.info(
            "ToolGuard [%s] 拦截工具: %s", self._mode, tool_name,
        )
        yield ToolResponse(
            id=tool_call.id,
            content=[TextBlock(
                text=f"[ToolGuard] 工具被拦截 ({self._mode}): {tool_name}",
            )],
            state=ToolResultState.DENIED,
        )

    # ------------------------------------------------------------------
    # 规则匹配
    # ------------------------------------------------------------------

    def _is_blocked(self, tool_name: str) -> bool:
        """判断工具名是否被拦截

        Args:
            tool_name: 工具名 (如 "Bash", "Read")

        Returns:
            True 表示应被拦截
        """
        matched = any(
            fnmatch.fnmatch(tool_name, pattern)
            for pattern in self._rules
        )

        if self._mode == "blocklist":
            return matched  # 命中黑名单 → 拦截
        else:
            return not matched  # 未命中白名单 → 拦截
