"""文件类型智能分类器 — 识别文档格式并路由到最优解析策略。

支持的判定维度：
- 文件扩展名（.pdf / .docx / .txt / .md / .png / .jpg）
- PDF 内部内容特征（文本型 / 扫描件 / 含表格 / 含嵌入图片）
"""

from __future__ import annotations

import io
from enum import Enum
from pathlib import Path
from typing import Optional

from log import get_logger

logger = get_logger("agent")


class PDFType(str, Enum):
    """PDF 内部内容类型。"""
    TEXT = "text"                # 文本型 PDF：可直接提取文字
    SCANNED = "scanned"          # 扫描件 PDF：需要 OCR
    MIXED = "mixed"              # 混合型：部分页面有文字、部分为扫描
    WITH_TABLES = "with_tables"  # 含表格 PDF：需要表格提取
    WITH_IMAGES = "with_images"  # 含嵌入图片：需要提取图片后 OCR


class FileCategory(str, Enum):
    """文件大类。"""
    DOCX = "docx"
    PDF_TEXT = "pdf_text"
    PDF_SCANNED = "pdf_scanned"
    PDF_MIXED = "pdf_mixed"
    TEXT = "text"                # .txt / .md
    IMAGE = "image"              # .png / .jpg / .jpeg
    UNKNOWN = "unknown"


class ClassificationResult:
    """文件分类结果。"""
    def __init__(
        self,
        category: FileCategory,
        pdf_type: Optional[PDFType] = None,
        has_tables: bool = False,
        has_images: bool = False,
        text_length: int = 0,
        page_count: int = 0,
        metadata: Optional[dict] = None,
    ):
        self.category = category
        self.pdf_type = pdf_type
        self.has_tables = has_tables
        self.has_images = has_images
        self.text_length = text_length
        self.page_count = page_count
        self.metadata = metadata or {}

    def needs_ocr(self) -> bool:
        """是否需要 OCR 处理。"""
        return self.category in {
            FileCategory.PDF_SCANNED,
            FileCategory.PDF_MIXED,
            FileCategory.IMAGE,
        }

    def needs_table_extraction(self) -> bool:
        """是否需要表格提取。"""
        return self.has_tables

    def needs_image_extraction(self) -> bool:
        """是否需要从 PDF 中提取嵌入图片。"""
        return self.has_images

    def to_dict(self) -> dict:
        return {
            "category": self.category.value,
            "pdf_type": self.pdf_type.value if self.pdf_type else None,
            "has_tables": self.has_tables,
            "has_images": self.has_images,
            "text_length": self.text_length,
            "page_count": self.page_count,
            "metadata": self.metadata,
        }


