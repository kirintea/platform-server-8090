# -*- coding: utf-8 -*-

"""第三方向量存储空壳 — 继承 VectorStoreBase，待具体 RAG 服务确定后填充实现

使用方式：
    RAG 服务确定后，只需实现以下 7 个方法 + __aenter__/__aexit__：
    - create_collection(name, dimensions)
    - delete_collection(name)
    - has_collection(name)
    - insert(collection, records)
    - delete(collection, document_id)
    - search(collection, query_vector, top_k, metadata_filter)
    - list_documents(collection, metadata_filter)

其余 Parser / Chunker / Embedding / RAGMiddleware 全部复用 AgentScope 官方实现。
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from agentscope.rag import (
    DocumentSummary,
    VectorRecord,
    VectorSearchResult,
    VectorStoreBase,
)


class ThirdPartyVectorStore(VectorStoreBase):
    """第三方向量存储空壳

    所有方法抛出 NotImplementedError，等待具体 RAG 服务确定后填充。

    对接第三方 RAG API 时，典型实现思路：
    - __aenter__: 创建 HTTP 客户端连接
    - create_collection: 调用第三方 API 创建 collection/index
    - insert: 将 VectorRecord 转换为第三方格式并批量写入
    - search: 将 query_vector 发送给第三方 API，返回结果转换为 VectorSearchResult
    - __aexit__: 关闭 HTTP 客户端
    """

    def __init__(
        self,
        api_url: str = "",
        api_key: str = "",
        timeout: int = 30,
        **kwargs: Any,
    ) -> None:
        """初始化第三方向量存储

        Args:
            api_url: 第三方 RAG 服务 API 地址
            api_key: API 密钥
            timeout: 请求超时（秒）
            **kwargs: 扩展参数（传递给具体实现）
        """
        self._api_url = api_url
        self._api_key = api_key
        self._timeout = timeout
        self._kwargs = kwargs
        self._client = None

    async def __aenter__(self) -> ThirdPartyVectorStore:
        """建立连接（待实现）"""
        if not self._api_url:
            logger.warning(
                "ThirdPartyVectorStore: api_url 未配置，"
                "所有 RAG 操作将抛出 NotImplementedError"
            )
            return self

        # TODO: 第三方 RAG 服务确定后，此处创建 HTTP 客户端
        # 示例：
        # self._client = httpx.AsyncClient(
        #     base_url=self._api_url,
        #     headers={"Authorization": f"Bearer {self._api_key}"},
        #     timeout=self._timeout,
        # )
        logger.info("ThirdPartyVectorStore: 连接到 {}", self._api_url)
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        """关闭连接（待实现）"""
        if self._client is not None:
            # TODO: 关闭客户端
            # await self._client.aclose()
            self._client = None

    async def create_collection(self, name: str, dimensions: int) -> None:
        """创建 collection/index（待实现）

        Args:
            name: collection 名称
            dimensions: 向量维度
        """
        raise NotImplementedError(
            f"ThirdPartyVectorStore.create_collection 尚未实现。"
            f"请对接具体 RAG 服务后填充此方法。"
            f"(name={name}, dimensions={dimensions})"
        )

    async def delete_collection(self, name: str) -> None:
        """删除 collection（待实现）

        Args:
            name: collection 名称
        """
        raise NotImplementedError(
            f"ThirdPartyVectorStore.delete_collection 尚未实现。"
            f"(name={name})"
        )

    async def has_collection(self, name: str) -> bool:
        """检查 collection 是否存在（待实现）

        Args:
            name: collection 名称
        """
        raise NotImplementedError(
            f"ThirdPartyVectorStore.has_collection 尚未实现。"
            f"(name={name})"
        )

    async def insert(
        self,
        collection: str,
        records: list[VectorRecord],
    ) -> None:
        """批量写入向量记录（待实现）

        Args:
            collection: collection 名称
            records: 向量记录列表，每条包含 vector, document_id, chunk
        """
        raise NotImplementedError(
            f"ThirdPartyVectorStore.insert 尚未实现。"
            f"(collection={collection}, records={len(records)})"
        )

    async def delete(self, collection: str, document_id: str) -> None:
        """按 document_id 删除文档所有记录（待实现）

        Args:
            collection: collection 名称
            document_id: 文档 ID
        """
        raise NotImplementedError(
            f"ThirdPartyVectorStore.delete 尚未实现。"
            f"(collection={collection}, document_id={document_id})"
        )

    async def search(
        self,
        collection: str,
        query_vector: list[float],
        top_k: int = 5,
        metadata_filter: dict[str, Any] | None = None,
    ) -> list[VectorSearchResult]:
        """向量检索（待实现）

        Args:
            collection: collection 名称
            query_vector: 查询向量
            top_k: 返回最大结果数
            metadata_filter: 元数据过滤条件（多租户隔离用）

        Returns:
            VectorSearchResult 列表，每条包含 score, document_id, chunk
        """
        raise NotImplementedError(
            f"ThirdPartyVectorStore.search 尚未实现。"
            f"(collection={collection}, top_k={top_k})"
        )

    async def list_documents(
        self,
        collection: str,
        metadata_filter: dict[str, Any] | None = None,
    ) -> list[DocumentSummary]:
        """列出 collection 中所有文档（待实现）

        Args:
            collection: collection 名称
            metadata_filter: 元数据过滤条件

        Returns:
            DocumentSummary 列表
        """
        raise NotImplementedError(
            f"ThirdPartyVectorStore.list_documents 尚未实现。"
            f"(collection={collection})"
        )
