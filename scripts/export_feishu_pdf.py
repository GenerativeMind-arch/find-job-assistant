# -*- coding: utf-8 -*-
"""飞书文档 → PDF 导出脚本（Playwright + Chromium）

针对「外部共享、无导出权限」的飞书文档：无法用飞书内置导出，改用浏览器
加载文档 → 提取正文结构化块 → 重建干净 HTML → 导出为多页 PDF。

为什么用「重建」而不是直接 page.pdf()：
  飞书会在 PDF 捕获瞬间注入「没有权限」水印（SVG 矢量层），DOM 无法删除；
  且飞书的 print CSS 会隐藏正文。重建页面后这些机制全部失效。

两阶段设计：
  Phase 1（有头）: 打开飞书文档，等待用户手动登录（扫码/账号），登录态
                   持久化到本目录 .feishu_profile/（含 Cookie）。
  Phase 2（无头）: 复用同一 profile（Cookie 已生效），无头打开文档，
                   滚动加载 → 提取正文 → 重建 → page.pdf() 分页导出。

用法：
  python scripts/export_feishu_pdf.py <飞书文档URL> [--output out.pdf]

依赖：pip install playwright && playwright install chromium
"""

from __future__ import annotations

import argparse
import html as html_mod
import re
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

PROFILE_DIR = Path(__file__).resolve().parent / ".feishu_profile"
DEFAULT_OUTPUT = Path(__file__).resolve().parent / "feishu_doc.pdf"

# 常见飞书文档正文容器（用于判断文档是否已加载）
CONTENT_SELECTORS = [
    ".garr-container-docx",
    ".render-unit-container",
    ".docx-container",
    ".wiki-content",
    "[data-page-id]",
]

# 重建页面的干净样式
_CLEAN_CSS = """
body { font-family: "Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC", sans-serif;
       max-width: 760px; margin: 0 auto; padding: 36px 30px 8px;
       line-height: 1.75; color: #24292f; font-size: 15px; }
h1 { font-size: 26px; text-align: center; margin: 0 0 4px; }
.meta { text-align: center; color: #8b949e; font-size: 13px; margin-bottom: 20px; }
h2 { font-size: 20px; margin: 1.5em 0 .5em; padding-left: 10px;
     border-left: 4px solid #3370ff; }
h3 { font-size: 17px; margin: 1.3em 0 .4em; }
p { margin: .7em 0; }
ul, ol { padding-left: 1.7em; margin: .6em 0; }
li { margin: .35em 0; }
blockquote { border-left: 4px solid #d0d7de; margin: 1em 0; padding: .3em 1em;
             color: #57606a; background: #f6f8fa; border-radius: 4px; }
pre, code { background: #f6f8fa; border-radius: 6px; font-size: 13px; }
pre { padding: 12px 14px; overflow-x: auto; line-height: 1.6; }
code { padding: 2px 5px; }
table { border-collapse: collapse; margin: 1em 0; width: 100%; }
th, td { border: 1px solid #d0d7de; padding: 6px 12px; font-size: 14px; }
th { background: #f6f8fa; }
img { max-width: 100%; }
a { color: #3370ff; text-decoration: none; word-break: break-all; }
.bookmark { border: 1px solid #d0d7de; border-radius: 8px; padding: 8px 14px;
            margin: .8em 0; background: #fff; }
.bookmark a { font-size: 13px; color: #57606a; }
.bookmark .bm-title { display: block; font-size: 14px; color: #24292f; }
.calout { background: #f0f6ff; border: 1px solid #cfe0ff; border-radius: 8px;
          padding: 10px 14px; margin: .8em 0; }
hr { border: 0; border-top: 1px solid #eaeef2; margin: 1.4em 0; }
"""

# 提取正文块的 JS：返回按 y 排序的「叶子块」列表。
# 飞书用虚拟化渲染 + 嵌套 block，必须只取叶子块（自身不再嵌套 docx-block），
# 否则外层 page/容器块会吞掉内部所有细粒度内容。
_EXTRACT_JS = """
() => {
  const root = document.querySelector('.garr-container-docx')
             || document.querySelector('.suite-body')
             || document.querySelector('[class*="docx-"]');
  const blocks = [];
  if (root) {
    const all = root.querySelectorAll('[class*="docx-"][class*="-block"]');
    for (const b of all) {
      const r = b.getBoundingClientRect();
      if (r.x < 100) continue;   // 排除左侧目录面板（wiki 文档树）
      // 仅保留叶子块：自身不嵌套 docx-block
      if (b.querySelector('[class*="docx-"][class*="-block"]')) continue;
      const t = (b.innerText || '').trim();
      if (!t) continue;
      const cls = String(b.className || '');
      const m = cls.match(/docx-([a-z-]+)-block/);
      blocks.push({ type: m ? m[1] : 'text', y: Math.round(r.top), text: t });
    }
  }
  blocks.sort((a, b) => a.y - b.y);
  return blocks;
}
"""


