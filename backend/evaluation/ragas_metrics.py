"""RAGAS 四大核心指标算法实现。

自研实现（不依赖 ragas 官方库——其 0.4.3 依赖 langchain_community.vertexai，
与当前环境不兼容）。直接复用项目 LLM（deepseek-chat）作为评判器 Judge，
embedding（text-embedding-v4）用于 Answer Relevancy 的余弦相似度。

指标定义（RAGAS 论文 / 文档）：
  1. Faithfulness        答案陈述能被上下文支持的比例
  2. Context Precision  相关 chunks 是否排在前面（Average Precision@K）
  3. Context Recall      上下文是否覆盖 ground truth 的关键信息
  4. Answer Relevancy    答案是否聚焦问题（反向生成问题 + 余弦相似度）

设计：
  - 每条用例约 6 次 LLM 调用（claim 提取 + 批量判定，避免逐条调用）
  - 判定失败/不确定的项记入 uncertain，不计入分母
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field

from models.llm import get_llm, ainvoke_json_with_schema
from log import get_logger

logger = get_logger("eval")


# ── LLM 结构化输出 Schema ──────────────────────────────────────

class _ClaimList(BaseModel):
    claims: list[str] = Field(default_factory=list)


class _Verdict(BaseModel):
    verdict: str = "uncertain"   # yes / no / uncertain
    reason: str = ""


class _VerdictList(BaseModel):
    verdicts: list[_Verdict] = Field(default_factory=list)


class _QuestionList(BaseModel):
    questions: list[str] = Field(default_factory=list)


# ── Prompt 模板 ────────────────────────────────────────────────

_CLAIM_EXTRACT_PROMPT = """你是一名严谨的评估助手。请将下面的【{role}】拆解为多个独立的原子陈述（claim）。

每个 claim 必须：
1. 是一个可独立判断真伪的简单陈述句（不含连接词组合多个事实）
2. 忠实于原文，不添加原文没有的信息
3. 不遗漏原文的关键事实

【{role}】：
{text}

只输出 JSON：{{"claims": ["陈述1", "陈述2", ...]}}"""

_VERIFY_CLAIMS_PROMPT = """你是一名严谨的评估助手。请逐条判断下面的每个陈述（claim）是否能从给定的【上下文】中推断出来。

【上下文】：
{contexts}

【陈述】：
{claims}

对每个陈述输出判定：
- yes：能从上下文推断
- no：不能从上下文推断
- uncertain：不确定

只输出 JSON：{{"verdicts": [{{"verdict": "yes|no|uncertain", "reason": "简短理由"}}, ...]}}"""

_CONTEXT_USEFULNESS_PROMPT = """你是一名检索质量评估助手。请判断下面的每个【检索片段】对回答【问题】是否有用（是否包含回答问题所需的相关信息）。

【问题】：
{question}

【检索片段】：
{contexts}

对每个片段输出判定：
- yes：有用，包含相关信息
- no：无用，与问题无关

只输出 JSON：{{"verdicts": [{{"verdict": "yes|no", "reason": "简短理由"}}, ...]}}"""

_ANSWER_QUESTION_GEN_PROMPT = """你是一名评估助手。根据下面的【回答】内容，反向生成 3 个该回答能够完整回答的问题。

要求：
1. 问题要覆盖回答的核心信息点
2. 用中文提问，语言自然
3. 不要包含"根据这段文字"等元描述

【回答】：
{answer}

