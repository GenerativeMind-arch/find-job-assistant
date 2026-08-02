"""OCR 处理器 — PaddleOCR 封装，支持中英文混合识别。

处理场景：
- 扫描件 PDF 整页 OCR
- 嵌入图片单独 OCR
- 独立图片文件 OCR（.png / .jpg）
"""

from __future__ import annotations

import asyncio
import io
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from log import get_logger

logger = get_logger("agent")

# 全局 OCR 实例（延迟初始化，模型加载较重）
_ocr_instance: Optional[object] = None
_ocr_lock = asyncio.Lock()
_executor = ThreadPoolExecutor(max_workers=2)


async def _get_ocr():
    """延迟初始化 PaddleOCR 实例（首次调用时自动下载模型）。"""
    global _ocr_instance
    if _ocr_instance is not None:
        return _ocr_instance
    async with _ocr_lock:
        if _ocr_instance is not None:
            return _ocr_instance
        try:
            from paddleocr import PaddleOCR
            # use_angle_cls=True 提升竖排/旋转文字识别率
            _ocr_instance = PaddleOCR(
                use_angle_cls=True,
                lang="ch",          # 中文（含英文识别）
                use_gpu=False,       # 默认 CPU（生产环境可开启 GPU）
                show_log=False,
            )
            logger.info("PaddleOCR initialized successfully")
        except Exception as exc:
            logger.error("PaddleOCR initialization failed: %s", exc)
            raise RuntimeError(f"PaddleOCR 初始化失败：{exc}") from exc
        return _ocr_instance


async def ocr_image(image_bytes: bytes) -> str:
    """
    对单张图片执行 OCR 识别，返回拼接后的文本。

    Args:
        image_bytes: 图片字节数据（PNG/JPEG 等格式）

    Returns:
        识别出的文本（多行用 \\n 分隔）
    """
    ocr = await _get_ocr()
    try:
        # PaddleOCR 是同步 API，在线程池中执行
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            _executor,
            lambda: ocr.ocr(image_bytes, cls=True),
        )
        texts = _extract_texts_from_ocr_result(result)
        return "\n".join(texts) if texts else ""
    except Exception as exc:
        logger.error("OCR failed for image: %s", exc)
        return ""


async def ocr_image_numpy(image_array) -> str:
    """
    对 numpy 数组格式的图片执行 OCR。

    Args:
        image_array: numpy 数组 (H, W, C)，RGB 格式
    """
    ocr = await _get_ocr()
    try:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            _executor,
            lambda: ocr.ocr(image_array, cls=True),
        )
        texts = _extract_texts_from_ocr_result(result)
        return "\n".join(texts) if texts else ""
    except Exception as exc:
        logger.error("OCR failed for numpy image: %s", exc)
        return ""


async def ocr_pdf_pages(pdf_bytes: bytes, pages: Optional[list[int]] = None) -> str:
    """
    对 PDF 的指定页面进行 OCR（先转为图片再识别）。

    Args:
        pdf_bytes: PDF 文件字节数据
        pages: 要 OCR 的页码列表（0-based），None 表示全部页面

    Returns:
        所有页面的 OCR 文本拼接
    """
    try:
        from pdf2image import convert_from_bytes
        loop = asyncio.get_event_loop()

        # pdf2image 是同步 API
        images = await loop.run_in_executor(
            _executor,
            lambda: convert_from_bytes(pdf_bytes, dpi=200),
        )

        all_texts = []
        target_images = images
        if pages is not None:
            target_images = [images[p] for p in pages if 0 <= p < len(images)]

        for i, img in enumerate(target_images):
            # 将 PIL Image 转为 numpy array
            import numpy as np
            img_array = np.array(img)
            page_text = await ocr_image_numpy(img_array)
            if page_text:
                all_texts.append(f"[Page {i + 1}]\n{page_text}")

        logger.info("OCR completed for %d/%d PDF pages", len(target_images), len(images))
        return "\n\n".join(all_texts)

    except ImportError as exc:
        logger.warning("pdf2image not available: %s", exc)
        return "[OCR 失败：pdf2image 未安装，无法将 PDF 转为图片]"
    except Exception as exc:
        logger.error("PDF OCR failed: %s", exc)
        return f"[OCR 失败：{exc}]"


async def ocr_pdf_embedded_images(pdf_bytes: bytes) -> list[dict]:
    """
    提取 PDF 中的嵌入图片并逐一 OCR。

    Returns:
        [{page: int, index: int, text: str}, ...]
    """
    results = []
    try:
        import fitz  # PyMuPDF
        loop = asyncio.get_event_loop()

        doc = await loop.run_in_executor(_executor, lambda: fitz.open(stream=pdf_bytes, filetype="pdf"))
        for page_num in range(len(doc)):
            page = doc[page_num]
            image_list = page.get_images(full=True)
            for img_index, img_info in enumerate(image_list):
                xref = img_info[0]
                base_image = doc.extract_image(xref)
                image_bytes = base_image.get("image")
                if image_bytes:
                    text = await ocr_image(image_bytes)
                    if text:
                        results.append({
                            "page": page_num + 1,
                            "index": img_index,
                            "text": text,
                        })
        doc.close()
        logger.info("OCR extracted %d embedded images from PDF", len(results))
    except ImportError:
        logger.warning("PyMuPDF not available for embedded image extraction")
    except Exception as exc:
        logger.error("PDF embedded image OCR failed: %s", exc)
    return results


def _extract_texts_from_ocr_result(ocr_result: list) -> list[str]:
    """
    从 PaddleOCR 原始返回结果中提取纯文本。

    PaddleOCR 返回格式：
    [
        [   # 第 1 页
            [[bbox], (text, confidence)],
            [[bbox], (text, confidence)],
            ...
        ],
        ...  # 更多页
    ]
    """
    texts = []
    if not ocr_result or not isinstance(ocr_result, list):
        return texts

    for page_result in ocr_result:
        if page_result is None:
            continue
        for line in page_result:
            if isinstance(line, (list, tuple)) and len(line) >= 2:
                text_info = line[1]
                if isinstance(text_info, (list, tuple)) and len(text_info) >= 1:
                    texts.append(str(text_info[0]))
                elif isinstance(text_info, str):
                    texts.append(text_info)
    return texts
