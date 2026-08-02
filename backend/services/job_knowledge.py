"""岗位知识库服务 — 封装 ChromaDB job_knowledge 集合的增删查操作。

与 KnowledgeBase 的区别：
  - KnowledgeBase 是文件导向的（PDF/DOCX/TXT → 解析 → 分块 → 入库）
  - JobKnowledgeBase 是结构化数据导向的（直接接受 dict 格式的岗位信息）

复用 RAGRetriever 的底层能力（Embedding + ChromaDB + Rerank），
但是操作独立的 collection "job_knowledge"。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Optional

from config_loader import get_job_knowledge_config
from log import get_logger
from services.rag_retriever import RAGRetriever, _get_collection

logger = get_logger("agent")


class JobKnowledgeBase:
    """岗位知识库服务。

    用法:
        # 检索
        results = await JobKnowledgeBase.search_jobs("AI Agent工程师")

        # 批量导入
        count = await JobKnowledgeBase.add_jobs([{...}, {...}])

        # 删除
        count = await JobKnowledgeBase.remove_jobs(["job_abc", "job_def"])

        # 统计
        stats = await JobKnowledgeBase.get_stats()
    """

    COLLECTION_NAME = "job_knowledge"

    # ── 检索 ──────────────────────────────────────────────────

    @staticmethod
    async def search_jobs(
        query: str,
        top_k: int | None = None,
        top_n: int | None = None,
        filters: dict | None = None,
    ) -> list[dict]:
        """语义搜索岗位知识库，返回格式与 _normalize_mcp_results 兼容。

        Args:
            query: 用户搜索词（如 "AI Agent 工程师 大模型"）
            top_k: ChromaDB 初召回数量
            top_n: Rerank 精排后返回数量
            filters: ChromaDB metadata 过滤条件

        Returns:
            [{job_id, title, company, salary, city, experience, education,
              description, tech_stack, platform, score}, ...]
              空列表表示无结果
        """
        cfg = get_job_knowledge_config()
        if top_k is None:
            top_k = cfg.get("search_top_k", 10)
        if top_n is None:
            top_n = cfg.get("search_top_n", 5)

        # 检查 collection 是否存在且有数据
        try:
            collection = _get_collection(JobKnowledgeBase.COLLECTION_NAME)
            if collection.count() == 0:
                logger.info("Job knowledge base is empty")
                return []
        except Exception as exc:
            logger.warning("Job knowledge base not available: %s", exc)
            return []

        # 调用 RAGRetriever 检索
        try:
            raw_results = await RAGRetriever.retrieve(
                query=query,
                top_k=top_k,
                top_n=top_n,
                collection_name=JobKnowledgeBase.COLLECTION_NAME,
                filter_metadata=filters,
            )
        except Exception as exc:
            logger.error("Job knowledge base search failed: %s", exc)
            return []

        if not raw_results:
            return []

        # 将 RAGRetriever 的结果（text + metadata）转为统一格式
        jobs = []
        seen_ids = set()
        for item in raw_results:
            meta = item.get("metadata", {}) or {}
            job_id = meta.get("job_id", "")

            # 去重（同一 job 可能跨多个 chunk 被召回，只保留第一个）
            if job_id in seen_ids:
                continue
            seen_ids.add(job_id)

            job = {
                "job_id": job_id,
                "title": meta.get("title", ""),
                "company": meta.get("company", ""),
                "city": meta.get("city", ""),
                "salary": meta.get("salary", ""),
                "experience": meta.get("experience", ""),
                "education": meta.get("education", ""),
                "description": item.get("text", meta.get("description", "")),
                "tech_stack": _split_metadata_list(meta.get("tech_stack_str", "")),
                "hard_skills": _split_metadata_list(meta.get("hard_skills_str", "")),
                "platform": meta.get("source", "job_knowledge"),
                "score": item.get("score", 0.0),
            }
            jobs.append(job)

        logger.info(
            "Job knowledge base search: query='%s' → %d results (after dedup)",
            query[:50], len(jobs),
        )
        return jobs

    # ── 导入 ──────────────────────────────────────────────────

    @staticmethod
    async def add_jobs(jobs: list[dict]) -> int:
        """批量导入岗位到知识库（幂等：重复 job_id 会自动跳过）。

        Args:
            jobs: 岗位 dict 列表，每个 dict 需包含：
                  - title (必填)
                  - tech_stack (可选，list[str])
                  - hard_skills (可选，list[str])
                  - responsibilities (可选，list[str])
                  - description (可选，str)
                  - company, city, salary, experience, education (可选)
                  - industry (可选)
                  - source (可选，默认 "boss-zhipin")

        Returns:
            成功导入的数量（去重后）
        """
        if not jobs:
            return 0

        collection = _get_collection(JobKnowledgeBase.COLLECTION_NAME)

        texts: list[str] = []
        metadatas: list[dict] = []
        ids: list[str] = []

        for job in jobs:
            # 生成唯一 ID：job_{sha256_of_identifying_fields}
            job_id = JobKnowledgeBase._build_job_id(job)

            # 构建 embedding 文本
            doc_text = JobKnowledgeBase._build_document_text(job)

            # 构建 metadata
            meta = JobKnowledgeBase._build_metadata(job, job_id)

            texts.append(doc_text)
            metadatas.append(meta)
            ids.append(job_id)

        # 批量写入 ChromaDB
        count = await RAGRetriever.add_documents(
            texts=texts,
            metadatas=metadatas,
            ids=ids,
            collection_name=JobKnowledgeBase.COLLECTION_NAME,
        )

        logger.info("Added %d jobs to job knowledge base", count)
        return count

    # ── 删除 ──────────────────────────────────────────────────

    @staticmethod
    async def remove_jobs(job_ids: list[str]) -> int:
        """从知识库中删除指定岗位。

        Args:
            job_ids: 要删除的 job_id 列表

        Returns:
            实际删除的数量
        """
        if not job_ids:
            return 0

        try:
            collection = _get_collection(JobKnowledgeBase.COLLECTION_NAME)
            # ChromaDB 的 delete 返回被删的 ID 列表
            result = collection.delete(ids=job_ids)
            deleted_count = len(result) if result else 0
            logger.info("Deleted %d jobs from job knowledge base", deleted_count)
            return deleted_count
        except Exception as exc:
            logger.error("Failed to delete jobs from knowledge base: %s", exc)
            return 0

    # ── 统计 ──────────────────────────────────────────────────

    @staticmethod
    async def get_stats() -> dict:
        """获取岗位知识库统计信息。

        Returns:
            {collection_name, document_count, status}
        """
        try:
            collection = _get_collection(JobKnowledgeBase.COLLECTION_NAME)
            return {
                "collection_name": JobKnowledgeBase.COLLECTION_NAME,
                "document_count": collection.count(),
                "status": "available",
            }
        except Exception as exc:
            return {
                "collection_name": JobKnowledgeBase.COLLECTION_NAME,
                "document_count": 0,
                "status": f"unavailable: {exc}",
            }

    # ── 内部方法 ──────────────────────────────────────────────

    @staticmethod
    def _build_job_id(job: dict) -> str:
        """基于岗位标识字段生成唯一 ID。

        用 title + company + city 的 SHA256 前缀作为 ID，
        同一岗位不会重复导入。
        """
        raw = f"{job.get('title', '')}|{job.get('company', '')}|{job.get('city', '')}"
        digest = hashlib.sha256(raw.encode()).hexdigest()[:16]
        return f"job_{digest}"

    @staticmethod
    def _build_document_text(job: dict) -> str:
        """构建用于 embedding 的文本。

        策略：将关键字段拼成一段连贯文本，让语义搜索能匹配到。
        格式：
          岗位名称: xxx
          行业: xxx
          技术栈: xxx, xxx
          硬技能: xxx, xxx
          岗位职责: xxx
          职位描述: xxx
        """
        parts = []

        title = job.get("title", "")
        if title:
            parts.append(f"岗位名称：{title}")

        industry = job.get("industry", "")
        if industry:
            parts.append(f"行业：{industry}")

        tech = job.get("tech_stack", [])
        if tech:
            parts.append(f"技术栈：{', '.join(tech if isinstance(tech, list) else [str(tech)])}")

        skills = job.get("hard_skills", [])
        if skills:
            parts.append(f"硬技能：{', '.join(skills if isinstance(skills, list) else [str(skills)])}")

        resp = job.get("responsibilities", [])
        if resp:
            items = resp if isinstance(resp, list) else [str(resp)]
            parts.append("岗位职责：" + "；".join(items[:5]))

        desc = job.get("description", "")
        if desc and len(desc) > 20:
            parts.append(f"职位描述：{desc[:2000]}")

        return "\n".join(parts)

    @staticmethod
    def _build_metadata(job: dict, job_id: str) -> dict:
        """构建 ChromaDB metadata（必须扁平键值对）。

        ChromaDB metadata 限制：
          - 值只能是 str / int / float / bool
          - list 类型需转为逗号分隔字符串
        """
        return {
            "job_id": job_id,
            "title": job.get("title", ""),
            "company": job.get("company", ""),
            "city": job.get("city", ""),
            "salary": job.get("salary", ""),
            "industry": job.get("industry", ""),
            "experience": job.get("experience", ""),
            "education": job.get("education", ""),
            "description": (job.get("description", "") or "")[:500],
            "tech_stack_str": _join_metadata_list(job.get("tech_stack", [])),
            "hard_skills_str": _join_metadata_list(job.get("hard_skills", [])),
            "source": job.get("source", "boss-zhipin"),
        }


# ── 辅助函数 ──────────────────────────────────────────────

def _join_metadata_list(items: Any) -> str:
    """将 list 转为逗号分隔字符串（ChromaDB metadata 只接受标量）。"""
    if isinstance(items, list):
        return ", ".join(str(i) for i in items if i)
    return str(items) if items else ""


def _split_metadata_list(text: str) -> list[str]:
    """将逗号分隔字符串转回 list。"""
    if not text:
        return []
    return [item.strip() for item in text.split(",") if item.strip()]
