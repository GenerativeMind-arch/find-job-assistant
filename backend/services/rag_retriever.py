"""RAG 检索器 — Embedding 向量化 + ChromaDB 相似度检索 + DashScope Rerank 精排。

检索流水线：
用户查询 → Query Embedding → ChromaDB Top-K 召回 → Rerank 精排 Top-N → 返回结果
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional

from config_loader import get_embedding_config, get_rerank_config, get_knowledge_base_config
from log import get_logger

logger = get_logger("agent")

# ChromaDB 客户端（延迟初始化）
_chroma_client: Any = None
_collection_cache: dict[str, Any] = {}


def _get_chroma_client():
    """延迟初始化 ChromaDB 持久化客户端。"""
    global _chroma_client
    if _chroma_client is not None:
        return _chroma_client
    try:
        import chromadb
        from config_loader import resolve_data_path
        kb_config = get_knowledge_base_config()
        persist_dir = resolve_data_path(kb_config.get("chroma_persist_dir", "data/chroma_db"))
        _chroma_client = chromadb.PersistentClient(path=persist_dir)
        logger.info("ChromaDB client initialized at %s", persist_dir)
    except Exception as exc:
        logger.error("ChromaDB client initialization failed: %s", exc)
        raise RuntimeError(f"ChromaDB 初始化失败：{exc}") from exc
    return _chroma_client


def _get_collection(name: str | None = None) -> Any:
    """获取或创建 ChromaDB Collection。"""
    if name is None:
        kb_config = get_knowledge_base_config()
        name = kb_config.get("collection_name", "interview_knowledge")
    if name in _collection_cache:
        return _collection_cache[name]
    client = _get_chroma_client()
    collection = client.get_or_create_collection(
        name=name,
        metadata={"description": "面试经验知识库 — Interview Assistant"},
    )
    _collection_cache[name] = collection
    logger.info("ChromaDB collection '%s' ready (%d docs)", name, collection.count())
    return collection


class RAGRetriever:
    """RAG 检索器：Embedding + 向量检索 + Rerank 精排。"""

    @staticmethod
    async def retrieve(
        query: str,
        top_k: int | None = None,
        top_n: int | None = None,
        collection_name: str | None = None,
        filter_metadata: dict | None = None,
    ) -> list[dict]:
        """
        检索知识库中最相关的文档块。

        Args:
            query: 查询文本
            top_k: ChromaDB 初召回数量（默认从 config 读取）
            top_n: Rerank 精排后返回数量（默认从 config 读取）
            collection_name: 指定 collection（默认使用配置中的）
            filter_metadata: ChromaDB metadata 过滤条件

        Returns:
            [{text: str, score: float, metadata: dict}, ...]
        """
        kb_config = get_knowledge_base_config()
        if top_k is None:
            top_k = int(kb_config.get("search_top_k", 10))
        if top_n is None:
            top_n = int(kb_config.get("rerank_top_n", 5))

        # 1. Query Embedding
        query_vector = await RAGRetriever._embed_query(query)

        # 2. ChromaDB 向量检索
        collection = _get_collection(collection_name)
        if collection.count() == 0:
            logger.info("Knowledge base is empty, no results for query: %s", query[:50])
            return []

        search_kwargs = {
            "query_embeddings": [query_vector],
            "n_results": min(top_k, collection.count()),
        }
        if filter_metadata:
            search_kwargs["where"] = filter_metadata

        chroma_results = collection.query(**search_kwargs)

        documents = chroma_results.get("documents", [[]])[0]
        metadatas = chroma_results.get("metadatas", [[]])[0]
        distances = chroma_results.get("distances", [[]])[0]

        if not documents:
            logger.info("No documents found for query: %s", query[:50])
            return []

        # 3. Rerank 精排
        rerank_top_n = min(top_n, len(documents))
        reranked = await RAGRetriever._rerank(query, documents, rerank_top_n)

        # 4. 组装结果
        results = []
        for item in reranked:
            idx = item.get("index", 0)
            score = item.get("relevance_score", 0.0)
            if 0 <= idx < len(documents):
                results.append({
                    "text": documents[idx],
                    "score": float(score),
                    "metadata": metadatas[idx] if idx < len(metadatas) else {},
                })

        logger.info(
            "RAG retrieve: query='%s' → ChromaDB top-%d → Rerank top-%d → %d results",
            query[:50], top_k, rerank_top_n, len(results),
        )
        return results

    @staticmethod
    async def retrieve_context(
        query: str,
        top_n: int = 5,
        collection_name: str | None = None,
    ) -> str:
        """
        检索并返回格式化的上下文字符串，可直接注入 Agent Prompt。

        Returns:
            格式化的参考上下文文本（多段以分隔线隔开），无结果时返回空字符串。
        """
        results = await RAGRetriever.retrieve(query, top_n=top_n, collection_name=collection_name)
        if not results:
            return ""

        parts = ["【以下来自知识库中的真实面试经验，仅供参考】"]
        for i, r in enumerate(results, 1):
            metadata = r.get("metadata", {})
            source = metadata.get("source", metadata.get("filename", "未知来源"))
            company = metadata.get("company", "")
            role = metadata.get("role", metadata.get("position", ""))
            header = f"参考 {i}"
            if company:
                header += f" — {company}"
            if role:
                header += f" · {role}"
            parts.append(f"\n### {header}\n来源: {source}\n\n{r['text']}")

        return "\n".join(parts) + "\n\n---\n"

    @staticmethod
    async def add_documents(
        texts: list[str],
        metadatas: list[dict] | None = None,
        ids: list[str] | None = None,
        collection_name: str | None = None,
    ) -> int:
        """
        向知识库中添加文档（自动 Embedding + 入库）。

        Returns:
            成功添加的文档数量
        """
        if not texts:
            return 0
        collection = _get_collection(collection_name)

        # 生成 embeddings
        try:
            from models.embedding import aembed_documents
            vectors = await aembed_documents(texts)
        except Exception as exc:
            logger.error("Embedding failed for %d documents: %s", len(texts), exc)
            raise

        # 生成 ID（如果未提供）
        if ids is None:
            import uuid
            ids = [f"doc_{uuid.uuid4().hex[:16]}" for _ in texts]

        # 确保 metadatas 与 texts 长度一致
        if metadatas is None:
            metadatas = [{} for _ in texts]

        collection.add(
            ids=ids,
            embeddings=vectors,
            documents=texts,
            metadatas=metadatas,
        )
        logger.info("Added %d documents to collection '%s'", len(texts), collection.name)
        return len(texts)

    # ── 内部方法 ──────────────────────────────────────────────

    @staticmethod
    async def _embed_query(text: str) -> list[float]:
        """将查询文本转为向量。"""
        from models.embedding import aembed_query
        return await aembed_query(text)

    @staticmethod
    async def _rerank(query: str, documents: list[str], top_n: int) -> list[dict]:
        """对候选文档进行 Rerank 精排。"""
        try:
            from models.rerank import arerank_texts
            return await arerank_texts(documents, query, top_n)
        except Exception as exc:
            logger.warning("Rerank failed, using ChromaDB order: %s", exc)
            # Fallback：保持 ChromaDB 原始顺序
            return [{"index": i, "relevance_score": 1.0 - i * 0.1} for i in range(min(top_n, len(documents)))]
