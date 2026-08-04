"""Interview Trigger — 模拟面试意图的引导节点。

用户说"开始模拟面试"时，Planner 分类为 interview_start，
路由到本节点。本节点只做前置检查并返回引导语，真正的面试流程
由独立的 POST /api/interview/* 端点驱动（交互式多轮，不适合 LangGraph 单次 DAG）。
"""

from __future__ import annotations

import asyncio
from typing import Any

from workflow.state import CopilotState
from workflow.trace import append_trace
from log import get_logger

logger = get_logger("agent")


async def interview_trigger_node_async(state: CopilotState) -> dict[str, Any]:
    """面试引导节点：校验前置条件 + 返回引导语。"""
    logger.info("Interview Trigger started for session %s", state.session_id)

    # 前置条件：需要岗位信息
    if state.job is None or not state.job.title:
        return {
            "reply_message": (
                "要开始模拟面试，请先提供目标岗位信息：\n"
                "1. 在对话区粘贴 JD 文本，或\n"
                "2. 在「BOSS」面板搜索岗位后点击「分析此岗位」\n\n"
                "有了岗位信息后，再对我说「开始模拟面试」即可。"
            ),
            "workflow_trace": append_trace(
                state,
                node="interview_trigger",
                status="skipped",
                input_summary="用户请求开始模拟面试",
                output_summary="缺少岗位信息，无法开始模拟面试",
                artifacts={"has_job": False, "interview_ready": False},
            ),
        }

    # 正常：返回引导语，前端将切换到 /api/interview/* 交互流程
    return {
        "reply_message": (
            f"好的，已为你准备模拟面试！\n\n"
            f"目标岗位：**{state.job.title}**\n"
            f"题目数量：5 道（技术/项目/行为混合）\n"
            f"作答方式：文字或语音输入\n\n"
            f"即将开始——我会逐题提问，你回答后我会给出四维度评分和针对性反馈。"
        ),
        "workflow_trace": append_trace(
            state,
            node="interview_trigger",
            input_summary="用户请求开始模拟面试",
            output_summary="面试触发成功，前端将切换到 /api/interview/* 流程",
            artifacts={
                "has_job": True,
                "interview_ready": True,
                "job_title": state.job.title,
            },
        ),
    }


def interview_trigger_node(state: CopilotState) -> dict[str, Any]:
    """Interview Trigger 同步兼容入口。"""
    return asyncio.run(interview_trigger_node_async(state))
