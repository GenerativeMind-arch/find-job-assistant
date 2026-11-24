"""Planner Agent — 意图分类 + 执行计划生成 + 事件记录。

支持 9 种意图（含新增 search_jd / search_knowledge）和复合意图。
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from agents.json_contracts import IntentClassificationOutput
from models.llm import get_llm, ainvoke_json_with_schema
from prompts.intent_classification import INTENT_CLASSIFICATION_PROMPT
from workflow.state import CopilotState
from workflow.trace import append_trace, summarize_user_message
from log import get_logger

logger = get_logger("agent")

# Intent → 基础执行链路
# 实际执行计划由 _build_execution_plan 根据 state 动态调整
_INTENT_PLAN: dict[str, list[str]] = {
    # 数据输入类
    "upload_jd": ["jd_agent", "gap_agent", "content_agent", "render_agent"],
    "upload_profile": ["profile_agent", "content_agent", "render_agent"],
    "search_jd": [],                                    # 特殊处理：先搜索展示结果，用户选择后再执行 pipeline
    "import_boss_job": ["gap_agent", "content_agent", "render_agent"],  # Boss 导入岗位，job 已解析，跳过 jd_agent
    # 分析与生成类
    "gap_analysis": ["gap_agent"],
    "content_edit": ["content_agent", "render_agent"],
    "render_edit": ["render_agent"],
    # 查询与导出类
    "search_knowledge": [],                             # 内联处理：RAG 检索 + LLM 直接回答
    "interview_start": ["interview_trigger"],           # 模拟面试引导节点
    "export": [],
    "ask_question": [],                                 # 内联处理：基于 state + RAG 直接回答
}


async def _classify_intent_async(state: CopilotState) -> IntentClassificationOutput:
    """异步调用 LLM 进行意图分类（支持复合意图和实体提取）。"""
    prompt = INTENT_CLASSIFICATION_PROMPT.format(
        has_job=state.job is not None,
        has_profile=state.candidate_profile is not None,
        has_resume=state.resume_content_json is not None,
        user_message=state.user_message,
    )
    # 分类输出很短，bind 一个较小的 max_tokens，避免按全局 8192 上限生成。
    # 用 bind 局部覆盖，不影响共享实例上的长文生成。
    llm = get_llm().bind(max_tokens=800)
    result = await ainvoke_json_with_schema(
        llm, prompt, IntentClassificationOutput, logger, "Planner Agent"
    )
    logger.info(
        "Intent classified: primary=%s, secondary=%s, entities=%s (reason: %s)",
        result.primary_intent,
        result.secondary_intents,
        result.entities.model_dump() if result.entities else {},
        result.reason,
    )
    return result


# ── 职位名关键词（用于 heuristic 检测）───────────────────────
_JOB_TITLE_KEYWORDS = [
    "工程师", "分析师", "经理", "开发", "设计", "运营", "产品",
    "架构师", "科学家", "算法", "测试", "前端", "后端", "全栈",
    "数据", "运维", "安全", "研究员", "总监", "主管", "专员",
    "顾问", "助理", "实习生", "管培生", "编辑", "文案", "策划",
    "销售", "市场", "财务", "HR", "人力", "行政", "法务",
]


def _looks_like_job_title(text: str) -> bool:
    """Heuristic：短文本 + 含职位关键词 → 很可能是职位搜索。"""
    stripped = text.strip()
    if len(stripped) > 120:
        return False
    return any(kw in stripped for kw in _JOB_TITLE_KEYWORDS)


# ── 寒暄快路径 ──────────────────────────────────────────────
# 整句就是问候/客套时直接回话，省掉一次意图分类 LLM 调用（实测 ~7s）
_SMALLTALK_INPUTS = {
    "你好", "您好", "您好呀", "hi", "hello", "hey", "嗨", "哈喽",
    "在吗", "在么", "在不在", "早上好", "早安", "中午好", "下午好", "晚上好", "晚安",
    "谢谢", "多谢", "感谢", "thanks", "thank you", "3q",
}


def _is_smalltalk(text: str) -> bool:
    """Heuristic：整句话就是寒暄（去标点后完全匹配）→ 走快路径。"""
    normalized = text.strip().lower().strip("!！。.,，、~～:：;；?？ 　")
    return normalized in _SMALLTALK_INPUTS


# ── Boss 导入岗位流水线触发关键词 ────────────────────────
_PIPELINE_TRIGGER_KEYWORDS = [
    "分析", "生成", "开始", "处理", "继续", "准备", "导入", "简历", "面试",
]


def _is_pipeline_trigger(text: str) -> bool:
    """Heuristic：短指令文本（如"分析这个岗位"、"生成简历"）→ 触发 Boss 导入岗位的全链路。

    排除疑问句（用户可能只是在问信息），避免误触发昂贵的 LLM 全链路。
    """
    stripped = text.strip()
    if not (2 <= len(stripped) <= 60):
        return False
    # 排除疑问句
    if any(m in stripped for m in ("?", "？", "吗", "多少", "什么", "怎么", "谁", "哪")):
        return False
    return any(kw in stripped for kw in _PIPELINE_TRIGGER_KEYWORDS)


def _force_search_jd_if_short_job_title(primary: str, state: CopilotState) -> str:
    """硬编码保护：当 LLM 把短职位名误判为 upload_jd/ask_question 时纠正。

    触发条件（全部满足）：
    1. 当前没有 JD（state.job is None）
    2. 用户输入短（<= 120 字符）
    3. 输入中包含职位关键词
    4. LLM 分类为 upload_jd 或 ask_question
    """
    if state.job is not None:
        return primary  # 已有 JD，不做纠正
    if primary in ("upload_jd", "ask_question") and _looks_like_job_title(state.user_message):
        logger.info(
            "Heuristic override: LLM classified '%s' as '%s' but it looks like a job title → forcing search_jd",
            state.user_message[:60], primary,
        )
        return "search_jd"
    return primary


# ── 前置条件检查：这些意图缺材料就无法产出有意义的结果 ──
# 典型：用户直接说"分析这个岗位"，但没有岗位 / 没有上传简历 → 空跑昂贵的 LLM 链路
_PROFILE_REQUIRED_INTENTS = {"gap_analysis", "content_edit", "import_boss_job"}


def _missing_requirements(primary: str, state: CopilotState) -> list[str]:
    """返回缺失的前置条件，可能包含 'job' / 'profile'。"""
    missing: list[str] = []
    if primary == "gap_analysis" and state.job is None:
        missing.append("job")
    if primary in _PROFILE_REQUIRED_INTENTS and state.candidate_profile is None:
        missing.append("profile")
    return missing


def _build_missing_notice(missing: list[str], primary: str) -> str:
    """把缺失项转成给用户看的提醒文案。"""
    todo = []
    if "job" in missing:
        todo.append("目标岗位 —— 直接发一个职位名搜索，或从「BOSS」页搜索后点「导入岗位」")
    if "profile" in missing:
        todo.append("你的简历/项目材料 —— 点输入框旁的上传按钮传一份")
    what = "岗位分析" if primary == "gap_analysis" else "这一步"
    return (
        f"还差这些信息才能做{what}：\n· " + "\n· ".join(todo)
        + "\n\n补齐后再发一句，我就开始。"
    )


def _build_execution_plan(intent: str, secondary: list[str], state: CopilotState) -> list[str]:
    """根据意图和当前状态构建实际执行计划。

    动态调整逻辑：
    - search_jd：先搜索，用户选择后走 upload_jd pipeline
    - import_boss_job：Boss 导入的岗位（job 已预置解析）→ 全链路（不含 jd_agent）
    - upload_jd 且无 profile：只跑 jd_agent（无法生成简历）
    - content_edit + render_edit 复合：一起排入 plan
    - search_knowledge：走 question_agent（带 RAG）
    """
    base_plan = list(_INTENT_PLAN.get(intent, []))

    # ── search_jd 特殊处理 ──
    if intent == "search_jd":
        # 搜索阶段不跑 Agent，由 planner 直接调用 JobSearchService
        return []

    # ── 内联意图（ask_question / search_knowledge）──
    if intent in ("ask_question", "search_knowledge"):
        # 由 planner 内联处理（RAG + LLM 直接回答），不路由 agent
        return []

    # ── import_boss_job 条件分支 ──
    if intent == "import_boss_job":
        if state.candidate_profile is None:
            # 缺少候选人画像：只有 gap_agent（内部会优雅跳过并提示）
            return ["gap_agent"]
        # 已有 job + profile → 全链路（不含 jd_agent，job 已由 import-job 预置解析）

    # ── upload_jd 条件分支 ──
    if intent == "upload_jd":
        if state.candidate_profile is None:
            # 没有候选人数据，只跑 JD 解析
            return ["jd_agent"]
        if state.job is not None:
            # 已有 JD，覆盖更新 → 全链路
            pass

    # ── 复合意图合并 ──
    merged: list[str] = list(base_plan)
    seen = set(merged)

    # 将 secondary intents 对应的 agent 合并到计划中
    _SECONDARY_AGENTS: dict[str, str] = {
        "render_edit": "render_agent",
        "content_edit": "content_agent",
        "gap_analysis": "gap_agent",
        "export": "",  # export 不通过 agent 处理
    }

    for sec in secondary:
        agent = _SECONDARY_AGENTS.get(sec)
        if agent and agent not in seen:
            merged.append(agent)
            seen.add(agent)

    return merged


async def _handle_search_jd(state: CopilotState) -> dict[str, Any]:
    """处理 search_jd 意图：调用 JobSearchService 搜索 JD。

    逻辑分层：
    1. MCP 搜索成功 → 展示结果列表，等待用户选择
    2. MCP 失败 → LLM 生成兜底 → 直接入库，跳过 jd_agent（Job 已就绪）
    3. 全部失败 → 提示用户手动输入
    """
    from services.job_search import JobSearchService

    job_title = state.user_message.strip()

    try:
        search_result = await JobSearchService.search_jd(job_title)
    except Exception as exc:
        logger.error("JobSearchService failed: %s", exc)
        search_result = {
            "source": "failed",
            "query": job_title,
            "jobs": [],
            "count": 0,
            "message": f"JD 搜索服务暂时不可用：{exc}。请尝试直接粘贴 JD 内容。",
        }

    jobs: list[dict] = search_result.get("jobs", [])
    source: str = search_result.get("source", "failed")
    message: str = search_result.get("message", "")

    # ── 情况 1：搜索成功（多条真实结果）─────────────────
    if jobs and source in ("mcp", "job_kb"):
        job_list_lines = []
        for i, job in enumerate(jobs, 1):
            company = job.get("company", "未知公司")
            salary = job.get("salary", "薪资面议")
            city = job.get("city", "")
            exp = job.get("experience", "")
            desc = job.get("description", "")
            desc_preview = desc[:120] + "..." if len(desc) > 120 else desc
            location = f" · {city}" if city else ""
            requirements = f" · {exp}" if exp else ""
            job_list_lines.append(
                f"{i}. **{job.get('title', job_title)}** — {company}\n"
                f"   薪资: {salary}{location}{requirements}\n"
                f"   {desc_preview}"
            )
        job_list_text = "\n\n".join(job_list_lines)
        reply = (
            "搜索「**" + job_title + "**」相关职位：\n\n"
            "找到以下来自各招聘平台的实时职位：\n\n"
            + job_list_text + "\n\n"
            "---\n"
            "请回复数字序号选择最匹配的职位（如 1），"
            "或输入「生成」让我根据该职位名称直接创建 JD。"
        )
        return {
            "reply_message": reply,
            "current_intent": "search_jd",
            "execution_plan": [],
            "triggered_agents": [],
            "workflow_trace": append_trace(
                state,
                node="planner",
                input_summary=f"用户搜索职位：{summarize_user_message(state.user_message)}",
                output_summary=f"MCP 搜索完成，共 {len(jobs)} 条结果",
                artifacts={"intent": "search_jd", "source": "mcp", "result_count": len(jobs)},
            ),
        }

    # ── 情况 2：LLM 生成成功（兜底方案）─────────────────────
    if jobs and source == "llm":
        job = jobs[0]
        # 将 LLM 生成的 JD 转为 Job 对象并入库 state
        job_data = JobSearchService.build_job_from_search_result(job, state.session_id)

        reply = (
            "未搜索到实时职位数据，已根据「**" + job_title + "**」"
            "的行业通用要求生成 JD：\n\n"
            "**" + str(job.get("title", job_title)) + "**\n"
            "- 行业：" + str(job.get("industry", "未指定")) + "\n"
            "- 技术栈：" + ", ".join(job.get("tech_stack", []) or ["未指定"]) + "\n"
            "- 硬技能：" + ", ".join(job.get("hard_skills", []) or ["未指定"]) + "\n"
            "- 学历要求：" + str(job.get("education", job.get("education_requirement", "未指定"))) + "\n"
            "- 经验要求：" + str(job.get("experience", job.get("experience_requirement", "未指定"))) + "\n\n"
            "我将基于这份 JD 为你生成简历和面试准备内容。如需调整，请直接告诉我。"
        )

        # 关键：跳过 jd_agent，因为 Job 已由 LLM 生成完毕
        plan = _build_execution_plan("upload_jd", [], state)
        plan = [a for a in plan if a != "jd_agent"]

        return {
            "reply_message": reply,
            "job": job_data,
            "current_intent": "upload_jd",
            "execution_plan": plan,
            "triggered_agents": list(plan),
            "workflow_trace": append_trace(
                state,
                node="planner",
                input_summary=f"用户搜索职位：{summarize_user_message(state.user_message)}",
                output_summary=f"LLM 生成 JD（{source}）：{job.get('title', job_title)}",
                artifacts={
                    "intent": "search_jd",
                    "source": source,
                    "job_title": job.get("title", job_title),
                },
            ),
        }

    # ── 情况 3：全部失败 ─────────────────────────────────────
    reply = message or (
        "很抱歉，未能获取到「" + job_title + "」的职位描述。"
        "请尝试使用更具体的职位名称，或直接粘贴 JD 内容。"
    )
    return {
        "reply_message": reply,
        "current_intent": "search_jd",
        "execution_plan": [],
        "triggered_agents": [],
        "workflow_trace": append_trace(
            state,
            node="planner",
            input_summary=f"用户搜索职位：{summarize_user_message(state.user_message)}",
            output_summary=f"搜索失败（{source}）",
            artifacts={"intent": "search_jd", "source": source, "result_count": 0},
        ),
    }


async def _handle_free_question(state: CopilotState, intent: str) -> dict[str, Any]:
    """内联处理自由问答（ask_question / search_knowledge）。

    原 question_agent 节点已被交互式模拟面试替代，自由问答能力
    改为在 planner 内联实现：RAG 知识库检索 + LLM 直接回答。
    """
    from services.knowledge_base import KnowledgeBase
    from models.llm import get_llm, _ainvoke_model, _extract_text_content

    # 1. RAG 知识库检索
    knowledge_context = ""
    try:
        if intent == "search_knowledge":
            knowledge_context = await KnowledgeBase.search(state.user_message, top_n=3)
        else:
            knowledge_context = await KnowledgeBase.search(state.user_message, top_n=3)
    except Exception as exc:
        logger.warning("Knowledge search failed: %s", exc)

    system_prompt = (
        "你是一个职业助手问答专家。你可以读取工作流当前的岗位、简历等信息，并用自然语言回答用户问题。\n"
        "回答要求：\n"
        "- 优先依据提供的上下文回答，不要编造\n"
        "- 如果提供了「知识库参考」，说明该信息来自真实面试经验数据库，请参考其内容回答\n"
        "- 回答要直接、自然，使用中文\n"
    )
    user_prompt = f"用户问题：\n{state.user_message}"
    if state.job and state.job.title:
        user_prompt += f"\n\n当前目标岗位：{state.job.title}"
    if knowledge_context:
        user_prompt += f"\n\n知识库参考：\n{knowledge_context}"

    llm = get_llm()
    try:
        response = await _ainvoke_model(
            llm, [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}]
        )
        answer = _extract_text_content(response) or "我暂时无法回答这个问题。"
    except Exception as exc:
        logger.error("Free question LLM failed: %s", exc)
        answer = "我暂时无法回答这个问题，请稍后重试。"

    return {
        "reply_message": answer,
        "current_intent": intent,
        "execution_plan": [],
        "triggered_agents": [],
        "workflow_trace": append_trace(
            state,
            node="planner",
            input_summary=f"自由问答：{summarize_user_message(state.user_message)}",
            output_summary=answer,
            artifacts={"intent": intent, "answer_length": len(answer)},
        ),
    }


async def planner_node_async(state: CopilotState) -> dict[str, Any]:
    """Planner Agent 异步节点函数。

    职责：
    1. 意图分类（支持复合意图）
    2. 构建执行计划
    3. 特殊意图处理（search_jd 搜索、search_knowledge/ask_question 内联问答）
    """
    logger.info("Planner Agent started for session %s", state.session_id)

    # 0. 寒暄快路径：整句就是问候 → 跳过 LLM 分类与 RAG，直接回话
    if _is_smalltalk(state.user_message):
        logger.info("Smalltalk fast-path: skipping LLM intent classification")
        return {
            "reply_message": (
                "你好！我是 Interview Assistant，可以帮你：\n"
                "· 搜岗位 —— 直接发职位名，如「Python 开发工程师 北京」\n"
                "· 传简历/项目材料，生成针对岗位定制的简历\n"
                "· 做岗位差距分析、模拟面试\n"
                "把需求发我就行。"
            ),
            "current_intent": "ask_question",
            "execution_plan": [],
            "triggered_agents": [],
            "workflow_trace": append_trace(
                state,
                node="planner",
                input_summary=f"寒暄：{summarize_user_message(state.user_message)}",
                output_summary="寒暄快路径直接回复（跳过 LLM 分类与 RAG）",
                artifacts={"intent": "smalltalk", "fast_path": True},
            ),
        }

    # 1. 意图分类
    try:
        intent_result = await _classify_intent_async(state)
    except RuntimeError as exc:
        logger.error("Planner Agent failed: %s", exc)
        return {
            "current_intent": "ask_question",
            "execution_plan": [],
            "triggered_agents": [],
            "workflow_trace": append_trace(
                state,
                node="planner",
                status="failed",
                input_summary=f"用户输入：{summarize_user_message(state.user_message)}",
                output_summary="意图识别失败：模型输出格式异常，请稍后重试。",
                error=str(exc),
            ),
        }

    primary = intent_result.primary_intent or "ask_question"
    secondary = intent_result.secondary_intents or []

    # ── 硬编码保护：短输入 + 像职位名 → 强制 search_jd ──
    # LLM 有时会把"Agent应用开发工程师"这种短职位名误判为 upload_jd 或 ask_question
    primary = _force_search_jd_if_short_job_title(primary, state)

    # ── Boss 导入岗位启发式 ──
    # 当 state.job 由 Boss 导入（id 以 boss_ 开头且已解析）且用户输入是
    # 流水线触发指令（如"分析这个岗位"）时，强制走 import_boss_job 全链路，
    # 避免 LLM 把这类短指令误判为 ask_question / gap_analysis 等只跑部分节点。
    if (
        state.job is not None
        and state.job.id.startswith("boss_")
        and state.job.parsed_at
        and _is_pipeline_trigger(state.user_message)
    ):
        primary = "import_boss_job"
        logger.info(
            "Boss-imported job heuristic: forced intent=import_boss_job (msg=%s)",
            state.user_message[:40],
        )

    # ── 前置条件检查：缺岗位/简历时明确提醒，而不是空跑昂贵链路 ──
    missing = _missing_requirements(primary, state)
    if missing:
        notice = _build_missing_notice(missing, primary)
        logger.info("Precondition missing for intent=%s: %s", primary, missing)
        return {
            "reply_message": notice,
            "notice": notice,
            "current_intent": primary,
            "execution_plan": [],
            "triggered_agents": [],
            "workflow_trace": append_trace(
                state,
                node="planner",
                input_summary=f"前置条件不足：{missing}（intent={primary}）",
                output_summary=notice,
                artifacts={"intent": primary, "missing": missing},
            ),
        }

    # ── search_jd 特殊处理：搜索 JD 并返回结果 ──
    if primary == "search_jd":
        return await _handle_search_jd(state)

    # ── 自由问答 / 知识库查询：内联处理（原 question_agent 已移除）──
    if primary in ("ask_question", "search_knowledge"):
        return await _handle_free_question(state, primary)

    # 2. 构建执行计划
    plan = _build_execution_plan(primary, secondary, state)
    logger.info("Execution plan: primary=%s, secondary=%s, plan=%s", primary, secondary, plan)

    # 3. 生成消息 ID
    message_id = f"msg_{uuid.uuid4().hex[:12]}"

    updates: dict[str, Any] = {
        "current_intent": primary,
        "execution_plan": plan,
        "triggered_agents": list(plan),
        "workflow_trace": append_trace(
            state,
            node="planner",
            input_summary=f"用户输入：{summarize_user_message(state.user_message)}",
            output_summary="",
            artifacts={
                "intent": primary,           # 向后兼容旧测试
                "primary_intent": primary,
                "secondary_intents": secondary,
                "reason": intent_result.reason,
                "execution_plan": plan,
                "entities": intent_result.entities.model_dump() if intent_result.entities else {},
            },
        ),
    }

    # 4. 无需执行 Agent 的意图直接回复
    if not plan:
        if primary == "export":
            updates["workflow_trace"] = append_trace(
                state.model_copy(update={"workflow_trace": updates["workflow_trace"]}),
                node="export",
                status="skipped",
                input_summary="用户请求导出。",
                output_summary="导出功能将在后续版本中支持。",
                artifacts={"supported": False},
            )

    updates["meta"] = state.meta.model_copy(update={
        "last_user_message_id": message_id,
    })

    return updates


def planner_node(state: CopilotState) -> dict[str, Any]:
    """Planner Agent 同步兼容入口。"""
    return asyncio.run(planner_node_async(state))
