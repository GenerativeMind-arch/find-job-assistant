"""模拟面试 API 路由。

端点（均以 /api/interview 为前缀）：
  - POST /api/interview/start     开始一场模拟面试（生成 5 题 + 第 1 题）
  - POST /api/interview/chat      提交回答（文本或语音），返回评分 + 下一题
  - POST /api/interview/end       结束面试，返回综合评价
  - POST /api/interview/transcribe 独立语音转文字
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from agents.interview.engine import InterviewEngine
from agents.interview.contracts import (
    InterviewStartRequest,
    InterviewChatRequest,
    InterviewEndRequest,
    InterviewTranscribeRequest,
    InterviewStartResponse,
    InterviewChatResponse,
    InterviewEndResponse,
)
from storage.redis_client import get_redis_client, RedisSessionStore
from workflow.state import CopilotState
from log import get_logger

logger = get_logger("api")

router = APIRouter(prefix="/api/interview", tags=["模拟面试"])


class _TranscribeOut(BaseModel):
    text: str


async def _get_engine(session_id: str) -> InterviewEngine:
    """构造 InterviewEngine，绑定 Redis 会话存储。"""
    if not session_id:
        raise HTTPException(status_code=400, detail="缺少 session_id")
    redis_client = await get_redis_client()
    store = RedisSessionStore(session_id, redis_client)
    return InterviewEngine(session_id=session_id, store=store)


async def _load_copilot_state(session_id: str) -> CopilotState:
    """从 Redis 加载主流程状态（供 start 端点快照上下文）。"""
    redis_client = await get_redis_client()
    store = RedisSessionStore(session_id, redis_client)
    saved = await store.load_state()
    if saved:
        try:
            return CopilotState.model_validate(saved)
        except Exception as exc:
            logger.warning("CopilotState load failed: %s", exc)
    return CopilotState(session_id=session_id)


@router.post("/start", response_model=InterviewStartResponse)
async def interview_start(req: InterviewStartRequest):
    """开始模拟面试：基于岗位 + 简历 + 知识库生成 5 道题。"""
    engine = await _get_engine(req.session_id)
    state = await _load_copilot_state(req.session_id)

    # 前置条件：需要岗位信息
    if state.job is None:
        raise HTTPException(status_code=400, detail="请先提供目标岗位（搜索或上传 JD）再开始模拟面试")

    try:
        return await engine.start(state)
    except Exception as exc:
        logger.error("Interview start failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"模拟面试启动失败: {exc}")


@router.post("/chat", response_model=InterviewChatResponse)
async def interview_chat(req: InterviewChatRequest):
    """提交一轮回答（文本或语音），返回 Judge 评分 + 下一题。"""
    engine = await _get_engine(req.session_id)
    try:
        return await engine.chat(answer=req.answer, audio_base64=req.audio_base64)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        logger.error("Interview chat failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"回答处理失败: {exc}")


@router.post("/end", response_model=InterviewEndResponse)
async def interview_end(req: InterviewEndRequest):
    """结束面试，生成综合评价。"""
    engine = await _get_engine(req.session_id)
    try:
        return await engine.end()
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        logger.error("Interview end failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"面试总结失败: {exc}")


@router.post("/transcribe", response_model=_TranscribeOut)
async def interview_transcribe(req: InterviewTranscribeRequest):
    """独立语音转文字（也可在 chat 中传 audio_base64 自动转写）。"""
    from agents.interview.audio import transcribe_audio
    try:
        text = await transcribe_audio(req.audio_base64)
        return _TranscribeOut(text=text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.error("Transcribe failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"语音转写失败: {exc}")
