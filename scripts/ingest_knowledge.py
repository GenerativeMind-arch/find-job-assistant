#!/usr/bin/env python
"""
知识库入库脚本 — 将 data/knowledge_base/ 目录下的所有文件向量化存入 ChromaDB。

用法:
    python scripts/ingest_knowledge.py                        # 导入默认目录
    python scripts/ingest_knowledge.py --dir path/to/files    # 导入指定目录
    python scripts/ingest_knowledge.py --file resume.pdf      # 导入单个文件
    python scripts/ingest_knowledge.py --stats                # 查看知识库统计
    python scripts/ingest_knowledge.py --clear                # 清空知识库

首次运行时 PaddleOCR 会自动下载模型（约 200MB），请确保网络通畅。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

# 确保 backend 在 sys.path 中
BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from backend.services.knowledge_base import KnowledgeBase
from backend.services.rag_retriever import _get_collection


async def ingest_directory(directory: str, metadata: dict | None = None) -> dict:
    """导入目录下所有文件。"""
    print(f"📂 扫描目录: {directory}")
    result = await KnowledgeBase.ingest_directory(directory, metadata)
    print(f"✅ 完成：{result['total_files']} 个文件 → {result['total_chunks']} 个文档块")
    if result["errors"]:
        print(f"⚠️  {len(result['errors'])} 个错误:")
        for err in result["errors"]:
            print(f"   - {err}")
    return result


async def ingest_file(file_path: str, metadata: dict | None = None) -> int:
    """导入单个文件。"""
    print(f"📄 导入文件: {file_path}")
    chunks = await KnowledgeBase.ingest_file(file_path, metadata)
    print(f"✅ 完成：{chunks} 个文档块")
    return chunks


async def show_stats():
    """显示知识库统计信息。"""
    stats = KnowledgeBase.get_stats()
    print(f"📊 知识库统计:")
    print(f"   Collection: {stats['collection_name']}")
    print(f"   文档总数: {stats['document_count']}")


async def clear_knowledge_base():
    """清空知识库。"""
    from backend.config_loader import get_knowledge_base_config
    import chromadb
    kb_config = get_knowledge_base_config()
    persist_dir = kb_config.get("chroma_persist_dir", "data/chroma_db")
    collection_name = kb_config.get("collection_name", "interview_knowledge")

    client = chromadb.PersistentClient(path=persist_dir)
    try:
        client.delete_collection(collection_name)
        print(f"🗑️  已删除 collection '{collection_name}'")
    except Exception as exc:
        print(f"⚠️  删除失败（可能不存在）: {exc}")


def main():
    parser = argparse.ArgumentParser(description="Interview Assistant — 知识库入库工具")
    parser.add_argument("--dir", type=str, help="导入指定目录")
    parser.add_argument("--file", type=str, help="导入单个文件")
    parser.add_argument("--stats", action="store_true", help="查看知识库统计")
    parser.add_argument("--clear", action="store_true", help="清空知识库（谨慎）")
    parser.add_argument("--company", type=str, default="", help="元数据：公司名称")
    parser.add_argument("--role", type=str, default="", help="元数据：职位")
    parser.add_argument("--stage", type=str, default="", help="元数据：面试阶段（初试/复试/终面）")
    args = parser.parse_args()

    # 构建元数据
    metadata = {}
    if args.company:
        metadata["company"] = args.company
    if args.role:
        metadata["role"] = args.role
    if args.stage:
        metadata["stage"] = args.stage

    if args.clear:
        asyncio.run(clear_knowledge_base())
        return

    if args.stats:
        asyncio.run(show_stats())
        return

    if args.file:
        path = Path(args.file)
        if not path.exists():
            print(f"❌ 文件不存在: {args.file}")
            sys.exit(1)
        asyncio.run(ingest_file(args.file, metadata))
        asyncio.run(show_stats())
        return

    if args.dir:
        directory = args.dir
    else:
        # 默认目录
        directory = str(Path(__file__).resolve().parent.parent / "data" / "knowledge_base")
        if not Path(directory).exists():
            print(f"❌ 默认知识库目录不存在: {directory}")
            print(f"   请创建该目录并放入面试经验文件（PDF/DOCX/TXT/图片）后重试。")
            print(f"   或使用 --dir 指定其他目录。")
            sys.exit(1)

    asyncio.run(ingest_directory(directory, metadata))
    asyncio.run(show_stats())


if __name__ == "__main__":
    main()
