"""RAG 评估主控 — 加载测试集 → 真实检索 → LLM 生成 → RAGAS 指标 → 报告。

使用项目真实 RAG 链路（RAGRetriever 向量检索 + gte-rerank-v2 精排），
对知识库结构化 Q&A 抽样样本进行端到端评估。
"""

from __future__ import annotations

import asyncio
import json
import random
import statistics
from pathlib import Path
from typing import Any, Optional

from log import get_logger
from models.llm import get_llm, acall_llm

from .ragas_metrics import RAGASMetrics

logger = get_logger("eval")

# 知识库结构化文件 → collection 映射
SOURCE_MAP = [
    ("llm_qa_structured.json", "llm_interview_knowledge"),
    ("write_cv_structured.json", "cv_knowledge"),
]


def _load_structured_samples(kb_dir: Path, filename: str, count: int) -> list[dict]:
    """从结构化 JSON 加载 Q&A 样本。"""
    path = kb_dir / filename
    if not path.exists():
        logger.warning("测试数据文件不存在: %s", path)
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    samples: list[dict] = []
    if "qa_pairs" in data:
        for qa in data["qa_pairs"]:
            q = (qa.get("question") or "").strip()
            a = (qa.get("answer") or "").strip()
            if q and a:
                samples.append({"question": q, "answer": a, "category": qa.get("category", ""),
                                "collection": None})
    elif "sections" in data:
        for sec in data["sections"]:
            sec_title = sec.get("title", "")
            for item in sec.get("items", []):
                title = (item.get("title") or "").strip()
                content = (item.get("content") or "").strip()
                if title and content:
                    samples.append({
                        "question": title,
                        "answer": content,
                        "category": sec_title,
                        "collection": None,
                    })
    random.seed(42)
    random.shuffle(samples)
    return samples[:count]