def _p(msg: str) -> None:
    """ASCII-safe 打印（Windows GBK 控制台兼容）。"""
    safe = msg.encode("ascii", errors="replace").decode("ascii")
    print(safe, flush=True)


def _page_has_content(page) -> bool:
    try:
        for sel in CONTENT_SELECTORS:
            if page.locator(sel).count() > 0:
                return True
        text = page.evaluate("document.body ? document.body.innerText : ''")
        return len(text.strip()) > 300
    except Exception:
        return False


def _wait_for_content(page, timeout_s: int = 120) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if _page_has_content(page):
            return True
        page.wait_for_timeout(2000)
    return False


def _scroll_to_load_all(page, max_scrolls: int = 80) -> None:
    prev = -1
    for _ in range(max_scrolls):
        try:
            height = page.evaluate("document.documentElement.scrollHeight")
        except Exception:
            break
        if height == prev:
            break
        prev = height
        page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
        page.wait_for_timeout(600)
    page.evaluate("window.scrollTo(0, 0)")
    page.wait_for_timeout(800)


def _doc_title(page) -> str:
    try:
        t = page.title()
        t = t.replace(" - 飞书云文档", "").replace(" - 飞书", "").strip()
        if t:
            return t[:80]
    except Exception:
        pass
    return "飞书文档导出"


# ── 正文块 → HTML ────────────────────────────────────────────────

def _clean_text(text: str) -> str:
    """清理飞书文本：去零宽字符、项目符号前缀、空白折叠。"""
    text = text.replace("​", "").replace("‌", "").replace("‍", "")
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return ""
    # 去掉列表项目符号（• / 数字.）
    first = lines[0]
    first = re.sub(r"^[•·\-]\s*", "", first)
    first = re.sub(r"^\d+[.、)]\s*", "", first)
    lines[0] = first
    return " ".join(lines)


def _bookmark_html(text: str) -> str:
    """链接卡片块 → 提取 URL + 标题。"""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return ""
    url = ""
    rest = lines
    if lines[0].startswith("http"):
        url = lines[0]
        rest = lines[1:]
    elif len(lines) >= 2 and lines[1].startswith("http"):
        url = lines[1]
        rest = [lines[0]] + lines[2:]
    title = html_mod.escape(" ".join(rest)[:120] or url)
    if url:
        return f'<div class="bookmark"><span class="bm-title">{title}</span><a href="{html_mod.escape(url)}">{html_mod.escape(url)}</a></div>'
    return f'<div class="bookmark"><span class="bm-title">{title}</span></div>'


def blocks_to_html(blocks: list[dict]) -> str:
    """按类型把正文块渲染为干净 HTML。"""
    parts: list[str] = []
    in_ul = False

    def close_list():
        nonlocal in_ul
        if in_ul:
            parts.append("</ul>")
            in_ul = False

    for b in blocks:
        btype = b.get("type", "text")
        text = _clean_text(b.get("text", ""))

        if btype == "heading":
            close_list()
            if text:
                parts.append(f"<h2>{html_mod.escape(text)}</h2>")
        elif btype == "bullet":
            if not in_ul:
                parts.append("<ul>")
                in_ul = True
            if text:
                parts.append(f"<li>{html_mod.escape(text)}</li>")
        elif btype in ("ordered-list", "todo"):
            close_list()
            if text:
                parts.append(f"<p>· {html_mod.escape(text)}</p>")
        elif btype == "quote":
            close_list()
            if text:
                parts.append(f"<blockquote>{html_mod.escape(text)}</blockquote>")
        elif btype == "code":
            close_list()
            if text:
                parts.append(f"<pre>{html_mod.escape(text)}</pre>")
        elif btype == "table":
            close_list()
            # 表格：按行拆分文本，近似渲染为表格
            if text:
                rows = [ln.split("\t") for ln in text.splitlines() if ln.strip()]
                if rows:
                    head = "".join(f"<th>{html_mod.escape(c)}</th>" for c in rows[0])
                    body = ""
                    for row in rows[1:]:
                        body += "<tr>" + "".join(f"<td>{html_mod.escape(c)}</td>" for c in row) + "</tr>"
                    parts.append(f"<table><tr>{head}</tr>{body}</table>")
        elif btype == "bookmark":
            close_list()
            parts.append(_bookmark_html(b.get("text", "")))
        elif btype == "divider":
            close_list()
            parts.append("<hr>")
        elif btype == "page":
            # 文档标题区，跳过（标题取自 document.title）
            continue
        elif btype == "image":
            continue  # 图片以原样无法重建，跳过（链接块已保留文本）
        else:  # text 及其它
            if text:
                close_list()
                parts.append(f"<p>{html_mod.escape(text)}</p>")

    close_list()
    return "\n".join(parts)


