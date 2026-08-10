# -*- coding: utf-8 -*-
"""结构化知识 JSON → ChromaDB 知识库入库

读取 scripts/ocr_and_structure.py 产出的结构化 JSON，按 Q&A 对切分，
存入对应的 ChromaDB collection：
  - write_cv_structured.json → collection `cv_knowledge`
  - llm_qa_structured.json   → collection `llm_interview_knowledge`

每条 Q&A / 知识点作为独立 chunk（最小检索单元），metadata 携带来源与分类。

用法：
  python scripts/build_knowledge_base.py
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import sys
from pathlib import Path

# Windows GBK 控制台兼容
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

_BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(_BACKEND_DIR))
_KB_DIR = Path(__file__).resolve().parent.parent / "data" / "knowledge_base"

# 来源 → (collection 名, 类型)
SOURCES = [
    ("write_cv_structured.json", "cv_knowledge", "cv"),
    ("llm_qa_structured.json", "llm_interview_knowledge", "qa"),
]


def _chunk_id(source: str, idx: int, text: str) -> str:
    h = hashlib.md5(f"{source}:{idx}:{text[:60]}".encode()).hexdigest()[:16]
    return f"kb_{h}"


def _build_chunks(doc_type: str, data: dict) -> list[tuple[str, dict]]:
    """返回 [(chunk_text, metadata), ...]"""
    chunks: list[tuple[str, dict]] = []
    if doc_type == "qa":
        for i, qa in enumerate(data.get("qa_pairs", []), 1):
            q = (qa.get("question") or "").strip()
            a = (qa.get("answer") or "").strip()
            if not q and not a:
                continue
            category = qa.get("category", "")
            difficulty = qa.get("difficulty", "")
            text = f"## Q: {q}\n"
            if category or difficulty:
                text += f"**分类**: {category} | **难度**: {difficulty}\n"
            text += f"\n## A: {a}"
            meta = {
                "source_file": "llm_qa.pdf",
                "doc_type": "interview_qa",
                "category": category,
                "difficulty": difficulty,
                "question": q,
            }
            chunks.append((text, meta))
    else:
        # write_cv：优先层级结构（sections → items），兼容旧扁平结构
        sections = data.get("sections")
        if sections:
            for sec in sections:
                sec_title = (sec.get("title") or "").strip()
                for item in sec.get("items", []):
                    num = item.get("number", "")
                    title = (item.get("title") or "").strip()
                    content = (item.get("content") or "").strip()
                    examples = (item.get("examples") or "").strip()
                    keywords = item.get("keywords", [])
                    if not title and not content:
                        continue
                    # chunk 前缀保留大标题层级上下文，检索更精确
                    header = f"## {num}. {title}" if num else f"## {title}"
                    text = f"## {sec_title}\n{header}\n\n**要点**: {content}"
                    if examples:
                        text += f"\n\n**示例**:\n{examples}"
                    if keywords:
                        text += f"\n\n**标签**: {', '.join(keywords)}"
                    meta = {
                        "source_file": "write_cv.pdf",
                        "doc_type": "cv_guide",
                        "section": sec_title,
                        "topic": title,
                        "keywords": ", ".join(keywords),  # ChromaDB metadata 不支持 list，转字符串
                    }
                    chunks.append((text, meta))
        else:
            # 兼容旧扁平 knowledge_points 结构
            for kp in data.get("knowledge_points", []):
                topic = (kp.get("topic") or "").strip()
                guidance = (kp.get("guidance") or "").strip()
                examples = (kp.get("examples") or "").strip()
                keywords = kp.get("keywords", [])
                if not topic and not guidance:
                    continue
                text = f"## 主题: {topic}\n\n**要点**: {guidance}"
                if examples:
                    text += f"\n\n**示例**:\n{examples}"
                if keywords:
                    text += f"\n\n**标签**: {', '.join(keywords)}"
                meta = {
                    "source_file": "write_cv.pdf",
                    "doc_type": "cv_guide",
                    "topic": topic,
                    "keywords": ", ".join(keywords),
                }
                chunks.append((text, meta))
    return chunks


def _clear_collection(collection_name: str) -> None:
    """删除并重建 collection（幂等重建用，避免 `where={}` 无效导致的重复）。"""
    from services.rag_retriever import _get_chroma_client
    try:
        client = _get_chroma_client()
        client.delete_collection(collection_name)
        print(f"  删除并重建 collection '{collection_name}'", flush=True)
    except Exception as e:
        print(f"  [警告] 删除 {collection_name} 失败: {e}", flush=True)


async def ingest_one(json_file: Path, collection: str, doc_type: str, clear: bool = False) -> None:
    from services.rag_retriever import RAGRetriever

    if not json_file.exists():
        print(f"[跳过] {json_file.name} 不存在", flush=True)
        return

    if clear:
        _clear_collection(collection)

    data = json.loads(json_file.read_text(encoding="utf-8"))
    chunks = _build_chunks(doc_type, data)
    if not chunks:
        print(f"[警告] {json_file.name} 无可入库内容", flush=True)
        return

    texts = [c for c, _ in chunks]
    metadatas = [m for _, m in chunks]
    ids = [_chunk_id(json_file.stem, i, texts[i]) for i in range(len(texts))]

    count = await RAGRetriever.add_documents(
        texts=texts, metadatas=metadatas, ids=ids, collection_name=collection,
    )
    print(f"[OK] {json_file.name} → collection '{collection}' 入库 {count} 条", flush=True)


async def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--clear", action="store_true", help="入库前清空对应 collection（幂等重建）")
    args = ap.parse_args()
    for fname, collection, doc_type in SOURCES:
        await ingest_one(_KB_DIR / fname, collection, doc_type, clear=args.clear)
    print("知识库入库完成", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
