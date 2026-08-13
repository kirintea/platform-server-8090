# -*- coding: utf-8 -*-

from __future__ import annotations

import typing

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from core.config.schemas import OTelConfig


class TracingSetup:
    """OTel 追踪初始化 — 供 AgentScope TracingMiddleware 使用

    必须在 Agent 创建之前调用，之后 TracingMiddleware 自动生效。
    """

    @staticmethod
    def init(config: OTelConfig) -> TracerProvider:
        """初始化 OpenTelemetry SDK

        Args:
            config: OTel 配置

        Returns:
            配置好的 TracerProvider
        """
        resource = Resource.create({
            "service.name": config.service_name,
            "service.version": config.service_version,
            "deployment.environment": config.environment,
        })

        exporter = OTLPSpanExporter(
            endpoint=config.endpoint,
            headers=config.headers or None,
        )

        provider = TracerProvider(resource=resource)
        provider.add_span_processor(BatchSpanProcessor(exporter))

        trace.set_tracer_provider(provider)
        return provider

    @staticmethod
    def shutdown() -> None:
        """关闭追踪，刷新剩余数据"""
        provider = trace.get_tracer_provider()
        if hasattr(provider, "shutdown"):
            typing.cast(TracerProvider, provider).shutdown()
            # provider.shutdown()
