# -*- coding: utf-8 -*-

from __future__ import annotations

from opentelemetry import trace
from opentelemetry.trace import Span, Tracer


def get_tracer(name: str = "platform-custom") -> Tracer:
    """获取自定义 Tracer 实例"""
    return trace.get_tracer(name)


def create_span(tracer: Tracer, name: str, attributes: dict | None = None):
    """创建 Span 的上下文管理器

    Usage:
        with create_span(tracer, "my_operation", {"key": "value"}) as span:
            ...
    """
    return tracer.start_as_current_span(name, attributes=attributes or {})
