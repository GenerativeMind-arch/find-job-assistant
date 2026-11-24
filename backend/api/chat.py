"""POST /api/chat — 主对话接口。"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel, Field

from api.chat_input import prepare_chat_input
from workflow.graph import run_workflow
from workflow.state import CopilotState
from storage.redis_client import get_redis_client, RedisSessionStore
from storage.session_archive import archive_session
from log import get_logger

logger = get_logger("api")

router = APIRouter(prefix="/api", tags=["chat"])

class ChatRequest(BaseModel):
    session_id: str = ""
    message: str
    attachments: list[dict[str, Any]] = Field(default_factory=list)


class ChatResponse(BaseModel):
    session_id: str
    reply_message: str = ""
    notice: str = ""
    job: dict | None = None
    gaps: list[dict] = Field(default_factory=list)
    questions_to_ask: list[dict] = Field(default_factory=list)
    resume_content_json: dict | None = None
    render_config: dict | None = None
    resume_html: dict | None = None
    interview_qa: list[dict] = Field(default_factory=list)
    triggered_agents: list[str] = Field(default_factory=list)


@router.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest, background_tasks: BackgroundTasks) -> ChatResponse:
    """主对话接口。"""
    session_id = req.session_id or f"sess_{uuid.uuid4().hex[:16]}"
    logger.info("Chat request: session=%s, msg_len=%d", session_id, len(req.message))

    # 从 Redis 加载或创建状态
    redis_client = await get_redis_client()
    store = RedisSessionStore(session_id, redis_client)
    saved_state = await store.load_state()

    if saved_state:
        state = CopilotState.model_validate(saved_state)
    else:
        state = CopilotState(session_id=session_id)

    # 注入用户输入
    prepared_input = prepare_chat_input(req.message, req.attachments)
    state.user_message = prepared_input.user_message
    state.user_attachments = prepared_input.user_attachments

    # 执行 workflow graph，加上config指定langsmith的run_name，方便在LangSmith上查看每次API调用的执行详情
    try:
        final_state = await run_workflow(state, config={"run_name": f"API-Chat-Request: {session_id}"})
    except Exception as e:
        logger.error("Workflow execution failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"处理失败: {e}")

    # 持久化到 Redis
    await store.save_state(final_state.to_session_snapshot())

    # 后台持久化到 MySQL，避免同步阻塞请求主链。
    background_tasks.add_task(archive_session, final_state)

    return ChatResponse(
        session_id=session_id,
        reply_message=final_state.reply_message,
        notice=final_state.notice,
        job=final_state.job.model_dump() if final_state.job else None,
        gaps=[g.model_dump() for g in final_state.gaps],
        questions_to_ask=[q.model_dump() for q in final_state.questions_to_ask],
        resume_content_json=final_state.resume_content_json.model_dump() if final_state.resume_content_json else None,
        render_config=final_state.render_config.model_dump(),
        resume_html=final_state.resume_html.model_dump(),
        interview_qa=[qa.model_dump() for qa in final_state.interview_qa],
        triggered_agents=final_state.triggered_agents,
    )