# ── Phase 1：有头登录 ──────────────────────────────────────────────

def phase_login(url: str) -> None:
    _p("[1/2] 打开有头浏览器，请在窗口内完成飞书登录（扫码/账号）...")
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = ctx.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=60000)

        if _wait_for_content(page, timeout_s=30):
            _p("检测到文档已加载（可能已有登录态），直接进入导出阶段。")
        else:
            _p("尚未检测到文档内容，请在弹出的浏览器中完成登录。")
            _p("登录完成后文档会自动加载，无需任何操作，脚本会自动继续...")
            ok = _wait_for_content(page, timeout_s=300)
            if not ok:
                _p("等待超时：5 分钟内未检测到文档内容。")
                _p("请确认：1) 你登录的账号对该文档有查看权限；2) 链接可访问。")
                _p("按回车键退出（登录态已保存，可直接重跑本脚本跳过登录）。")
                try:
                    input()
                except EOFError:
                    pass
                ctx.close()
                sys.exit(1)
            _p("登录成功，文档已加载。")
        ctx.close()
    _p("Phase 1 完成，登录态已保存。")


# ── Phase 2：无头提取 + 重建导出 ──────────────────────────────────

def phase_export(url: str, out_path: Path) -> None:
    _p("[2/2] 无头模式打开文档、提取正文并导出 PDF...")
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=True,
        )
        page = ctx.new_page()

        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
        except Exception as exc:
            _p(f"打开文档失败: {exc}")
            ctx.close()
            sys.exit(1)

        if not _wait_for_content(page, timeout_s=60):
            _p("无头模式下文档未加载（可能登录态失效）。请先运行带登录的完整脚本。")
            ctx.close()
            sys.exit(1)

        _p("滚动加载全部内容...")
        _scroll_to_load_all(page)

        title = _doc_title(page)
        blocks = page.evaluate(_EXTRACT_JS)
        _p(f"标题: {title}  |  提取到 {len(blocks)} 个内容块")

        body_html = blocks_to_html(blocks)
        if not body_html.strip():
            _p("未提取到正文内容，导出失败。")
            ctx.close()
            sys.exit(1)

        clean_html = (
            "<!DOCTYPE html><html lang='zh'><head><meta charset='utf-8'>"
            f"<title>{html_mod.escape(title)}</title><style>{_CLEAN_CSS}</style></head><body>"
            f"<h1>{html_mod.escape(title)}</h1>"
            f"<div class='meta'>飞书文档 · 自动导出 PDF</div>"
            f"{body_html}"
            "</body></html>"
        )

        # 重建为空白页，注入干净 HTML（此时页面无飞书 JS，无水印、无 UI）
        page.set_content(clean_html)
        page.emulate_media(media="screen")
        page.wait_for_timeout(500)

        _p("导出中...")
        page.pdf(
            path=str(out_path),
            format="A4",
            print_background=True,
            prefer_css_page_size=False,
            margin={"top": "14mm", "bottom": "10mm", "left": "14mm", "right": "14mm"},
        )
        ctx.close()

    _p(f"PDF 已生成: {out_path} ({out_path.stat().st_size / 1024:.1f} KB)")


def main() -> None:
    ap = argparse.ArgumentParser(description="飞书文档导出 PDF（Playwright 提取重建）")
    ap.add_argument("url", help="飞书文档 / Wiki 链接")
    ap.add_argument("--output", "-o", default=str(DEFAULT_OUTPUT), help="输出 PDF 路径")
    args = ap.parse_args()

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)

    phase_login(args.url)
    phase_export(args.url, out)


if __name__ == "__main__":
    main()
