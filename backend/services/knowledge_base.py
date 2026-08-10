"""知识库管理服务 — 文档入库、智能解析、分块策略。

支持的文件类型：
- .docx Word 文档
- .pdf 文本型/扫描件/混合型 PDF
- .txt/.md 纯文本
- .png/.jpg 图片（OCR 识别）

入库流水线：
原始文件 → FileClassifier 分类 → 差异化解析 → 智能分块 → RAGRetriever 入库
"""

from __future__ import annotations

import hashlib
import io
import asyncio
from pathlib import Path
from typing import Optional

from config_loader import get_knowledge_base_config
from log import get_logger

logger = get_logger("agent")


class KnowledgeBase:
    """知识库管理器 — 负责文档入库和检索入口。"""

    @staticmethod
    async def ingest_file(
        file_path: str | Path,
        metadata: dict | None = None,
    ) -> int:
        """
        将单个文件解析、分块、向量化后入库。

        Args:
            file_path: 文件路径
            metadata: 自定义元数据（如 {company, role, stage, date}）

        Returns:
            入库的文档块数量
        """
        file_path = Path(file_path)
        if not file_path.exists():
            raise FileNotFoundError(f"文件不存在: {file_path}")

        filename = file_path.name
        file_bytes = file_path.read_bytes()
        meta = metadata or {}
        meta["source"] = str(file_path)
        meta["filename"] = filename

        return await KnowledgeBase._ingest_bytes(file_bytes, filename, meta)

    @staticmethod
    async def ingest_bytes(
        file_bytes: bytes,
        filename: str,
        metadata: dict | None = None,
    ) -> int:
        """将内存中的文件数据解析入库。"""
        meta = metadata or {}
        meta["filename"] = filename
        return await KnowledgeBase._ingest_bytes(file_bytes, filename, meta)

    @staticmethod
    async def ingest_directory(
        directory: str | Path,
        metadata: dict | None = None,
    ) -> dict:
        """
        批量导入目录下的所有支持文件。

        Returns:
            {total_files: int, total_chunks: int, errors: list[str]}
        """
        directory = Path(directory)
        if not directory.is_dir():
            raise NotADirectoryError(f"目录不存在: {directory}")

        supported_extensions = {".pdf", ".docx", ".doc", ".txt", ".md", ".markdown", ".png", ".jpg", ".jpeg"}
        total_chunks = 0
        total_files = 0
        errors = []

        for file_path in directory.rglob("*"):
            if file_path.is_file() and file_path.suffix.lower() in supported_extensions:
                try:
                    chunks = await KnowledgeBase.ingest_file(file_path, metadata)
                    total_chunks += chunks
                    total_files += 1
                    logger.info("Ingested %s → %d chunks", file_path.name, chunks)
                except Exception as exc:
                    err_msg = f"{file_path.name}: {exc}"
                    errors.append(err_msg)
                    logger.error("Failed to ingest %s: %s", file_path.name, exc)

        logger.info(
            "Directory ingest complete: %d files, %d chunks, %d errors",
            total_files, total_chunks, len(errors),
        )
        return {
            "total_files": total_files,
            "total_chunks": total_chunks,
            "errors": errors,
        }

    @staticmethod
    async def search(query: str, top_n: int = 5, collection_name: str | None = None) -> str:
        """
        检索知识库并返回格式化的上下文字符串。
        对外统一入口，供 Agent 直接调用。

        Args:
            query: 检索查询
            top_n: 返回条数
            collection_name: 指定 collection（默认 interview_knowledge）
        """
        from services.rag_retriever import RAGRetriever
        return await RAGRetriever.retrieve_context(query, top_n=top_n, collection_name=collection_name)

    @staticmethod
    def get_stats() -> dict:
        """获取知识库统计信息。"""
        from services.rag_retriever import _get_collection
        collection = _get_collection()
        return {
            "collection_name": collection.name,
            "document_count": collection.count(),
        }

    # ── 内部方法 ──────────────────────────────────────────────

    @staticmethod
    async def _ingest_bytes(file_bytes: bytes, filename: str, metadata: dict) -> int:
        """内部入库逻辑：分类 → 解析 → 分块 → Embedding → 入库。"""
        from services.file_classifier import FileClassifier, FileCategory
        from services.rag_retriever import RAGRetriever

        # 1. 文件分类
        classification = FileClassifier.classify(file_bytes, filename)
        logger.info("Classified %s → %s", filename, classification.category.value)

        # 2. 差异化解析
        raw_text = await KnowledgeBase._parse_file(file_bytes, filename, classification)

        if not raw_text or not raw_text.strip():
            logger.warning("No text extracted from %s", filename)
            return 0

        # 3. 智能分块
        chunks = KnowledgeBase._chunk_text(raw_text, filename, classification)

        if not chunks:
            return 0

        # 4. 向量化 + 入库
        metadatas = []
        ids = []
        for i, chunk in enumerate(chunks):
            chunk_meta = dict(metadata)
            chunk_meta.update({
                "filename": filename,
                "chunk_index": i,
                "total_chunks": len(chunks),
                "file_type": classification.category.value,
                "source": chunk_meta.get("source", filename),
            })
            metadatas.append(chunk_meta)
            chunk_id = hashlib.md5(f"{filename}:{i}:{chunk[:50]}".encode()).hexdigest()[:16]
            ids.append(f"chunk_{chunk_id}")

        count = await RAGRetriever.add_documents(
            texts=chunks,
            metadatas=metadatas,
            ids=ids,
        )
        return count

    @staticmethod
    async def _parse_file(
        file_bytes: bytes,
        filename: str,
        classification,
    ) -> str:
        """根据分类结果选择最优解析策略。"""
        from services.file_classifier import FileCategory

        category = classification.category
        texts: list[str] = []

        try:
            if category == FileCategory.DOCX:
                texts.append(_extract_docx(file_bytes))

            elif category == FileCategory.PDF_TEXT:
                texts.append(_extract_pdf_text(file_bytes))
                if classification.needs_table_extraction():
                    table_text = _extract_pdf_tables(file_bytes)
                    if table_text:
                        texts.append(table_text)

            elif category == FileCategory.PDF_SCANNED:
                from services.ocr_processor import ocr_pdf_pages
                ocr_text = await ocr_pdf_pages(file_bytes)
                if ocr_text:
                    texts.append(ocr_text)

            elif category == FileCategory.PDF_MIXED:
                # 混合型：先提取文本，再对空白页 OCR 补充
                text = _extract_pdf_text(file_bytes)
                texts.append(text)
                from services.ocr_processor import ocr_pdf_pages
                ocr_text = await ocr_pdf_pages(file_bytes)
                if ocr_text:
                    texts.append("\n[OCR 补充内容]\n" + ocr_text)

            elif category in (FileCategory.TEXT,):
                texts.append(file_bytes.decode("utf-8", errors="replace"))

            elif category == FileCategory.IMAGE:
                from services.ocr_processor import ocr_image
                ocr_text = await ocr_image(file_bytes)
                if ocr_text:
                    texts.append(ocr_text)

            else:
                # UNKNOWN：尝试 UTF-8 解码
                texts.append(file_bytes.decode("utf-8", errors="replace"))

        except Exception as exc:
            logger.error("Parse failed for %s (%s): %s", filename, category.value, exc)
            texts.append(f"[解析 {filename} 时发生错误：{exc}]")

        return "\n\n".join(t for t in texts if t.strip())

    @staticmethod
    def _chunk_text(text: str, filename: str, classification) -> list[str]:
        """智能分块策略 — 根据内容类型选择最优分块方式。"""
        from config_loader import get_knowledge_base_config

        kb_config = get_knowledge_base_config()
        chunk_size = int(kb_config.get("chunk_size", 512))
        chunk_overlap = int(kb_config.get("chunk_overlap", 64))

        # ── 策略 1：Q&A 格式 — 按 Q&A 对分割 ──
        if _is_qa_format(text):
            return _chunk_by_qa(text)

        # ── 策略 2：技术面试题 — 按题目编号分割 ──
        if _is_interview_questions(text):
            return _chunk_by_question_number(text)

        # ── 策略 3：通用文档 — 递归文本分割 ──
        return _chunk_recursive(text, chunk_size, chunk_overlap)


