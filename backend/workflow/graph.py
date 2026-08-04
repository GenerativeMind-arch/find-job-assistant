"""LangGraph 图定义与编排。

当前架构（兼容模式）：
  Planner → (JD | Profile | Gap | Content | Render | Interview | Question) → Respond

Plan Mode 演进方向（feature flag）：
  Planner → PlanExecutor → Respond
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import StateGraph, END

from workflow.state import CopilotState

from agents.planner import planner_node_async
from agents.jd_agent import jd_node_async
from agents.profile_agent import profile_node_async
from agents.gap_agent import gap_node_async
from agents.content_agent import content_node_async
from agents.render_agent import render_node_async
from agents.interview_trigger import interview_trigger_node_async
from log import get_logger

logger = get_logger("agent")

_agents_registered = False


def _ensure_agents_registered() -> None:
    """确保所有 Agent 已注册到 AgentRegistry（幂等）。"""
    global _agents_registered
    if not _agents_registered:
        from agents.registry import register_all
        register_all()
        _agents_registered = True


def _route_after_planner(state: CopilotState) -> str:
    """根据 Planner 识别的 intent 路由到下一个节点。"""
    plan = state.execution_plan
    if not plan:
        return "respond"
    return plan[0]


def _route_after_jd(state: CopilotState) -> str:
    plan = state.execution_plan
    if "gap_agent" in plan:
        return "gap_agent"
    if "content_agent" in plan:
        return "content_agent"
    return "respond"


def _route_after_profile(state: CopilotState) -> str:
    plan = state.execution_plan
    if "content_agent" in plan:
        return "content_agent"
    return "respond"


def _route_after_gap(state: CopilotState) -> str:
    plan = state.execution_plan
    if "content_agent" in plan:
        return "content_agent"
    return "respond"


def _route_after_content(state: CopilotState) -> str:
    plan = state.execution_plan
    if "render_agent" in plan:
        return "render_agent"
    return "respond"


def _route_after_render(state: CopilotState) -> str:
    """Render 完成后直接 respond（interview_agent 已被交互式模拟面试替代）。"""
    return "respond"


def _respond(state: CopilotState) -> dict[str, Any]:
    """最终响应节点 — 基于本轮运行轨迹生成稳定的过程记录。

    如果 Planner 已预置 reply_message（如 search_jd 搜索结果展示），
    则保留不覆盖，避免把格式化的用户展示替换为技术 trace。
    """
    if state.reply_message:
        return {}
    return {"reply_message": _build_trace_reply(state)}


def _build_trace_reply(state: CopilotState) -> str:
    """基于本轮运行轨迹生成简洁的用户回复——只输出有意义的结果，不暴露执行过程。"""
    if not state.workflow_trace:
        return ""

    # 收集失败信息（需要告知用户）
    failures = [item for item in state.workflow_trace if item.status == "failed"]
    if failures:
        return failures[-1].output_summary or "处理未完成，请重试。"

    # 收集非 planner 节点的输出摘要，拼成简洁回复
    messages = []
    for item in state.workflow_trace:
        if item.node == "planner":
            continue
        if item.output_summary:
            messages.append(item.output_summary)

    if not messages:
        if state.current_intent == "export":
            return "导出功能将在后续版本中支持。"
        return ""

    return "\n\n".join(messages)


def build_graph() -> StateGraph:
    """构建主 workflow 图。

    兼容模式：保留原固定节点 + 条件路由。
    Plan Mode 升级后，可逐步收敛为 planner → executor → respond。
    """
    # 确保 Agent 已注册到 Registry
    _ensure_agents_registered()

    graph = StateGraph(CopilotState)

    # 添加节点
    graph.add_node("planner", planner_node_async)
    graph.add_node("jd_agent", jd_node_async)
    graph.add_node("profile_agent", profile_node_async)
    graph.add_node("gap_agent", gap_node_async)
    graph.add_node("content_agent", content_node_async)
    graph.add_node("render_agent", render_node_async)
    graph.add_node("interview_trigger", interview_trigger_node_async)
    graph.add_node("respond", _respond)

    # 入口
    graph.set_entry_point("planner")

    # Planner 路由
    graph.add_conditional_edges("planner", _route_after_planner, {
        "jd_agent": "jd_agent",
        "profile_agent": "profile_agent",
        "gap_agent": "gap_agent",
        "content_agent": "content_agent",
        "render_agent": "render_agent",
        "interview_trigger": "interview_trigger",
        "respond": "respond",
    })

    # JD Agent → Content or Respond
    graph.add_conditional_edges("jd_agent", _route_after_jd, {
        "gap_agent": "gap_agent",
        "content_agent": "content_agent",
        "respond": "respond",
    })

    # Profile Agent → Content or Respond
    graph.add_conditional_edges("profile_agent", _route_after_profile, {
        "content_agent": "content_agent",
        "respond": "respond",
    })

    # Gap Agent → Content or Respond
    graph.add_conditional_edges("gap_agent", _route_after_gap, {
        "content_agent": "content_agent",
        "respond": "respond",
    })

    # Content Agent → Render or Respond
    graph.add_conditional_edges("content_agent", _route_after_content, {
        "render_agent": "render_agent",
        "respond": "respond",
    })

    # Render Agent → Respond（interview_agent 已被交互式模拟面试 API 替代）
    graph.add_edge("render_agent", "respond")

    # Interview Trigger → Respond
    graph.add_edge("interview_trigger", "respond")

    # Respond → END
    graph.add_edge("respond", END)

    return graph


def compile_graph():
    """编译并返回可执行图。"""
    g = build_graph()
    return g.compile()