class FileClassifier:
    """文件类型分类器。

    分类策略：
    1. 根据扩展名初步分类
    2. 对 PDF 进行深度分析（文本量 / 表格 / 图片）
    3. 返回 ClassificationResult 指导后续解析
    """

    # 文本型 PDF 最低字符数阈值
    _TEXT_PDF_MIN_CHARS = 100

    @staticmethod
    def classify_by_extension(filename: str) -> str:
        """根据文件名后缀返回基础分类。"""
        ext = Path(filename).suffix.lower()
        mapping = {
            ".pdf": "pdf",
            ".docx": "docx",
            ".doc": "docx",
            ".txt": "text",
            ".md": "text",
            ".markdown": "text",
            ".png": "image",
            ".jpg": "image",
            ".jpeg": "image",
            ".bmp": "image",
            ".tiff": "image",
            ".tif": "image",
        }
        return mapping.get(ext, "unknown")

    @staticmethod
    def classify_pdf(
        file_bytes: bytes,
        filename: str = "",
    ) -> ClassificationResult:
        """
        深度分析 PDF 文件内容特征，返回详细的分类结果。

        判定逻辑：
        1. 用 pdfplumber 提取文本 → 判断文本量
        2. 用 pdfplumber 检测表格 → 判断是否需要表格提取
        3. 用 PyMuPDF (fitz) 检测嵌入图片 → 判断是否需要图片 OCR
        """
        page_count = 0
        total_text_len = 0
        has_tables = False
        has_images = False
        has_text_pages = 0
        has_empty_pages = 0

        # ── 阶段 1：pdfplumber 分析文本和表格 ──
        try:
            import pdfplumber
            with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
                page_count = len(pdf.pages)
                for page in pdf.pages:
                    text = page.extract_text() or ""
                    text_len = len(text.strip())
                    total_text_len += text_len
                    if text_len > 50:
                        has_text_pages += 1
                    elif text_len < 10:
                        has_empty_pages += 1

                    # 表格检测
                    tables = page.extract_tables()
                    if tables and len(tables) > 0:
                        has_tables = True

                    # 图片检测（pdfplumber 的 page.images）
                    if page.images and len(page.images) > 0:
                        has_images = True

        except Exception as exc:
            logger.warning("pdfplumber analysis failed for %s: %s", filename, exc)
            # 降级：尝试用 PyMuPDF 分析
            return FileClassifier._classify_pdf_fallback(file_bytes, filename)

        # ── 阶段 2：PyMuPDF 补充检测嵌入图片 ──
        try:
            import fitz  # PyMuPDF
            doc = fitz.open(stream=file_bytes, filetype="pdf")
            for page_num in range(len(doc)):
                page = doc[page_num]
                images = page.get_images(full=True)
                if images and len(images) > 0:
                    has_images = True
                    break
            doc.close()
        except Exception:
            pass  # PyMuPDF 仅作补充检测，失败不影响主流程

        # ── 阶段 3：综合判定 ──
        category: FileCategory
        pdf_type: PDFType

        if total_text_len >= FileClassifier._TEXT_PDF_MIN_CHARS and has_empty_pages < page_count * 0.5:
            # 大多数页面有文字 → 文本型
            pdf_type = PDFType.TEXT
            category = FileCategory.PDF_TEXT
            if has_tables:
                pdf_type = PDFType.WITH_TABLES
            if has_images:
                pdf_type = PDFType.WITH_IMAGES if not has_tables else PDFType.WITH_TABLES
        elif has_text_pages == 0:
            # 几乎无文字 → 扫描件
            pdf_type = PDFType.SCANNED
            category = FileCategory.PDF_SCANNED
        else:
            # 混合型
            pdf_type = PDFType.MIXED
            category = FileCategory.PDF_MIXED

        result = ClassificationResult(
            category=category,
            pdf_type=pdf_type,
            has_tables=has_tables,
            has_images=has_images,
            text_length=total_text_len,
            page_count=page_count,
            metadata={
                "filename": filename,
                "has_text_pages": has_text_pages,
                "has_empty_pages": has_empty_pages,
            },
        )

        logger.info(
            "PDF classified: %s → %s (pages=%d, text_len=%d, tables=%s, images=%s)",
            filename, category.value, page_count, total_text_len, has_tables, has_images,
        )
        return result

    @staticmethod
    def _classify_pdf_fallback(file_bytes: bytes, filename: str) -> ClassificationResult:
        """PyMuPDF 作为 pdfplumber 失败的 fallback。"""
        try:
            import fitz
            doc = fitz.open(stream=file_bytes, filetype="pdf")
            page_count = len(doc)
            total_text_len = 0
            has_images = False

            for page_num in range(len(doc)):
                page = doc[page_num]
                text = page.get_text()
                total_text_len += len(text.strip())
                if page.get_images(full=True):
                    has_images = True

            doc.close()

            if total_text_len >= FileClassifier._TEXT_PDF_MIN_CHARS:
                category = FileCategory.PDF_TEXT
                pdf_type = PDFType.WITH_IMAGES if has_images else PDFType.TEXT
            else:
                category = FileCategory.PDF_SCANNED
                pdf_type = PDFType.SCANNED

            return ClassificationResult(
                category=category,
                pdf_type=pdf_type,
                has_images=has_images,
                text_length=total_text_len,
                page_count=page_count,
                metadata={"filename": filename, "fallback": "pymupdf"},
            )
        except Exception as exc:
            logger.error("PDF fallback classification also failed for %s: %s", filename, exc)
            return ClassificationResult(
                category=FileCategory.UNKNOWN,
                metadata={"filename": filename, "error": str(exc)},
            )

    @staticmethod
    def classify(
        file_bytes: bytes,
        filename: str,
    ) -> ClassificationResult:
        """
        统一入口：根据文件名和数据内容返回分类结果。
        对 PDF 进行深度分析，其他格式仅根据扩展名判定。
        """
        base = FileClassifier.classify_by_extension(filename)

        if base == "pdf":
            return FileClassifier.classify_pdf(file_bytes, filename)

        if base == "docx":
            return ClassificationResult(
                category=FileCategory.DOCX,
                metadata={"filename": filename},
            )

        if base == "text":
            text_len = len(file_bytes.decode("utf-8", errors="replace").strip())
            return ClassificationResult(
                category=FileCategory.TEXT,
                text_length=text_len,
                metadata={"filename": filename},
            )

        if base == "image":
            return ClassificationResult(
                category=FileCategory.IMAGE,
                metadata={"filename": filename},
            )

        return ClassificationResult(
            category=FileCategory.UNKNOWN,
            metadata={"filename": filename, "detected_type": base},
        )
