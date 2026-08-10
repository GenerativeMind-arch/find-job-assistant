"""qwen-vl-ocr 视觉 OCR 服务 — 图片 / 扫描件 PDF 文字识别。

使用 DashScope `qwen-vl-ocr` 视觉语言模型（中文识别质量高，支持版面/表格）。
与知识库处理链路（scripts/ocr_and_structure.py）使用同一模型，保持一致性。

同步实现（httpx sync）：用于简历上传等低频、单次场景，避免跨 async 边界。
"""

from __future__ import annotations

import base64
import io
import os

import httpx

from log import get_logger

logger = get_logger("agent")

_OCR_MODEL = "qwen-vl-ocr"
_DASHSCOPE_URL = "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
_MAX_IMAGE_DIM = 1600  # 缩放图片最大边，控制 payload


def _get_dashscope_key() -> str:
    """从 config / 环境变量读取 DashScope API Key。"""
    try:
        from config_loader import get_embedding_config
        key = get_embedding_config().get("api_key") or ""
        if key:
            return key
    except Exception:
        pass
    return os.environ.get("DASHSCOPE_API_KEY", "")


def _downscale(image_bytes: bytes, max_dim: int = _MAX_IMAGE_DIM) -> bytes:
    """等比缩放大图，减小 API payload。"""
    from PIL import Image
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    w, h = img.size
    if max(w, h) <= max_dim:
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=90)
        return buf.getvalue()
    ratio = max_dim / max(w, h)
    img = img.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


def _call_dashscope(image_bytes: bytes, timeout: float = 60.0) -> str:
    """单张图片调用 qwen-vl-ocr，返回识别文本。"""
    api_key = _get_dashscope_key()
    if not api_key:
        raise RuntimeError("DASHSCOPE_API_KEY 未配置，无法进行图片/扫描件识别")

    img_bytes = _downscale(image_bytes)
    data_uri = "data:image/jpeg;base64," + base64.b64encode(img_bytes).decode()
    payload = {
        "model": _OCR_MODEL,
        "input": {"messages": [{"role": "user", "content": [
            {"image": data_uri},
            {"text": "请完整识别这张图片中的所有文字，严格按原始阅读顺序输出，"
                      "保留所有内容（包括姓名、联系方式、教育经历、工作经历、技能等），"
                      "不要遗漏、不要改写、不要添加解释。"
                      "如果内容是思维导图/表格，请按逻辑顺序整理为文字，不要输出坐标。"},
        ]}]},
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    last_err = ""
    for attempt in range(3):
        try:
            resp = httpx.post(_DASHSCOPE_URL, json=payload, headers=headers, timeout=timeout)
            resp.raise_for_status()
            data = resp.json()
            choices = data.get("output", {}).get("choices", [])
            if not choices:
                return ""
            content = choices[0].get("message", {}).get("content", [])
            if isinstance(content, str):
                return content.strip()
            return "".join(c.get("text", "") for c in content if isinstance(c, dict) and c.get("text")).strip()
        except Exception as exc:
            last_err = str(exc)
            if attempt < 2:
                import time
                time.sleep(2 * (attempt + 1))
    logger.error("qwen-vl-ocr call failed: %s", last_err)
    return ""


def qwen_vl_ocr_image(image_bytes: bytes, timeout: float = 60.0) -> str:
    """识别单张图片，返回文本。"""
    text = _call_dashscope(image_bytes, timeout=timeout)
    if text:
        logger.info("qwen-vl-ocr image recognized: %d chars", len(text))
    return text


def qwen_vl_ocr_pdf(pdf_bytes: bytes, timeout: float = 90.0) -> str:
    """扫描件 PDF 逐页 OCR，返回拼接文本（每页标注页码）。"""
    try:
        import fitz  # PyMuPDF
    except ImportError:
        logger.error("PyMuPDF not available for scanned PDF OCR")
        return ""

    all_texts: list[str] = []
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        for pageno in range(len(doc)):
            page = doc[pageno]
            images = page.get_images(full=True)
            for img_info in images:
                xref = img_info[0]
                base_img = doc.extract_image(xref)
                raw = base_img.get("image")
                if not raw:
                    continue
                text = _call_dashscope(raw, timeout=timeout)
                if text:
                    all_texts.append(f"[第{pageno + 1}页]\n{text}")
        doc.close()
    except Exception as exc:
        logger.error("Scanned PDF OCR failed: %s", exc)
    return "\n\n".join(all_texts)
