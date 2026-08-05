"""InterviewEngine — 交互式模拟面试核心引擎。

生命周期：start → chat（多轮）→ end。
所有状态存 Redis（session:{session_id}:interview），每个方法加载状态、操作、持久化。
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import BaseModel as _BM

from models.llm import get_llm, ainvoke_json_with_schema
from services.knowledge_base import KnowledgeBase
from log import get_logger

from .contracts import (
    InterviewQuestion,
    InterviewSessionState,
    InterviewTurn,
    InterviewStartResponse,
    InterviewChatResponse,
    InterviewEndResponse,
    JudgeOutput,
)
from .judge import JudgeAgent
from .prompts import QUESTION_GENERATION_PROMPT, FINAL_SUMMARY_PROMPT
from .audio import transcribe_audio

logger = get_logger("agent")


# ── Pydantic Schema（LLM 结构化输出）──

class _Q(_BM):
    category: str = "技术"
    difficulty: str = "medium"
    question: str
    reason: str = ""


class _QuestionPoolSchema(_BM):
    questions: list[_Q]


class _SummarySchema(_BM):
    summary: str = ""
    strong_areas: list[str] = []
    weak_areas: list[str] = []
    recommendations: list[str] = []


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class InterviewEngine:
    """交互式模拟面试引擎。

    每个方法从 Redis 加载面试状态，操作后持久化回 Redis。
    """

    def __init__(self, session_id: str, store, llm: Any = None):
        self.session_id = session_id
        self.store = store  # RedisSessionStore 实例
        self.llm = llm or get_llm()
        self.judge = JudgeAgent()

    # ── 生命周期 ──

    async def start(self, state: Any) -> InterviewStartResponse:
        """初始化面试会话：快照上下文 → 出题 → 生成开场白 → 持久化。

        Args:
            state: 主流程 CopilotState（含 job / resume / profile）
        """
        # 1. 快照上下文
        job = getattr(state, "job", None)
        resume = getattr(state, "resume_content_json", None)
        profile = getattr(state, "candidate_profile", None)

        job_title = (job.title if job else "") or ""
        job_json = job.model_dump_json(indent=2) if job else "{}"
        resume_json = resume.model_dump_json(indent=2) if resume else "{}"
        profile_json = profile.model_dump_json(indent=2) if profile else "{}"

        # 2. 知识库检索（岗位 + 技能关键词）
        kb_context = ""
        try:
            query_parts = [job_title]
            if job and job.keywords:
                query_parts.extend(job.keywords[:3])
            if job and job.hard_skills:
                query_parts.extend(job.hard_skills[:2])
            query = " ".join(p for p in query_parts if p) or "面试经验"
            kb_context = await KnowledgeBase.search(query, top_n=5)
        except Exception as exc:
            logger.warning("Interview KB search failed: %s", exc)

        # 3. LLM 出题（5 道）
        questions = await self._generate_questions(
            job_json=job_json,
            resume_json=resume_json,
            profile_json=profile_json,
            kb_context=kb_context,
            n=5,
        )

        # 4. 构建会话状态
        now = _now()
        session_state = InterviewSessionState(
            session_id=self.session_id,
            status="active",
            max_rounds=len(questions) or 5,
            job_title=job_title,
            job_json=job_json,
            resume_json=resume_json,
            profile_json=profile_json,
            question_pool=questions,
            created_at=now,
            updated_at=now,
        )

        # 5. 持久化
        await self.store.save_interview_state(session_state.model_dump())

        # 6. 首题 + 开场白
        first_q = questions[0] if questions else None
        if not first_q:
            first_q = InterviewQuestion(id="q1", category="技术", question="请做一下自我介绍。")

        opening = (
            f"你好！我是你的模拟面试官。今天将围绕岗位「{job_title or '你的目标岗位'}」"
            f"进行一场 {session_state.max_rounds} 题的模拟面试，我会逐题提问，"
            f"全部回答完毕后统一给出评分和反馈。\n\n"
            f"{first_q.question}"
        )

        return InterviewStartResponse(
            session_id=self.session_id,
            round=1,
            total_rounds=session_state.max_rounds,
            message=opening,
            question=first_q,
        )

    async def chat(
        self,
        answer: str = "",
        audio_base64: Optional[str] = None,
    ) -> InterviewChatResponse:
        """接收一轮回答 → 记录 → 出下一题或收尾。

        Args:
            answer: 用户文本回答
            audio_base64: 可选，语音音频 base64

        注意：本方法不再逐题评分。回答仅暂存于 history（judge_result=None），
        待全部题目作答完毕后由 end()/_finish() 统一评分。
        """
        # 1. 语音转文字（优先）
        if audio_base64:
            try:
                answer = await transcribe_audio(audio_base64)
                logger.info("Interview audio transcribed: %d chars", len(answer))
            except Exception as exc:
                logger.error("Audio transcription failed: %s", exc)
                answer = answer or "（语音转写失败）"

        if not answer or not answer.strip():
            answer = "（候选人未作答）"

        # 2. 加载会话状态
        raw = await self.store.load_interview_state()
        if not raw:
            raise ValueError("面试会话不存在或已过期，请重新开始模拟面试")
        state = InterviewSessionState.model_validate(raw)

        # 3. 取当前题
        idx = state.round_count  # 0-based
        if idx >= len(state.question_pool):
            # 已无题可出，视为结束（统一评分交给 end/_finish）
            return InterviewChatResponse(
                session_id=self.session_id,
                round=state.round_count,
                total_rounds=len(state.question_pool),
                judge=None,
                next_question=None,
                is_last=True,
                message="所有题目已回答完毕，请获取综合评分。",
            )

        current_q = state.question_pool[idx]

        # 4. 记录本轮（仅暂存答案，不评分）
        turn = InterviewTurn(
            round=idx + 1,
            question=current_q,
            user_answer=answer,
            judge_result=None,  # 统一评分，逐题不评
        )
        state.history.append(turn)
        state.round_count += 1
        state.updated_at = _now()

        # 5. 判断是否最后一题
        is_last = state.round_count >= len(state.question_pool)

        next_q = None
        if not is_last:
            next_q = state.question_pool[state.round_count]

        # 6. 持久化（每次刷新 TTL）
        await self.store.save_interview_state(state.model_dump())

        # 7. 构造回复（逐题不返回评分）
        if is_last:
            message = (
                f"第 {turn.round} 题回答完毕，这是最后一题。\n"
                f"面试结束！点击「完成面试」即可查看全部题目的综合评分与评价。"
            )
        else:
            message = next_q.question if next_q else ""

        return InterviewChatResponse(
            session_id=self.session_id,
            round=state.round_count,
            total_rounds=len(state.question_pool),
            judge=None,
            next_question=next_q,
            is_last=is_last,
            message=message,
        )

    async def end(self) -> InterviewEndResponse:
        """结束面试，生成综合评价。"""
        raw = await self.store.load_interview_state()
        if not raw:
            raise ValueError("面试会话不存在或已过期")
        state = InterviewSessionState.model_validate(raw)

        return await self._finish(state)

    # ── 内部方法 ──

    async def _finish(self, state: InterviewSessionState) -> InterviewEndResponse:
        """对全部回答统一评分，生成最终综合报告并持久化。"""
        # 1. 对所有未评分轮次并行评分
        pending = [t for t in state.history if t.judge_result is None]
        if pending:
            results = await asyncio.gather(
                *(self._judge_turn(state, t) for t in pending),
                return_exceptions=True,
            )
            for turn, res in zip(pending, results):
                if isinstance(res, Exception):
                    logger.error("Judge failed for round %s: %s", turn.round, res)
                    turn.judge_result = JudgeOutput(
                        overall_score=0.0,
                        category=turn.question.category,
                        weakness="评分失败，请稍后重试。",
                    )
                else:
                    turn.judge_result = res

        # 2. 汇总各轮评分
        detailed = []
        scores = []
        for turn in state.history:
            j = turn.judge_result
            score = j.overall_score if j else 0.0
            scores.append(score)
            detailed.append({
                "round": turn.round,
                "category": turn.question.category,
                "question": turn.question.question,
                "score": round(score, 1),
                "strength": (j.strength if j else ""),
                "weakness": (j.weakness if j else ""),
            })

        avg = sum(scores) / len(scores) if scores else 0.0
        max_s = max(scores) if scores else 0.0
        min_s = min(scores) if scores else 0.0

        # 3. 分类强弱项
        category_scores: dict[str, list[float]] = {}
        for turn in state.history:
            if turn.judge_result:
                cat = turn.question.category
                category_scores.setdefault(cat, []).append(turn.judge_result.overall_score)
        strong = [c for c, s in category_scores.items() if (sum(s) / len(s)) >= 7.0]
        weak = [c for c, s in category_scores.items() if (sum(s) / len(s)) < 6.0]

        # 4. LLM 生成综合评价
        rounds_detail = "\n".join(
            f"- 第{t['round']}题（{t['category']}）{t['score']}/10：{t['question'][:80]}"
            for t in detailed
        )
        summary = ""
        recommendations: list[str] = []
        try:
            prompt = FINAL_SUMMARY_PROMPT.format(
                job_focus=state.job_title or "未指定岗位",
                rounds_detail=rounds_detail or "（无轮次记录）",
            )
            parsed = await ainvoke_json_with_schema(self.llm, prompt, _SummarySchema, logger, "Interview Summary")
            summary = parsed.summary
            strong = parsed.strong_areas or strong
            weak = parsed.weak_areas or weak
            recommendations = parsed.recommendations
        except Exception as exc:
            logger.error("Interview summary generation failed: %s", exc)
            summary = f"本次模拟面试完成，平均分 {avg:.1f}/10。"

        # 5. 标记结束并持久化
        state.status = "ended"
        state.updated_at = _now()
        await self.store.save_interview_state(state.model_dump())
        # 记录已完成（保留在 Redis 供 end 端点查询，稍后删除或保留均可）

        return InterviewEndResponse(
            session_id=self.session_id,
            total_rounds=len(state.history),
            avg_score=round(avg, 1),
            max_score=round(max_s, 1),
            min_score=round(min_s, 1),
            detailed_scores=detailed,
            strong_areas=strong,
            weak_areas=weak,
            summary=summary,
            recommendations=recommendations,
        )

    async def _judge_turn(self, state: InterviewSessionState, turn: InterviewTurn) -> JudgeOutput:
        """针对单轮回答做知识库检索 + Judge 评分（用于统一评分阶段）。"""
        kb_context = ""
        try:
            kb_context = await KnowledgeBase.search(
                f"{state.job_title} {turn.question.category} {turn.question.question}", top_n=3
            )
        except Exception:
            pass
        return await self.judge.evaluate(
            question=turn.question,
            user_answer=turn.user_answer,
            job_focus=state.job_title,
            resume_summary=self._resume_summary(state.resume_json),
            knowledge_context=kb_context,
        )

    async def _generate_questions(
        self,
        job_json: str,
        resume_json: str,
        profile_json: str,
        kb_context: str,
        n: int = 5,
    ) -> list[InterviewQuestion]:
        """LLM 生成 n 道题（每题内容带「第X题」编号前缀）。"""
        prompt = QUESTION_GENERATION_PROMPT.format(
            n=n,
            job_json=job_json or "{}",
            resume_json=resume_json or "{}",
            profile_json=profile_json or "{}",
            knowledge_context=kb_context or "（知识库无内容）",
        )
        try:
            parsed = await ainvoke_json_with_schema(self.llm, prompt, _QuestionPoolSchema, logger, "Interview QuestionGen")
            questions = []
            for i, q in enumerate(parsed.questions, 1):
                questions.append(InterviewQuestion(
                    id=f"q{i}",
                    category=q.category,
                    difficulty=q.difficulty,
                    question=f"第{i}题：{q.question.strip()}",
                ))
            return questions
        except Exception as exc:
            logger.error("Question generation failed: %s", exc)
            # 降级：返回 5 个通用题
            return [
                InterviewQuestion(id=f"q{i}", category="技术", question=f"第{i}题：{fallback}")
                for i, fallback in enumerate([
                    "请做一下自我介绍。",
                    "请讲讲你最熟悉的一个技术点，并说明它的原理。",
                    "请描述一个你参与过的项目，重点讲你的角色和贡献。",
                    "遇到棘手问题时你是如何解决的？请举例说明。",
                    "谈谈你未来 3 年的职业规划。",
                ], 1)
            ]

    def _resume_summary(self, resume_json: str) -> str:
        """从简历 JSON 提取简短摘要（技能 + 项目名）。"""
        try:
            data = json.loads(resume_json)
            parts = []
            skills = data.get("skills", [])
            if skills:
                parts.append("技能：" + ", ".join(s.get("title", "") for s in skills[:5]))
            projects = data.get("projects", [])
            if projects:
                parts.append("项目：" + ", ".join(p.get("title", "") for p in projects[:3]))
            return "；".join(parts)[:800]
        except Exception:
            return ""
