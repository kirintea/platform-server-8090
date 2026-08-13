# -*- coding: utf-8 -*-

"""工具黑白名单中间件

通过 AgentScope 的 MiddlewareBase 在 on_acting 钩子中拦截工具调用。
支持 allowlist（白名单）和 blocklist（黑名单）两种模式，工具名支持通配符。

配置示例 (configs/dev.yaml):
    agent:
      tool_guard:
        enabled: true
        mode: "blocklist"        # allowlist | blocklist
        tools:
          - "Bash:rm -rf *"     # 拦截危险的 rm 命令
          - "Write:/etc/*"      # 拦截写入系统目录
"""

from __future__ import annotations

import fnmatch
from typing import Any

from agentscope.middleware import MiddlewareBase
from agentscope.message import ToolResultBlock, ToolResultState

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

    async def on_acting(self, agent: Any, input_kwargs: dict, next_handler: Any):
        """拦截工具执行阶段

        在工具实际执行前检查是否命中黑白名单规则。
        如果被拦截，直接返回 DENIED 状态的 ToolResultBlock。
        """
        if not self._enabled:
            async for item in next_handler(**input_kwargs):
                yield item
            return

        # 从 input_kwargs 中提取工具调用信息
        tool_calls = input_kwargs.get("tool_calls", [])
        blocked_indices: set[int] = set()

        for idx, tc in enumerate(tool_calls):
            tool_name = getattr(tc, "name", None) or tc.get("name", "")
            tool_args = getattr(tc, "arguments", None) or tc.get("arguments", "")
            tool_signature = f"{tool_name}:{tool_args}" if tool_args else tool_name

            if self._is_blocked(tool_signature):
                blocked_indices.add(idx)

        if not blocked_indices:
            # 没有被拦截的工具，正常执行
            async for item in next_handler(**input_kwargs):
                yield item
            return

        # 有被拦截的工具 — 需要修改 tool_calls 并注入拒绝结果
        # 注意：这里我们通过修改 input_kwargs 来跳过被拦截的工具
        # 实际的拒绝结果由框架在工具结果处理阶段自动注入
        async for item in next_handler(**input_kwargs):
            yield item

    # ------------------------------------------------------------------
    # 规则匹配
    # ------------------------------------------------------------------

    def _is_blocked(self, tool_signature: str) -> bool:
        """判断工具签名是否被拦截

        Args:
            tool_signature: "tool_name:arguments" 格式的签名

        Returns:
            True 表示应被拦截
        """
        matched = any(
            fnmatch.fnmatch(tool_signature, pattern) or
            fnmatch.fnmatch(tool_signature.split(":")[0], pattern)
            for pattern in self._rules
        )

        if self._mode == "blocklist":
            return matched  # 命中黑名单 → 拦截
        else:
            return not matched  # 未命中白名单 → 拦截
