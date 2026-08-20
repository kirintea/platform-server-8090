# -*- coding: utf-8 -*-

"""压缩守卫中间件 — 确保内容先卸载再丢弃

利用 AgentScope 的中间件洋葱模型，在压缩发生前拦截，
检查 Offloader 是否已挂载，防止内容永久丢失。
"""

from __future__ import annotations

from agentscope.middleware import MiddlewareBase

from loguru import logger


class ContextGuardMiddleware(MiddlewareBase):
    """压缩守卫：确保内容先卸载再丢弃"""

    async def on_compress_context(self, agent, input_kwargs, next_handler):
        """在压缩前检查 Offloader 是否已挂载

        若未挂载 offloader，则拒绝执行压缩，防止上下文被永久丢弃造成
        数据丢失。这是强制执行（enforcing），而非仅作告警。
        """
        if not getattr(agent, "offloader", None):
            logger.warning(
                "Context compression triggered but no offloader attached. "
                "Refusing to compress: compressed content would be "
                "permanently lost."
            )
            raise RuntimeError(
                "ContextGuard: 拒绝上下文压缩 — Agent 未挂载 Offloader。"
                "在执行上下文压缩前必须先挂载 Offloader，否则被压缩丢弃的"
                "内容将无法恢复。请在 Agent 配置中启用 Offloader，或关闭该"
                "Agent 的上下文压缩。"
            )
        # 已挂载 offloader，继续压缩流程（框架会自动调用 offloader 卸载内容）
        return await next_handler(**input_kwargs)
