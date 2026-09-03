# -*- coding: utf-8 -*-

"""OTel 追踪上下文中间件 — 在活跃 span 上追加业务属性

在 AgentScope TracingMiddleware 创建的 span 上追加 user_id、device_id
等业务属性，使 Jaeger 支持多维度搜索。

使用方式：
    agent.__tracing_context__ = {
        "agentscope.user.id": "user_001",
        "agentscope.device.id": "mobile",
    }

必须排在 TracingMiddleware 之后（中间件列表中更靠后），使其代码
在 TracingMiddleware 的 span 内执行。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, AsyncGenerator, Callable

from opentelemetry import trace as otel_trace

from agentscope.middleware import MiddlewareBase

if TYPE_CHECKING:
    from agentscope.agent import Agent


class TracingContextMiddleware(MiddlewareBase):
    """在活跃 OTel span 上追加业务属性。

    从 agent.__tracing_context__ 读取键值对，设置到当前活跃 span 上。
    属性在 next_handler 调用前设置，确保 span 关闭前已写入。
    """

    _CONTEXT_KEY = "__tracing_context__"

    def _apply_context(self, agent: "Agent") -> None:
        """从 agent 读取 tracing context 并设置到当前活跃 span"""
        ctx = getattr(agent, self._CONTEXT_KEY, None)
        if not ctx or not isinstance(ctx, dict):
            return

        span = otel_trace.get_current_span()
        if not span.is_recording():
            return

        for key, value in ctx.items():
            if value is not None:
                span.set_attribute(key, str(value))

    async def on_reply(
        self,
        agent: "Agent",
        input_kwargs: dict,
        next_handler: Callable[..., AsyncGenerator],
    ) -> AsyncGenerator:
        self._apply_context(agent)
        async for item in next_handler(**input_kwargs):
            yield item

    async def on_model_call(
        self,
        agent: "Agent",
        input_kwargs: dict,
        next_handler: Callable,
    ):
        self._apply_context(agent)
        return await next_handler(**input_kwargs)

    async def on_acting(
        self,
        agent: "Agent",
        input_kwargs: dict,
        next_handler: Callable[..., AsyncGenerator],
    ) -> AsyncGenerator:
        self._apply_context(agent)
        async for item in next_handler(**input_kwargs):
            yield item