# ── 分块策略函数 ──────────────────────────────────────────────

def _is_qa_format(text: str) -> bool:
    """判断是否为 Q&A 对话格式。"""
    import re
    qa_patterns = [
        r'(?:Q[:\s]|问[:\s]|问题\s*\d+[:\s])',
        r'(?:A[:\s]|答[:\s]|回答\s*\d+[:\s]|参考答案[:\s])',
        r'(?:面试官[:\s]|面试者[:\s])',
    ]
    matches = sum(1 for pat in qa_patterns if re.search(pat, text, re.IGNORECASE))
    return matches >= 2


def _is_interview_questions(text: str) -> bool:
    """判断是否为面试题目列表格式。"""
    import re
    # 检测是否有多个编号题目
    numbered_items = re.findall(r'(?:^|\n)\s*(?:\d+[\.、\)]\s*|[（(]\d+[）)])', text)
    return len(numbered_items) >= 3


def _chunk_by_qa(text: str) -> list[str]:
    """按 Q&A 对分割文本。"""
    import re
    # 匹配 Q: 或 问: 开头的内容块
    qa_blocks = re.split(r'\n(?=(?:Q[:\s]|问[:\s]))', text)
    chunks = []
    current = ""
    for block in qa_blocks:
        if re.match(r'(?:Q[:\s]|问[:\s])', block):
            if current.strip():
                chunks.append(current.strip())
            current = block
        else:
            if len(current) < 2000:
                current += "\n" + block
            else:
                chunks.append(current.strip())
                current = block
    if current.strip():
        chunks.append(current.strip())
    return [c for c in chunks if len(c) > 10]


