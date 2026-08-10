# -*- coding: utf-8 -*-
"""RAG 端到端评估（RAGAS 指标自研实现）

对知识库结构化 Q&A 抽样样本，运行真实 RAG 链路（检索 + rerank + 生成），
计算 Faithfulness / Context Precision / Context Recall / Answer Relevancy，
输出 JSON + Markdown 报告到 data/evaluation/{timestamp}/。

用法：
  python scripts/evaluate_rag.py                    # 全量评估（每库 8 条）
  python scripts/evaluate_rag.py --per-source 5     # 每库 5 条（快速）
  python scripts/evaluate_rag.py --top-k 8          # 检索 top_k=8
  python scripts/evaluate_rag.py --outdir data/evaluation/custom

依赖：DASHSCOPE_API_KEY（embedding）、DEEPSEEK_API_KEY（LLM）
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

# Windows GBK 控制台兼容
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

_BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(_BACKEND_DIR))

_KB_DIR = Path(__file__).resolve().parent.parent / "data" / "knowledge_base"


async def main():
    ap = argparse.ArgumentParser(description="RAG 端到端评估（RAGAS 指标）")
    ap.add_argument("--per-source", type=int, default=8, help="每个知识库抽取样本数")
    ap.add_argument("--top-k", type=int, default=5, help="检索返回 Top-K")
    ap.add_argument("--outdir", default=None, help="输出目录（默认 data/evaluation/{timestamp}）")
    args = ap.parse_args()

    from evaluation.evaluator import RAGEvaluator

    evaluator = RAGEvaluator(kb_dir=_KB_DIR, samples_per_source=args.per_source, top_k=args.top_k)

    print(f"开始 RAG 评估：每库 {args.per_source} 条，Top-K={args.top_k} ...", flush=True)
    t0 = time.time()
    result = await evaluator.run()
    elapsed = time.time() - t0
    print(f"评估完成，耗时 {elapsed:.1f}s", flush=True)

    # 输出目录
    outdir = Path(args.outdir) if args.outdir else (
        Path(__file__).resolve().parent.parent / "data" / "evaluation" / time.strftime("%Y%m%d_%H%M%S")
    )
    outdir.mkdir(parents=True, exist_ok=True)

    # 结果 JSON
    result["meta"]["elapsed_sec"] = round(elapsed, 1)
    json_path = outdir / "report.json"
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    # Markdown 报告
    md = evaluator.generate_report(result)
    md_path = outdir / "report.md"
    md_path.write_text(md, encoding="utf-8")

    print(f"\n报告已生成:")
    print(f"  JSON: {json_path}")
    print(f"  Markdown: {md_path}")
    print(f"\n综合 RAGAS Score: {result['overall'].get('ragas_score', 'N/A')}")
    for m, label in [("faithfulness", "Faithfulness"), ("context_precision", "Context Precision"),
                     ("context_recall", "Context Recall"), ("answer_relevancy", "Answer Relevancy")]:
        s = result["overall"].get(m)
        print(f"  {label}: {s['mean'] if s else 'N/A'}")


if __name__ == "__main__":
    asyncio.run(main())
