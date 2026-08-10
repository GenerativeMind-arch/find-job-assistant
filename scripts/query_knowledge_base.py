# -*- coding: utf-8 -*-
"""查看 ChromaDB 知识库内容（只读）

用法：
  python scripts/query_knowledge_base.py                    # 列出全部 collection 及条数
  python scripts/query_knowledge_base.py --collection llm_interview_knowledge   # 查看该库前 5 条 chunk
  python scripts/query_knowledge_base.py --collection llm_interview_knowledge --limit 20  # 前 20 条
  python scripts/query_knowledge_base.py --query "什么是LoRA微调"               # 语义检索（默认 llm_interview_knowledge）
  python scripts/query_knowledge_base.py --query "简历项目经历" --collection cv_knowledge
  python scripts/query_knowledge_base.py --collection cv_knowledge --filter source_file=write_cv.pdf  # 按 metadata 过滤
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Windows GBK 控制台兼容
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

_BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(_BACKEND_DIR))


def _print_chunk(doc: str, meta: dict | None = None, score: float | None = None):
    if score is not None:
        print(f"  [score={score:.3f}]")
    if meta:
        cat = meta.get("category") or meta.get("topic") or ""
        src = meta.get("source_file") or ""
        print(f"  [来源: {src} | 分类: {cat}]")
    print(f"  {doc[:400]}")
    print()


def main():
    from services.rag_retriever import _get_chroma_client, _get_collection, RAGRetriever

    ap = argparse.ArgumentParser(description="查看 ChromaDB 知识库")
    ap.add_argument("--collection", default=None, help="collection 名")
    ap.add_argument("--limit", type=int, default=5, help="显示条数")
    ap.add_argument("--query", default=None, help="语义检索查询")
    ap.add_argument("--filter", default=None, help="metadata 过滤，格式 k=v（可多次用逗号分隔）")
    args = ap.parse_args()

    client = _get_chroma_client()
    collections = client.list_collections()

    if args.collection is None and args.query is None:
        # 仅列出全部 collection
        print("=== ChromaDB Collections ===")
        for c in collections:
            print(f"  {c.name}: {c.count()} 条")
        print("\n提示: 用 --collection <名称> 查看具体内容，--query <问题> 语义检索。")
        return

    name = args.collection or "llm_interview_knowledge"

    # 语义检索
    if args.query:
        print(f"=== 语义检索 [{name}]：「{args.query}」 ===")
        top_n = args.limit
        results = await_retrieve(name, args.query, top_n)
        for item in results:
            _print_chunk(item.get("text", ""), item.get("metadata"), item.get("score"))
        return

    # 查看指定 collection 内容
    col = _get_collection(name)
    print(f"=== collection '{name}' 共 {col.count()} 条，显示前 {args.limit} 条 ===")

    where = None
    if args.filter:
        where = {}
        for kv in args.filter.split(","):
            k, v = kv.split("=", 1)
            where[k.strip()] = v.strip()

    data = col.get(limit=args.limit, include=["documents", "metadatas"], where=where)
    docs = data.get("documents", [])
    metas = data.get("metadatas", [])
    for i, doc in enumerate(docs):
        print(f"[{i + 1}]")
        _print_chunk(doc, metas[i] if i < len(metas) else None)


def await_retrieve(collection: str, query: str, top_n: int) -> list[dict]:
    import asyncio

    async def _run():
        from services.rag_retriever import RAGRetriever
        return await RAGRetriever.retrieve(query, top_k=top_n * 3, top_n=top_n, collection_name=collection)

    return asyncio.run(_run())


if __name__ == "__main__":
    main()
