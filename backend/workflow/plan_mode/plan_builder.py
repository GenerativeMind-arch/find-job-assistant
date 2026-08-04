"""Plan Builder — 将 Intent + State 转换为结构化执行计划。

兼容策略：同时生成旧 execution_plan (list[str]) 和新 execution_steps (list[PlanStep])。
"""

from __future__ import annotations

from typing import Any

from workflow.plan_mode.plan_schema import Plan, PlanStep, PlanPolicy, StepPolicy
from workflow.state import CopilotState
from log import get_logger

logger = get_logger("agent")

# ── Intent → Agent 映射 ─────────────────────────────────────────

_INTENT_TO_AGENTS: dict[str, list[str]] = {
    "upload_jd": ["jd_agent", "gap_agent", "content_agent", "render_agent"],
    "upload_profile": ["profile_agent", "content_agent", "render_agent"],
    "search_jd": [],                    # 特殊处理：先搜索展示
    "import_boss_job": ["gap_agent", "content_agent", "render_agent"],
    "gap_analysis": ["gap_agent"],
    "content_edit": ["content_agent", "render_agent"],
    "render_edit": ["render_agent"],
    "search_knowledge": [],             # 内联处理：RAG + LLM 直接回答
    "interview_start": ["interview_trigger"],
    "export": [],
    "ask_question": [],                 # 内联处理
}

# ── Agent 读写声明 ───────────────────────────────────────────────

_AGENT_RW: dict[str, dict] = {
    "jd_agent": {
        "reads": ["user_message", "job"],
        "writes": ["job"],
        "forbidden": ["candidate_profile", "resume_content_json", "render_config", "resume_html", "interview_qa"],
    },
    "profile_agent": {
        "reads": ["user_message", "candidate_profile"],
        "writes": ["candidate_profile"],
        "forbidden": ["job", "resume_content_json", "render_config", "resume_html", "interview_qa"],
    },
    "gap_agent": {
        "reads": ["job", "candidate_profile"],
        "writes": ["gaps", "questions_to_ask"],
        "forbidden": ["resume_content_json", "render_config", "resume_html", "interview_qa"],
    },
    "content_agent": {
        "reads": ["job", "candidate_profile", "gaps", "resume_content_json", "user_message"],
        "writes": ["resume_content_json"],
        "forbidden": ["render_config", "resume_html", "interview_qa"],
    },
    "render_agent": {
        "reads": ["resume_content_json", "render_config", "user_message"],
        "writes": ["render_config", "resume_html"],
        "forbidden": ["job", "candidate_profile", "resume_content_json", "interview_qa"],
    },
    "interview_trigger": {
        "reads": ["job"],               # 只检查前置条件
        "writes": [],                   # 只写 workflow_trace / reply_message
        "forbidden": ["candidate_profile", "resume_content_json", "render_config", "resume_html", "interview_qa"],
    },
}


class PlanBuilder:
    """将意图包 + 当前 State 转换为 Plan。"""

    @staticmethod
    def build(
        primary_intent: str,
        secondary_intents: list[str],
        state: CopilotState,
        entities: dict | None = None,
    ) -> Plan:
        """生成执行计划（同时兼容旧格式）。"""
        plan = Plan(
            session_id=state.session_id,
            intent_bundle={
                "primary": primary_intent,
                "secondary": secondary_intents,
                "entities": entities or {},
            },
        )

        # ── 主意图 → 基础步骤 ──
        plan.steps = PlanBuilder._build_steps_for_intent(primary_intent, state)

        # ── 合并次要意图 ──
        seen_agents = {s.agent_name for s in plan.steps}
        _SECONDARY_AGENTS: dict[str, str] = {
            "render_edit": "render_agent",
            "content_edit": "content_agent",
            "gap_analysis": "gap_agent",
        }
        for sec in secondary_intents:
            agent = _SECONDARY_AGENTS.get(sec)
            if agent and agent not in seen_agents:
                extra_steps = PlanBuilder._build_steps_for_agent(agent)
                for s in extra_steps:
                    if s.agent_name not in seen_agents:
                        plan.steps.append(s)
                        seen_agents.add(s.agent_name)

        # ── 设置依赖关系 ──
        PlanBuilder._set_dependencies(plan)

        # ── 条件跳过逻辑 ──
        PlanBuilder._apply_skip_logic(plan, state)

        logger.info(
            "Plan built: plan_id=%s, primary=%s, steps=%d (%s)",
            plan.plan_id, primary_intent, len(plan.steps),
            " → ".join(plan.step_names) if plan.steps else "empty",
        )
        return plan

    @staticmethod
    def _build_steps_for_intent(intent: str, state: CopilotState) -> list[PlanStep]:
        """为指定意图构建步骤列表。"""
        agent_names = _INTENT_TO_AGENTS.get(intent, [])

        # upload_jd 条件分支：无 profile 时只跑 jd_agent
        if intent == "upload_jd" and state.candidate_profile is None:
            agent_names = ["jd_agent"]

        steps = []
        for name in agent_names:
            steps.append(PlanBuilder._create_step(name))
        return steps

    @staticmethod
    def _build_steps_for_agent(agent_name: str) -> list[PlanStep]:
        """为单个 Agent 创建步骤。"""
        return [PlanBuilder._create_step(agent_name)]

    @staticmethod
    def _create_step(agent_name: str) -> PlanStep:
        """创建 PlanStep 并自动填充读写声明。"""
        rw = _AGENT_RW.get(agent_name, {})
        return PlanStep(
            agent_name=agent_name,
            action="execute",
            reads=rw.get("reads", []),
            writes=rw.get("writes", []),
        )

    @staticmethod
    def _set_dependencies(plan: Plan) -> None:
        """根据步骤顺序和读写声明设置 depends_on 关系。

        规则：步骤 B 的 reads 中有步骤 A 的 writes → B 依赖 A。
        """
        for i, step in enumerate(plan.steps):
            if i == 0:
                continue
            for j in range(i):
                prev = plan.steps[j]
                # 前序 step 写入了当前 step 需要读的字段 → 建立依赖
                if any(w in step.reads for w in prev.writes):
                    if prev.step_id not in step.depends_on:
                        step.depends_on.append(prev.step_id)

    @staticmethod
    def _apply_skip_logic(plan: Plan, state: CopilotState) -> None:
        """根据 State 状态应用跳过逻辑（空 Plan 的直接返回）。"""
        has_job = state.job is not None
        has_profile = state.candidate_profile is not None
        has_resume = state.resume_content_json is not None

        for step in plan.steps:
            name = step.agent_name
            # Gap Agent：无 JD 或无 Profile → 跳过
            if name == "gap_agent" and (not has_job or not has_profile):
                step.preconditions.append("SKIP: job or candidate_profile missing")
            # Content Agent：无 JD 或无 Profile → 跳过
            if name == "content_agent" and (not has_job or not has_profile):
                step.preconditions.append("SKIP: job or candidate_profile missing")
            # Render Agent：无 resume_content → 跳过
            if name == "render_agent" and not has_resume:
                step.preconditions.append("SKIP: resume_content_json missing")
            # Interview Trigger：无 JD → 跳过（引导用户先提供岗位）
            if name == "interview_trigger" and not has_job:
                step.preconditions.append("SKIP: job missing")
