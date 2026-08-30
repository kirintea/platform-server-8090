# -*- coding: utf-8 -*-

"""RAG VectorStoreBase 空壳与配置测试

验证 RAG 配置和 ThirdPartyVectorStore 空壳可用。
运行方式：
    .venv/Scripts/python.exe -m pytest tests/test_rag_config.py -v
"""

from __future__ import annotations

import pytest


class TestRAGConfig:
    """验证 RAGConfig schema"""

    def test_rag_config_defaults(self):
        from core.config.schemas import RAGConfig

        cfg = RAGConfig()
        assert cfg.enabled is False
        assert cfg.backend == "third_party"
        assert cfg.api_url == ""
        assert cfg.top_k == 5
        assert cfg.mode == "agentic"

    def test_rag_config_custom(self):
        from core.config.schemas import RAGConfig

        cfg = RAGConfig(
            enabled=True,
            api_url="http://localhost:8000/api/v1",
            api_key="test-key",
            collection="my-kb",
            top_k=10,
            mode="static",
        )
        assert cfg.enabled is True
        assert cfg.api_url == "http://localhost:8000/api/v1"
        assert cfg.collection == "my-kb"
        assert cfg.mode == "static"

    def test_app_config_has_rag_field(self):
        from core.config.schemas import AppConfig, OTelConfig, LLMConfig

        config = AppConfig(
            otel=OTelConfig(enabled=False, endpoint="http://localhost:4317", environment="test"),
            llm=LLMConfig(api_key="test", base_url="http://localhost:8090/v1", model="test"),
            rag={"enabled": True, "api_url": "http://rag:8000"},
        )
        assert config.rag.enabled is True
        assert config.rag.api_url == "http://rag:8000"


class TestThirdPartyVectorStore:
    """验证 ThirdPartyVectorStore 空壳"""

    def test_importable(self):
        from core.rag import ThirdPartyVectorStore
        assert ThirdPartyVectorStore is not None

    def test_inherits_vector_store_base(self):
        from agentscope.rag import VectorStoreBase
        from core.rag import ThirdPartyVectorStore
        assert issubclass(ThirdPartyVectorStore, VectorStoreBase)

    def test_construction(self):
        from core.rag import ThirdPartyVectorStore

        store = ThirdPartyVectorStore(
            api_url="http://localhost:8000",
            api_key="test-key",
        )
        assert store._api_url == "http://localhost:8000"
        assert store._api_key == "test-key"

    @pytest.mark.asyncio
    async def test_context_manager_no_url(self):
        """无 api_url 时进入 context manager 不报错"""
        from core.rag import ThirdPartyVectorStore

        store = ThirdPartyVectorStore()
        async with store:
            pass  # 不应抛出异常

    @pytest.mark.asyncio
    async def test_all_methods_raise_not_implemented(self):
        """所有核心方法在未实现时抛出 NotImplementedError"""
        from core.rag import ThirdPartyVectorStore

        store = ThirdPartyVectorStore(api_url="http://fake")

        with pytest.raises(NotImplementedError):
            await store.create_collection("test", 1536)

        with pytest.raises(NotImplementedError):
            await store.delete_collection("test")

        with pytest.raises(NotImplementedError):
            await store.has_collection("test")

        with pytest.raises(NotImplementedError):
            await store.insert("test", [])

        with pytest.raises(NotImplementedError):
            await store.delete("test", "doc-1")

        with pytest.raises(NotImplementedError):
            await store.search("test", [0.1, 0.2])

        with pytest.raises(NotImplementedError):
            await store.list_documents("test")