只输出 JSON：{{"questions": ["问题1", "问题2", "问题3"]}}"""


# ── 指标实现 ───────────────────────────────────────────────────

class RAGASMetrics:
    """RAGAS 四大核心指标算法，使用项目 LLM 作为评判器。"""

    def __init__(self, llm: Any = None):
        self.llm = llm or get_llm()

    # ── 工具方法 ──

    async def _extract_claims(self, text: str, role: str = "回答") -> list[str]:
        """将文本拆解为原子陈述。"""
        prompt = _CLAIM_EXTRACT_PROMPT.format(role=role, text=text[:3000])
        try:
            parsed = await ainvoke_json_with_schema(
                self.llm, prompt, _ClaimList, logger, "Eval Claims",
            )
            return [c.strip() for c in parsed.claims if c and c.strip()]
        except Exception as exc:
            logger.error("Claim extraction failed: %s", exc)
            return []

    async def _verify_claims(self, claims: list[str], contexts: list[str], prompt_tpl: str) -> list[dict]:
        """批量判定 claims 是否被上下文支持/归因。"""
        if not claims:
            return []
        numbered_claims = "\n".join(f"{i + 1}. {c}" for i, c in enumerate(claims))
        prompt = prompt_tpl.format(contexts=_format_contexts(contexts), claims=numbered_claims)
        try:
            parsed = await ainvoke_json_with_schema(
                self.llm, prompt, _VerdictList, logger, "Eval Verdicts",
            )
            return [{"verdict": v.verdict.lower(), "reason": v.reason} for v in parsed.verdicts]
        except Exception as exc:
            logger.error("Claim verification failed: %s", exc)
            return [{"verdict": "uncertain", "reason": "判定失败"} for _ in claims]

    # ── 指标 1：Faithfulness（忠实性）──

    async def faithfulness(self, answer: str, contexts: list[str]) -> dict:
        """答案中能被上下文支持的陈述比例。"""
        claims = await self._extract_claims(answer, "回答")
        if not claims:
            return {"score": 0.0, "supported": 0, "total": 0, "uncertain": 0}

        verdicts = await self._verify_claims(claims, contexts, _VERIFY_CLAIMS_PROMPT)
        supported = sum(1 for v in verdicts if v["verdict"] == "yes")
        uncertain = sum(1 for v in verdicts if v["verdict"] == "uncertain")
        judged = len(verdicts) - uncertain
        score = supported / judged if judged > 0 else 0.0
        return {"score": round(score, 3), "supported": supported, "total": len(claims), "uncertain": uncertain}

    # ── 指标 2：Context Precision（上下文精确性）──

    async def context_precision(self, question: str, contexts: list[str], ground_truth: str) -> dict:
        """相关 chunks 是否排在前列（Average Precision@K）。"""
        if not contexts:
            return {"score": 0.0, "relevant": 0, "total": 0, "uncertain": 0}

        numbered = "\n".join(f"{i + 1}. {c[:300]}" for i, c in enumerate(contexts))
        prompt = _CONTEXT_USEFULNESS_PROMPT.format(question=question[:500], contexts=numbered)
        try:
            parsed = await ainvoke_json_with_schema(
                self.llm, prompt, _VerdictList, logger, "Eval ContextPrecision",
            )
            verdicts = [v.verdict.lower() for v in parsed.verdicts]
        except Exception as exc:
            logger.error("Context precision judgment failed: %s", exc)
            verdicts = ["uncertain"] * len(contexts)

        # Average Precision@K
        ap = 0.0
        relevant_count = 0
        hits = 0
        for k, v in enumerate(verdicts, 1):
            if v == "yes":
                hits += 1
                ap += hits / k
                relevant_count += 1
        score = ap / relevant_count if relevant_count > 0 else 0.0
        return {"score": round(score, 3), "relevant": relevant_count, "total": len(contexts),
                "uncertain": verdicts.count("uncertain")}

    # ── 指标 3：Context Recall（上下文召回率）──

    async def context_recall(self, contexts: list[str], ground_truth: str) -> dict:
        """上下文覆盖 ground truth 关键信息的比例。"""
        gt_claims = await self._extract_claims(ground_truth, "标准答案")
        if not gt_claims:
            return {"score": 0.0, "attributed": 0, "total": 0, "uncertain": 0}

        verdicts = await self._verify_claims(gt_claims, contexts, _VERIFY_CLAIMS_PROMPT)
        attributed = sum(1 for v in verdicts if v["verdict"] == "yes")
        uncertain = sum(1 for v in verdicts if v["verdict"] == "uncertain")
        judged = len(verdicts) - uncertain
        score = attributed / judged if judged > 0 else 0.0
        return {"score": round(score, 3), "attributed": attributed, "total": len(gt_claims), "uncertain": uncertain}

    # ── 指标 4：Answer Relevancy（答案相关性）──

    async def answer_relevancy(self, question: str, answer: str, strictness: int = 3) -> dict:
        """答案是否聚焦问题（反向生成问题 + 余弦相似度）。"""
        from models.embedding import aembed_query, aembed_documents

        prompt = _ANSWER_QUESTION_GEN_PROMPT.format(answer=answer[:2000])
        try:
            parsed = await ainvoke_json_with_schema(
                self.llm, prompt, _QuestionList, logger, "Eval AnswerRel",
            )
            gen_questions = [q.strip() for q in parsed.questions if q and q.strip()][:strictness]
        except Exception as exc:
            logger.error("Answer relevancy question generation failed: %s", exc)
            return {"score": 0.0, "gen_questions": []}

        if not gen_questions:
            return {"score": 0.0, "gen_questions": []}

        try:
            q_emb = await aembed_query(question)
            gen_embs = await aembed_documents(gen_questions)
            sims = [cosine_similarity(q_emb, g) for g in gen_embs]
            score = sum(sims) / len(sims)
        except Exception as exc:
            logger.error("Answer relevancy embedding failed: %s", exc)
            return {"score": 0.0, "gen_questions": gen_questions}

        return {"score": round(max(0.0, min(1.0, score)), 3), "gen_questions": gen_questions}

    # ── 单条完整评估 ──

    async def evaluate(self, question: str, answer: str, contexts: list[str], ground_truth: str) -> dict:
        """对一条测试用例计算全部四项指标。"""
        faith = await self.faithfulness(answer, contexts)
        prec = await self.context_precision(question, contexts, ground_truth)
        rec = await self.context_recall(contexts, ground_truth)
        rel = await self.answer_relevancy(question, answer)
        ragas = 0.25 * (faith["score"] + prec["score"] + rec["score"] + rel["score"])
        return {
            "faithfulness": faith,
            "context_precision": prec,
            "context_recall": rec,
            "answer_relevancy": rel,
            "ragas_score": round(ragas, 3),
        }


# ── 辅助函数 ───────────────────────────────────────────────────

def _format_contexts(contexts: list[str], max_len: int = 300) -> str:
    """格式化检索上下文列表，带编号。"""
    if not contexts:
        return "（无检索上下文）"
    return "\n".join(f"{i + 1}. {c[:max_len]}" for i, c in enumerate(contexts))


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """计算两个向量的余弦相似度。"""
    import math
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)
