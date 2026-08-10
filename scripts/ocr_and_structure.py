# -*- coding: utf-8 -*-
"""扫描件 PDF → OCR 识别 → LLM 结构化 → md/json 知识文件

OCR 引擎：DashScope `qwen-vl-ocr`（视觉语言模型，中文扫描件识别效果最佳）。
结构化：DashScope `deepseek-chat`（复用项目 LLM 配置）。

输入（data/ 下的扫描件 PDF）：
  - write_cv.pdf   → 简历制作知识（简历重塑指南）
  - llm_qa.pdf     → 大厂 LLM 技术面试题库（一问一答）

输出（data/knowledge_base/）：
  - {name}_ocr.txt      原始 OCR 文本（可人工复核）
  - {name}_structured.md 整理后的 Markdown（Q&A 或知识点）
  - {name}_structured.json 结构化 JSON（供入库脚本使用）

用法：
  python scripts/ocr_and_structure.py [--pdf data/write_cv.pdf --pdf data/llm_qa.pdf]
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import io
import json
import sys
from pathlib import Path

# Windows GBK 控制台兼容：Unicode 字符打印不报错
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import fitz  # PyMuPDF
import httpx

# ── 项目路径 ──────────────────────────────────────────────
_BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(_BACKEND_DIR))
_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_KB_DIR = _DATA_DIR / "knowledge_base"
_KB_DIR.mkdir(parents=True, exist_ok=True)

_OCR_MODEL = "qwen-vl-ocr"
_LLM_MODEL = "deepseek-chat"
_DASHSCOPE_URL = "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
_DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
_MAX_IMAGE_DIM = 1600  # 缩放图片最大边，控制 payload 与速度


def _looks_like_coordinates(text: str, threshold: int = 5) -> bool:
    """检测 qwen-vl-ocr 是否输出了坐标定位数据（如 "378,28,29,687,90"）。

    思维导图/流程图页面容易被模型输出节点坐标而非文字。
    坐标行特征：一行 5 个逗号分隔的数字（x,y,w,h,angle）。
    """
    import re
    coord_lines = 0
    for line in (text or "").splitlines():
        line = line.strip()
        if re.match(r"^\d+(,\s*\d+){4}$", line):
            coord_lines += 1
    return coord_lines >= threshold


def _get_key(name: str) -> str:
    """从 config_loader / 环境变量读取密钥。"""
    try:
        from config_loader import get_embedding_config
        if name == "DASHSCOPE_API_KEY":
            k = get_embedding_config().get("api_key") or ""
            if k:
                return k
        from config_loader import get_llm_config
        if name == "DEEPSEEK_API_KEY":
            k = get_llm_config().get("api_key") or ""
            if k:
                return k
    except Exception:
        pass
    import os
    return os.environ.get(name, "")


def _downscale(image_bytes: bytes, max_dim: int = _MAX_IMAGE_DIM) -> bytes:
    """将大图缩放到 max_dim 以内（等比例），减小 API payload。"""
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


async def _dashscope_retry(payload: dict, api_key: str, timeout: float = 90.0, retries: int = 3) -> str:
    """调用 DashScope 多模态接口（含重试），返回输出文本。"""
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    last_err = ""
    for attempt in range(1, retries + 1):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(_DASHSCOPE_URL, json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()
            choices = data.get("output", {}).get("choices", [])
            if not choices:
                return f"[OCR 无输出] {str(data)[:200]}"
            content = choices[0].get("message", {}).get("content", [])
            if isinstance(content, str):
                return content
            return "".join(c.get("text", "") for c in content if isinstance(c, dict) and c.get("text"))
        except Exception as e:
            last_err = str(e)
            if attempt < retries:
                await asyncio.sleep(2 * attempt)
    return f"[OCR 失败: {last_err[:200]}]"


async def ocr_pdf(pdf_path: Path) -> dict:
    """逐页 OCR 一个 PDF，返回 {page_num: text}。"""
    doc = fitz.open(str(pdf_path))
    api_key = _get_key("DASHSCOPE_API_KEY")
    if not api_key:
        raise RuntimeError("DASHSCOPE_API_KEY 未配置")

    page_texts: dict[int, str] = {}
    total = len(doc)
    for pageno in range(total):
        page = doc[pageno]
        images = page.get_images(full=True)
        if not images:
            page_texts[pageno + 1] = ""
            continue
        base_img = doc.extract_image(images[0][0])
        raw = base_img.get("image")
        if not raw:
            page_texts[pageno + 1] = ""
            continue
        img_bytes = _downscale(raw)
        data_uri = f"data:image/{base_img.get('ext', 'jpeg')};base64," + base64.b64encode(img_bytes).decode()
        payload = {
            "model": _OCR_MODEL,
            "input": {"messages": [{"role": "user", "content": [
                {"image": data_uri},
                {"text": "请完整识别这张图片中的所有文字，严格按原始阅读顺序输出，"
                          "保留所有内容，不要遗漏、不要改写、不要添加解释。"
                          "如果内容是思维导图/流程图/图表，请输出图中各节点的文字内容，"
                          "按逻辑顺序整理为普通文本；不要输出任何坐标、边框、数字编码或定位信息。"},
            ]}]},
        }
        print(f"  [OCR] {pdf_path.name} 第 {pageno + 1}/{total} 页...", flush=True)
        page_texts[pageno + 1] = await _dashscope_retry(payload, api_key)
        # 若仍输出坐标数据（形如 "x,y,w,h,90"），重试一次强制文字
        if _looks_like_coordinates(page_texts[pageno + 1]):
            print(f"    检测到坐标输出，重试强制文字模式...", flush=True)
            payload["input"]["messages"][0]["content"][1]["text"] = (
                "请只输出这张图中的文字内容（思维导图/流程图节点的文字），"
                "按主题整理为清晰的要点列表。严禁输出坐标、数字编码或定位框信息。"
            )
            page_texts[pageno + 1] = await _dashscope_retry(payload, api_key)
        await asyncio.sleep(0.5)  # 限速，避免触发风控

    doc.close()
    return page_texts


async def structure_with_llm(name: str, ocr_full: str, api_key: str) -> dict:
    """调用 deepseek 将 OCR 全文结构化。返回结构化数据 dict。"""
    if name == "llm_qa":
        system = "你是面试题结构化助手，只输出严格 JSON。"
        user = f"""以下是 OCR 识别出的一份「大模型技术面试题」文档全文：