def _chunk_by_question_number(text: str) -> list[str]:
    """按题目序号分割文本。"""
    import re
    blocks = re.split(r'\n(?=\s*(?:\d+[\.、\)])\s*)', text)
    return [b.strip() for b in blocks if len(b.strip()) > 10]


def _chunk_recursive(text: str, chunk_size: int = 512, chunk_overlap: int = 64) -> list[str]:
    """递归文本分割（通用策略）。"""
    try:
        from langchain_text_splitters import RecursiveCharacterTextSplitter
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            separators=["\n\n", "\n", "。", "！", "？", "；", "，", " ", ""],
            length_function=len,
        )
        return splitter.split_text(text)
    except ImportError:
        pass

    # 简易 fallback 分割器
    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunk = text[start:end]
        chunks.append(chunk)
        start = end - chunk_overlap
    return chunks


# ── 各类文件解析函数 ──────────────────────────────────────────

def _extract_docx(file_bytes: bytes) -> str:
    """提取 DOCX 文件文本。"""
    try:
        from docx import Document
        doc = Document(io.BytesIO(file_bytes))
        paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
        return "\n\n".join(paragraphs)
    except Exception as exc:
        logger.error("DOCX extraction failed: %s", exc)
        return f"[DOCX 解析失败：{exc}]"


def _extract_pdf_text(file_bytes: bytes) -> str:
    """提取 PDF 文本（pdfplumber）。"""
    try:
        import pdfplumber
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            texts = []
            for i, page in enumerate(pdf.pages):
                text = page.extract_text()
                if text and text.strip():
                    texts.append(f"[Page {i + 1}]\n{text.strip()}")
            return "\n\n".join(texts)
    except Exception as exc:
        logger.error("PDF text extraction failed: %s", exc)
        return f"[PDF 文本提取失败：{exc}]"


def _extract_pdf_tables(file_bytes: bytes) -> str:
    """提取 PDF 中的表格，转换为 Markdown 表格格式。"""
    try:
        import pdfplumber
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            all_tables = []
            for i, page in enumerate(pdf.pages):
                tables = page.extract_tables()
                for j, table in enumerate(tables):
                    if table and len(table) > 1:  # 至少要有表头 + 一行数据
                        md_table = _table_to_markdown(table)
                        all_tables.append(f"[Page {i + 1}, Table {j + 1}]\n{md_table}")
            return "\n\n".join(all_tables) if all_tables else ""
    except Exception as exc:
        logger.error("PDF table extraction failed: %s", exc)
        return ""


def _table_to_markdown(table: list[list]) -> str:
    """将二维表转为 Markdown 表格。"""
    if not table:
        return ""
    lines = []
    # 表头
    header = [str(cell or "") for cell in table[0]]
    lines.append("| " + " | ".join(header) + " |")
    # 分隔行
    lines.append("| " + " | ".join("---" for _ in header) + " |")
    # 数据行
    for row in table[1:]:
        cells = [str(cell or "") for cell in row]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)
