"""Agent 契约声明 — 将 docs/agents_contract.md 中的约定落地为可执行规则。

每个 Agent 声明：
- allowed_reads: 允许读取的状态字段
- allowed_writes: 允许写入的状态字段
- forbidden_writes: 严格禁止写入的字段
- is_idempotent: 是否幂等（支持安全重试）
"""

from __future__ import annotations

from typing import Optional


class AgentContract:
    """Agent 能力与约束声明。"""

    __slots__ = ("name", "description", "allowed_reads", "allowed_writes",
                 "forbidden_writes", "actions", "is_idempotent")

    def __init__(
        self,
        name: str,
        description: str = "",
        allowed_reads: list[str] | None = None,
        allowed_writes: list[str] | None = None,
        forbidden_writes: list[str] | None = None,
        actions: list[str] | None = None,
        is_idempotent: bool = True,
    ):
        self.name = name
        self.description = description
        self.allowed_reads = allowed_reads or []
        self.allowed_writes = allowed_writes or []
        self.forbidden_writes = forbidden_writes or []
        self.actions = actions or ["execute"]
        self.is_idempotent = is_idempotent

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "allowed_reads": self.allowed_reads,
            "allowed_writes": self.allowed_writes,
            "forbidden_writes": self.forbidden_writes,
            "actions": self.actions,
            "is_idempotent": self.is_idempotent,
        }


# ── 所有 Agent 的契约定义 ──────────────────────────────────────

_CONTRACTS: dict[str, AgentContract] = {
    "jd_agent": AgentContract(
        name="jd_agent",
        description="解析岗位描述（JD），输出结构化 Job 对象",
        allowed_reads=["user_message", "job"],
        allowed_writes=["job"],
        forbidden_writes=["candidate_profile", "resume_content_json", "render_config", "resume_html", "interview_qa", "gaps", "questions_to_ask"],
    ),
    "profile_agent": AgentContract(
        name="profile_agent",
        description="解析用户材料，输出结构化 CandidateProfile",
        allowed_reads=["user_message", "candidate_profile", "user_attachments"],
        allowed_writes=["candidate_profile"],
        forbidden_writes=["job", "resume_content_json", "render_config", "resume_html", "interview_qa"],
    ),
    "gap_agent": AgentContract(
        name="gap_agent",
        description="对比 Job 和 CandidateProfile，输出 Gap 和 Question 列表",
        allowed_reads=["job", "candidate_profile"],
        allowed_writes=["gaps", "questions_to_ask"],
        forbidden_writes=["resume_content_json", "render_config", "resume_html", "interview_qa", "candidate_profile", "job"],
    ),
    "content_agent": AgentContract(
        name="content_agent",
        description="生成/更新简历内容 JSON",
        allowed_reads=["job", "candidate_profile", "gaps", "resume_content_json", "user_message"],
        allowed_writes=["resume_content_json"],
        forbidden_writes=["render_config", "resume_html", "interview_qa", "job", "candidate_profile"],
    ),
    "render_agent": AgentContract(
        name="render_agent",
        description="更新渲染配置并生成 HTML 简历",
        allowed_reads=["resume_content_json", "render_config", "render_config", "user_message"],
        allowed_writes=["render_config", "resume_html"],
        forbidden_writes=["job", "candidate_profile", "resume_content_json", "interview_qa"],
    ),
    "interview_agent": AgentContract(
        name="interview_agent",
        description="生成面试问答集（可 RAG 增强）",
        allowed_reads=["job", "candidate_profile", "resume_content_json"],
        allowed_writes=["interview_qa"],
        forbidden_writes=["resume_html", "render_config", "job", "candidate_profile", "resume_content_json"],
    ),
    "question_agent": AgentContract(
        name="question_agent",
        description="基于当前 State 回答自由问题（可 RAG 增强）",
        allowed_reads=["*"],  # 读取所有业务字段
        allowed_writes=[],     # 只写 workflow_trace，不写业务字段
        forbidden_writes=["job", "candidate_profile", "resume_content_json", "render_config", "resume_html", "interview_qa", "gaps", "questions_to_ask"],
    ),
}

# 注册静态方法方便查找
AgentContract.get = staticmethod(lambda name: _CONTRACTS.get(name))
AgentContract.list_all = staticmethod(lambda: sorted(_CONTRACTS.keys()))