---
{ocr_full}
---

请整理为 Q&A 数组，每项包含：
- question: 面试问题（以疑问句或概念名呈现，通常字号较大）
- answer: 完整答案
- category: 技术方向（如 LLM基础 / 微调 / RAG / 推理优化 / Agent / 部署 等）
- difficulty: easy / medium / hard

要求：
1. 严格一问一答，不要合并不同的问题，也不要拆分同一问答
2. 若某个问题在 OCR 文本中没有明确答案，answer 填"（原文无答案，见上文）"
3. 保留所有技术术语、数字和关键细节
4. 只输出 JSON：{{"qa_pairs": [...]}}
"""
    else:
        system = "你是简历制作指导文档结构化助手，只输出严格 JSON。"
        user = f"""以下是 OCR 识别出的一份「简历制作指南」文档全文：
---
{ocr_full}
---

这份文档是层级结构：大标题（一、二、三...）→ 编号题号（1、2、3...）→ 具体内容/答案。
请保留这个层级，整理为 sections 数组，每项包含大标题及其下所有编号题：

{{
  "sections": [
    {{
      "title": "一、简历重塑",
      "items": [
        {{
          "number": 1,
          "title": "教育背景：不拼绩点，拼相关性",
          "content": "具体指导内容（含要点）",
          "examples": "示例（若有，保留原文示例，如 STAR 案例、具体分数/数字）",
          "keywords": ["标签1", "标签2"]
        }}
      ]
    }}
  ]
}}

要求：
1. 严格按原文的大标题分组（一、二、三...），每个编号题号作为一个 item（含 number/title/content）
2. 大标题可能是"一、""二、""三、"或"1、""2、"等形态，按原文分组
3. 无法归入编号大标题的独立内容（如表格"经历替代方案"、"面试简历注意事项"、"立即删除的5大简历毒药"），
   单独作为一个 section（title 用原文标题，items 里每个要点作为一个 item）
