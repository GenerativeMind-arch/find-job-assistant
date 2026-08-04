"""Judge Agent — 对候选人面试回答进行四维度评分与反馈。"""

from __future__ import annotations

import asyncio
from typing import Any

from models.llm import get_llm, ainvoke_json_with_schema
from log import get_logger

from .contracts import InterviewQuestion, JudgeOutput
from .prompts import JUDGE_SYSTEM_PROMPT

logger = get_logger("agent")


class JudgeAgent:
    """评估单道面试回答，返回结构化评分结果。

    四维度（0-10 分）：准确性 / 深度 / 表达 / 匹配度。
    输出 JudgeOutput：总分 + 维度分 + 优势 + 薄弱 + 建议 + 类别。
    """

    async def evaluate(
        self,
        question: InterviewQuestion,
        user_answer: str,
        job_focus: str,
        resume_summary: str,
        knowledge_context: str = "",
    ) -> JudgeOutput:
        """评估一个回答。"""
        prompt = JUDGE_SYSTEM_PROMPT.format(
            job_focus=job_focus or "未指定岗位",
            category=question.category,
            question=question.question,
            user_answer=user_answer or "（候选人未作答）",
            resume_summary=resume_summary or "（未提供简历摘要）",
            knowledge_context=knowledge_context or "（知识库无相关内容）",
        )
        llm = get_llm()
        try:
            parsed = await ainvoke_json_with_schema(
                llm, prompt, JudgeOutput, logger, "Judge Agent"
            )
            parsed.category = parsed.category or question.category
            return parsed
        except RuntimeError as exc:
            logger.error("Judge Agent failed: %s", exc)
            # 降级：返回低分占位，避免面试流程中断
            return JudgeOutput(
                overall_score=0.0,
                category=question.category,
                strength="",
                weakness="评估失败，请重新作答。",
                suggestion=str(exc),
            )


def judge_node(state: Any) -> Any:
    """Judge Agent 同步兼容入口。"""
    return asyncio.run(JudgeAgent().evaluate(state))
