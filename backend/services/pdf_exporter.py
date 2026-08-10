"""HTML → PDF 导出服务 — 使用 Playwright（Chromium）渲染。

WeasyPrint 在 Windows 上缺 GTK3 原生库无法使用；Playwright Chromium 项目已验证
可用（scripts/export_feishu_pdf.py），且对 HTML/CSS 渲染效果最佳。
同步实现，调用方用 asyncio.to_thread 包装避免阻塞事件循环。
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from log import get_logger

logger = get_logger("agent")


def html_to_pdf_bytes(html: str, format: str = "A4", margin: str = "12mm") -> bytes:
    """将 HTML 字符串渲染为 PDF 字节流。

    Args:
        html: 完整 HTML 文档
        format: 纸张格式（A4 / Letter 等）
        margin: 页边距

    Returns:
        PDF 字节流
    """
    import asyncio
    import sys

    # uvicorn reload worker 默认用 SelectorEventLoop，而 Playwright 启动
    # driver 子进程需要 ProactorEventLoop（与 Boss 模块相同的问题）
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

    from playwright.sync_api import sync_playwright

    with tempfile.TemporaryDirectory() as tmpdir:
        html_path = Path(tmpdir) / "resume.html"
        html_path.write_text(html, encoding="utf-8")

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(f"file://{html_path}", wait_until="networkidle", timeout=30000)
            # 确保打印媒体，触发模板中的 @media print 样式
            page.emulate_media(media="print")
            pdf_bytes = page.pdf(
                format=format,
                print_background=True,
                margin={"top": margin, "bottom": margin, "left": margin, "right": margin},
            )
            browser.close()
    logger.info("HTML rendered to PDF: %d bytes", len(pdf_bytes))
    return pdf_bytes


async def ahtml_to_pdf_bytes(html: str, format: str = "A4", margin: str = "12mm") -> bytes:
    """异步包装，在独立线程执行 Playwright 渲染，避免阻塞事件循环。"""
    return await asyncio.to_thread(html_to_pdf_bytes, html, format, margin)