4. 保留所有示例和具体数字
5. 只输出 JSON：{{"sections": [...]}}
"""
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {
        "model": _LLM_MODEL,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
    }
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=180) as client:
                resp = await client.post(_DEEPSEEK_URL, json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()
            content = data["choices"][0]["message"]["content"]
            # 去掉可能的 markdown 代码块包裹
            content = content.strip()
            if content.startswith("```"):
                content = content.split("\n", 1)[1].rsplit("```", 1)[0]
            return json.loads(content)
        except Exception as e:
            print(f"  结构化第 {attempt + 1} 次失败: {str(e)[:120]}", flush=True)
            if attempt < 2:
                await asyncio.sleep(3 * (attempt + 1))
    return {}


def _format_md(name: str, structured: dict) -> str:
    """将结构化 JSON 转为可读的 Markdown。"""
    lines = [f"# {'简历制作知识库' if name == 'write_cv' else '大厂 LLM 面试题库'}\n"]
    if name == "llm_qa":
        for i, qa in enumerate(structured.get("qa_pairs", []), 1):
            q = qa.get("question", "")
            a = qa.get("answer", "")
            cat = qa.get("category", "")
            diff = qa.get("difficulty", "")
            lines.append(f"## Q{i}. {q}")
            if cat or diff:
                lines.append(f"**分类**: {cat} | **难度**: {diff}")
            lines.append(f"\n**A**: {a}\n")
    else:
        # write_cv：层级结构 sections → items
        sections = structured.get("sections")
        if sections:
            for sec in sections:
                sec_title = sec.get("title", "")
                lines.append(f"# {sec_title}")
                items = sec.get("items", [])
                for item in items:
                    num = item.get("number", "")
                    title = item.get("title", "")
                    content = item.get("content", "")
                    examples = item.get("examples", "")
                    kws = ", ".join(item.get("keywords", []))
                    header = f"## {num}. {title}" if num else f"## {title}"
                    lines.append(header)
                    lines.append(f"**要点**: {content}")
                    if examples:
                        lines.append(f"\n**示例**:\n{examples}")
                    if kws:
                        lines.append(f"\n**标签**: {kws}")
                    lines.append("")
                lines.append("")
        else:
            # 兼容旧扁平结构
            for i, kp in enumerate(structured.get("knowledge_points", []), 1):
                topic = kp.get("topic", "")
                guidance = kp.get("guidance", "")
                examples = kp.get("examples", "")
                kws = ", ".join(kp.get("keywords", []))
                lines.append(f"## {i}. {topic}")
                lines.append(f"**要点**: {guidance}")
                if examples:
                    lines.append(f"\n**示例**:\n{examples}")
                if kws:
                    lines.append(f"\n**标签**: {kws}")
            lines.append("")
    return "\n".join(lines)


async def process_pdf(pdf_path: Path):
    """处理单个 PDF：OCR → 结构化 → 落盘 md/json/txt。"""
    name = pdf_path.stem  # write_cv / llm_qa
    _KB_DIR.mkdir(parents=True, exist_ok=True)
    print(f"\n=== 处理 {pdf_path.name} ===", flush=True)

    # 1. OCR
    page_texts = await ocr_pdf(pdf_path)
    ocr_full = "\n\n".join(f"[第{p}页]\n{t}" for p, t in sorted(page_texts.items()) if t.strip())
    if not ocr_full.strip():
        print(f"  [警告] {name} OCR 结果为空！", flush=True)
        return

    ( _KB_DIR / f"{name}_ocr.txt").write_text(ocr_full, encoding="utf-8")
    print(f"  OCR 完成，总字符数: {len(ocr_full)}", flush=True)

    # 2. 结构化
    api_key = _get_key("DEEPSEEK_API_KEY")
    if not api_key:
        raise RuntimeError("DEEPSEEK_API_KEY 未配置")
    structured = await structure_with_llm(name, ocr_full, api_key)
    if not structured:
        print(f"  [警告] {name} 结构化失败，保留 OCR 原文。", flush=True)
        return

    # 3. 落盘 md + json
    md_text = _format_md(name, structured)
    ( _KB_DIR / f"{name}_structured.md").write_text(md_text, encoding="utf-8")
    ( _KB_DIR / f"{name}_structured.json").write_text(
        json.dumps(structured, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if "qa_pairs" in structured:
        count = len(structured["qa_pairs"])
    elif "sections" in structured:
        count = sum(len(s.get("items", [])) for s in structured["sections"])
    else:
        count = len(structured.get("knowledge_points", []))
    print(f"  [OK] 已生成 {name}_structured.md / .json（{count} 条）", flush=True)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", action="append", default=[], help="要处理的 PDF 路径（可多个）")
    args = ap.parse_args()

    if args.pdf:
        pdfs = [Path(p) for p in args.pdf]
    else:
        pdfs = [_KB_DIR / "write_cv.pdf", _KB_DIR / "llm_qa.pdf"]

    for p in pdfs:
        if not p.exists():
            print(f"PDF 不存在: {p}", flush=True)
            continue
        await process_pdf(p)


if __name__ == "__main__":
    asyncio.run(main())
