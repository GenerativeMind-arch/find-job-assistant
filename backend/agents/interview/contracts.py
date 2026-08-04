"""模拟面试模块 — Pydantic 数据模型。

包含面试会话状态、轮次、题目、Judge 评分结果、API 契约等数据结构。
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


# ── 面试题目 ──────────────────────────────────────────────

class InterviewQuestion(BaseModel):
    """一道面试题。"""
    id: str
    category: str = "技术"          # 题目类别（技术/项目/行为/开放）
    question: str                   # 问题内容
    difficulty: str = "medium"      # easy / medium / hard
    source_kb_refs: list[str] = Field(default_factory=list)  # 知识库参考来源


# ── Judge 评分结果 ────────────────────────────────────────

class DimensionScores(BaseModel):
    """四维度评分（每项 0-10 分）。"""
    accuracy: float = 0.0      # 准确性
    depth: float = 0.0         # 深度
    expression: float = 0.0    # 表达能力
    relevance: float = 0.0     # 匹配度


class JudgeOutput(BaseModel):
    """Judge Agent 对单道回答的评估结果。"""
    overall_score: float = 0.0     # 总分 0-10
    dimension_scores: DimensionScores = Field(default_factory=DimensionScores)
    strength: str = ""             # 做得好的方面
    weakness: str = ""             # 薄弱环节
    suggestion: str = ""           # 学习建议
    category: str = "技术"          # 题目类别


# ── 面试轮次 ──────────────────────────────────────────────

class InterviewTurn(BaseModel):
    """一轮问答记录。"""
    round: int
    question: InterviewQuestion
    user_answer: str = ""
    judge_result: Optional[JudgeOutput] = None


# ── 面试会话状态 ──────────────────────────────────────────

class InterviewSessionState(BaseModel):
    """面试会话状态，整体序列化后存 Redis。"""
    session_id: str
    status: str = "active"          # active / ended
    round_count: int = 0
    max_rounds: int = 5
    history: list[InterviewTurn] = Field(default_factory=list)

    # 上下文快照（面试期间只读）
    job_title: str = ""
    job_json: str = ""              # 序列化 Job
    resume_json: str = ""           # 序列化 ResumeContent
    profile_json: str = ""          # 序列化 CandidateProfile

    # 预生成的题目池
    question_pool: list[InterviewQuestion] = Field(default_factory=list)

    # 动态类别权重（弱项类别出题权重更高）
    category_weights: dict[str, float] = Field(default_factory=dict)

    # 累计统计
    total_score: float = 0.0
    created_at: str = ""
    updated_at: str = ""

    # 面试官开场白（首轮返回给用户）
    opening_message: str = ""


# ── API 契约 ──────────────────────────────────────────────

class InterviewStartRequest(BaseModel):
    session_id: str


class InterviewChatRequest(BaseModel):
    session_id: str
    answer: str = ""
    audio_base64: Optional[str] = None


class InterviewEndRequest(BaseModel):
    session_id: str


class InterviewTranscribeRequest(BaseModel):
    audio_base64: str


class InterviewStartResponse(BaseModel):
    session_id: str
    round: int = 1
    total_rounds: int = 5
    message: str = ""
    question: Optional[InterviewQuestion] = None


class InterviewChatResponse(BaseModel):
    session_id: str
    round: int = 1
    total_rounds: int = 5
    judge: Optional[JudgeOutput] = None
    next_question: Optional[InterviewQuestion] = None
    is_last: bool = False
    message: str = ""


class InterviewEndResponse(BaseModel):
    session_id: str
    total_rounds: int = 0
    avg_score: float = 0.0
    max_score: float = 0.0
    min_score: float = 0.0
    detailed_scores: list[dict] = Field(default_factory=list)
    strong_areas: list[str] = Field(default_factory=list)
    weak_areas: list[str] = Field(default_factory=list)
    summary: str = ""
    recommendations: list[str] = Field(default_factory=list)
