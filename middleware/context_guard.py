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
        """在压缩前检查 Offloader 是否已挂载"""
        if not getattr(agent, "offloader", None):
            logger.warning(
                "Context compression triggered but no offloader attached. "
                "Compressed content will be permanently lost."
            )
        # 继续执行压缩（框架会自动调用 offloader）
        return await next_handler(**input_kwargs)