class RAGEvaluator:
    """RAG 端到端评估器。"""

    def __init__(self, kb_dir: Path, samples_per_source: int = 8, top_k: int = 5):
        self.kb_dir = kb_dir
        self.samples_per_source = samples_per_source
        self.top_k = top_k
        self.metrics = RAGASMetrics()
        self._llm = None

    @property
    def llm(self):
        if self._llm is None:
            self._llm = get_llm()
        return self._llm

    # ── 测试集 ──

    def load_test_set(self) -> list[dict]:
        """加载测试集：从两个知识库各抽 samples_per_source 条。"""
        test_set: list[dict] = []
        for filename, collection in SOURCE_MAP:
            samples = _load_structured_samples(self.kb_dir, filename, self.samples_per_source)
            for s in samples:
                s["collection"] = collection
                s["source_file"] = filename
            test_set.extend(samples)
            logger.info("从 %s 加载 %d 条测试样本", filename, len(samples))
        return test_set

    # ── 检索与生成 ──

    async def run_retrieval(self, question: str, collection: str) -> list[str]:
        """调用项目真实 RAG 检索链路，返回上下文文本列表。"""
        from services.rag_retriever import RAGRetriever
        try:
            results = await RAGRetriever.retrieve(
                query=question, top_k=self.top_k * 2, top_n=self.top_k,
                collection_name=collection,
            )
            return [r.get("text", "") for r in results if r.get("text")]
        except Exception as exc:
            logger.error("检索失败 %s: %s", collection, exc)
            return []

    async def generate_answer(self, question: str, contexts: list[str]) -> str:
        """基于检索上下文生成答案。"""
        if not contexts:
            return "（检索无结果，无法回答）"
        ctx_text = "\n\n".join(f"[片段{i + 1}]\n{c[:800]}" for i, c in enumerate(contexts))
        system = "你是一个严谨的问答助手。请仅根据提供的知识片段回答问题，不要编造知识片段中没有的内容。"
        user = f"知识片段：\n{ctx_text}\n\n问题：{question}\n\n请给出准确、完整的回答。"
        try:
            return await acall_llm(system, user)
        except Exception as exc:
            logger.error("答案生成失败: %s", exc)
            return ""

    # ── 单条与全量评估 ──

    async def evaluate_sample(self, sample: dict) -> dict:
        """单条测试：检索 → 生成 → 四指标。"""
        q = sample["question"]
        contexts = await self.run_retrieval(q, sample["collection"])
        answer = await self.generate_answer(q, contexts)
        scores = await self.metrics.evaluate(
            question=q, answer=answer, contexts=contexts, ground_truth=sample["answer"],
        )
        return {
            "question": q,
            "collection": sample["collection"],
            "category": sample.get("category", ""),
            "ground_truth": sample["answer"][:200],
            "answer": answer[:300],
            "context_count": len(contexts),
            **scores,
        }

    async def run(self) -> dict:
        """全量评估，返回结构化结果。"""
        test_set = self.load_test_set()
        if not test_set:
            raise RuntimeError("测试集为空，请先构建知识库（scripts/build_knowledge_base.py）")

        results = []
        for i, sample in enumerate(test_set, 1):
            logger.info("评估样本 %d/%d: %s", i, len(test_set), sample["question"][:40])
            try:
                results.append(await self.evaluate_sample(sample))
            except Exception as exc:
                logger.error("样本 %d 评估失败: %s", i, exc)
                results.append({"question": sample["question"], "error": str(exc)})

        return self._aggregate(results)

    # ── 汇总与报告 ──

    def _aggregate(self, results: list[dict]) -> dict:
        """聚合各条结果，计算整体与分 collection 统计。"""
        metrics = ["faithfulness", "context_precision", "context_recall", "answer_relevancy"]

        def _scores(rows: list[dict]) -> dict:
            out = {}
            for m in metrics:
                vals = [r[m]["score"] for r in rows if isinstance(r.get(m), dict)]
                if vals:
                    out[m] = {
                        "mean": round(statistics.mean(vals), 3),
                        "median": round(statistics.median(vals), 3),
                        "min": round(min(vals), 3),
                        "max": round(max(vals), 3),
                    }
                else:
                    out[m] = None
            ragas = [r["ragas_score"] for r in rows if "ragas_score" in r]
            out["ragas_score"] = round(statistics.mean(ragas), 3) if ragas else 0.0
            return out

        overall = _scores([r for r in results if "ragas_score" in r])

        by_collection = {}
        for r in results:
            if "ragas_score" not in r:
                continue
            col = r["collection"]
            by_collection.setdefault(col, []).append(r)
        col_stats = {col: _scores(rows) for col, rows in by_collection.items()}

        total_uncertain = sum(
            sum(r[m].get("uncertain", 0) for m in metrics if isinstance(r.get(m), dict))
            for r in results if "ragas_score" in r
        )
        return {
            "samples": results,
            "overall": overall,
            "by_collection": col_stats,
            "meta": {
                "total_samples": len([r for r in results if "ragas_score" in r]),
                "total_uncertain_judgments": total_uncertain,
                "top_k": self.top_k,
            },
        }

    def generate_report(self, agg: dict) -> str:
        """生成 Markdown 评估报告。"""
        overall = agg["overall"]
        lines = ["# RAG 评估报告\n"]
        lines.append(f"- 样本数: {agg['meta']['total_samples']}")
        lines.append(f"- 检索 Top-K: {agg['meta']['top_k']}")
        lines.append(f"- 不确定判定数: {agg['meta']['total_uncertain_judgments']}")
        lines.append("")

        lines.append("## 综合评分\n")
        lines.append("| 指标 | 平均分 | 中位数 | 最低 | 最高 |")
        lines.append("|------|--------|--------|------|------|")
        labels = {
            "faithfulness": "Faithfulness 忠实性",
            "context_precision": "Context Precision 精确性",
            "context_recall": "Context Recall 召回率",
            "answer_relevancy": "Answer Relevancy 相关性",
        }
        for m, label in labels.items():
            s = overall.get(m)
            if s:
                lines.append(f"| {label} | {s['mean']} | {s['median']} | {s['min']} | {s['max']} |")
        lines.append(f"| **RAGAS Score** | **{overall.get('ragas_score', 0)}** | - | - | - |\n")

        lines.append("## 分 Collection 对比\n")
        lines.append("| Collection | 忠实性 | 精确性 | 召回率 | 相关性 | RAGAS |")
        lines.append("|-----------|--------|--------|--------|--------|-------|")
        for col, s in agg["by_collection"].items():
            lines.append(
                f"| {col} | {s['faithfulness']['mean'] if s['faithfulness'] else '-'} | "
                f"{s['context_precision']['mean'] if s['context_precision'] else '-'} | "
                f"{s['context_recall']['mean'] if s['context_recall'] else '-'} | "
                f"{s['answer_relevancy']['mean'] if s['answer_relevancy'] else '-'} | "
                f"{s['ragas_score']} |"
            )
        lines.append("")

        lines.append("## 样本明细\n")
        for i, r in enumerate(agg["samples"], 1):
            lines.append(f"### {i}. {r['question'][:60]}")
            lines.append(f"- Collection: {r.get('collection', '-')} | Category: {r.get('category', '-')}")
            if "ragas_score" in r:
                lines.append(f"- Faithfulness: {r['faithfulness']['score']} | "
                             f"Context Prec: {r['context_precision']['score']} | "
                             f"Context Rec: {r['context_recall']['score']} | "
                             f"Answer Rel: {r['answer_relevancy']['score']} | "
                             f"**RAGAS: {r['ragas_score']}**")
                lines.append(f"- 检索片段数: {r.get('context_count', 0)}")
            else:
                lines.append(f"- 评估失败: {r.get('error', '')}")
            lines.append("")

        return "\n".join(lines)
