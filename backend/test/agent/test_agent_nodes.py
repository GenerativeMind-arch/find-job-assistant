"""Agent 节点测试：使用 fixtures 实际文件，且启用 LangSmith 监控。"""
# python -m pytest backend/test/agent/test_agent_nodes.py -sv
from __future__ import annotations

import pytest

from agents.planner import planner_node
from agents.jd_agent import jd_node
from agents.profile_agent import profile_node
from agents.gap_agent import gap_node
from agents.content_agent import content_node
from agents.render_agent import render_node
from agents.interview_trigger import interview_trigger_node
from workflow.state import (
    CopilotState, Job, CandidateProfile, ProfileBasic,
    ResumeContent, ResumeProfile, ResumeContentMeta, SectionItem, Education,
)
from test.test_support import (
    PromptRouterLLM,
    patch_all_agent_llm,
    read_fixture_text,
    ensure_langsmith_enabled,
)


class _FakeResponse:
    def __init__(self, content: str) -> None:
        self.content = content


@pytest.fixture(scope="module", autouse=True)
def _langsmith_monitoring():
    ensure_langsmith_enabled()


@pytest.fixture
def fixture_jd_text() -> str:
    return read_fixture_text("jds", "通用AIGC实习JD.md")


@pytest.fixture
def fixture_profile_text() -> str:
    return read_fixture_text("profiles", "虚拟候选人信息.md")


@pytest.fixture
def fixture_project_text() -> str:
    return read_fixture_text("profiles", "求职Agent_README.md")


def test_planner_node_builds_plan_for_upload_jd(monkeypatch, fixture_jd_text):
    llm = PromptRouterLLM(intent="upload_jd")
    patch_all_agent_llm(monkeypatch, llm)

    state = CopilotState(
        session_id="sess_planner_jd",
        user_message=fixture_jd_text,
        candidate_profile=CandidateProfile(profile_basic=ProfileBasic(name="林知遥")),
    )

    updates = planner_node(state)

    assert updates["current_intent"] == "upload_jd"
    assert updates["execution_plan"] == ["jd_agent", "gap_agent", "content_agent", "render_agent"]
    assert updates["triggered_agents"] == ["jd_agent", "gap_agent", "content_agent", "render_agent"]
    assert updates["workflow_trace"][-1].node == "planner"
    assert updates["workflow_trace"][-1].artifacts["intent"] == "upload_jd"


def test_planner_node_skips_content_when_no_job(monkeypatch, fixture_profile_text):
    llm = PromptRouterLLM(intent="upload_profile")
    patch_all_agent_llm(monkeypatch, llm)

    state = CopilotState(session_id="sess_planner_profile", user_message=fixture_profile_text)
    updates = planner_node(state)

    assert updates["current_intent"] == "upload_profile"
    assert updates["execution_plan"] == ["profile_agent", "content_agent", "render_agent"]


def test_planner_node_routes_gap_analysis_to_gap_agent(monkeypatch):
    llm = PromptRouterLLM(intent="gap_analysis")
    patch_all_agent_llm(monkeypatch, llm)

    state = CopilotState(
        session_id="sess_planner_gap",
        user_message="分析一下我和这个岗位还有哪些差距",
        job=Job(id="job_1", title="AIGC工程师"),
        candidate_profile=CandidateProfile(profile_basic=ProfileBasic(name="林知遥")),
    )
    updates = planner_node(state)

    assert updates["current_intent"] == "gap_analysis"
    assert updates["execution_plan"] == ["gap_agent"]
    assert updates["triggered_agents"] == ["gap_agent"]


def test_planner_node_routes_ask_question_inline(monkeypatch):
    llm = PromptRouterLLM(intent="ask_question")
    patch_all_agent_llm(monkeypatch, llm)

    state = CopilotState(
        session_id="sess_planner_question",
        user_message="我当前的目标岗位是什么？",
        job=Job(id="job_1", title="AIGC工程师"),
    )
    updates = planner_node(state)

    # ask_question 现在内联处理（RAG + LLM 直接回答），不路由 agent
    assert updates["current_intent"] == "ask_question"
    assert updates["execution_plan"] == []
    assert updates["triggered_agents"] == []
    assert updates["reply_message"]


def test_planner_node_routes_interview_start_to_trigger(monkeypatch):
    llm = PromptRouterLLM(intent="interview_start")
    patch_all_agent_llm(monkeypatch, llm)

    state = CopilotState(
        session_id="sess_planner_interview",
        user_message="开始模拟面试",
        job=Job(id="job_1", title="AIGC工程师"),
    )
    updates = planner_node(state)

    assert updates["current_intent"] == "interview_start"
    assert updates["execution_plan"] == ["interview_trigger"]
    assert updates["triggered_agents"] == ["interview_trigger"]


def test_interview_trigger_node_guides_user_when_no_job(monkeypatch):
    llm = PromptRouterLLM(intent="interview_start")
    patch_all_agent_llm(monkeypatch, llm)

    state = CopilotState(session_id="sess_iv_trigger", user_message="开始模拟面试")
    updates = interview_trigger_node(state)

    assert updates["workflow_trace"][-1].status == "skipped"
    assert "岗位" in updates["reply_message"]


def test_interview_trigger_node_ready_when_job_exists(monkeypatch):
    llm = PromptRouterLLM(intent="interview_start")
    patch_all_agent_llm(monkeypatch, llm)

    state = CopilotState(
        session_id="sess_iv_trigger2",
        user_message="开始模拟面试",
        job=Job(id="job_1", title="AIGC工程师"),
    )
    updates = interview_trigger_node(state)

    assert updates["workflow_trace"][-1].status == "success"
    assert "模拟面试" in updates["reply_message"]


def test_jd_node_parses_job_from_fixture(monkeypatch, fixture_jd_text):
    llm = PromptRouterLLM(intent="upload_jd")
    patch_all_agent_llm(monkeypatch, llm)

    state = CopilotState(session_id="sess_jd", user_message=fixture_jd_text)
    updates = jd_node(state)

    job = updates["job"]
    assert job.title == "AIGC工程师"
    assert "RAG" in job.tech_stack
    assert updates["meta"].dirty_flags.content_dirty is True
    assert updates["workflow_trace"][-1].node == "jd_agent"
    assert updates["workflow_trace"][-1].artifacts["job_title"] == "AIGC工程师"


def test_profile_node_updates_candidate_profile(monkeypatch, fixture_profile_text, fixture_project_text):
    llm = PromptRouterLLM(intent="upload_profile")
    patch_all_agent_llm(monkeypatch, llm)

    state = CopilotState(
        session_id="sess_profile",
        user_message=f"{fixture_profile_text}\n\n{fixture_project_text}",
    )
    updates = profile_node(state)

    profile = updates["candidate_profile"]
    assert profile.profile_basic.name == "林知遥"
    assert len(profile.materials) == 1
    assert len(profile.facts) >= 2
    assert updates["workflow_trace"][-1].node == "profile_agent"
    assert updates["workflow_trace"][-1].artifacts["candidate_name"] == "林知遥"


def test_gap_node_generates_gaps_and_questions(monkeypatch, fixture_jd_text):
    llm = PromptRouterLLM(intent="upload_jd")
    patch_all_agent_llm(monkeypatch, llm)

    state = CopilotState(
        session_id="sess_gap",
        user_message=fixture_jd_text,
        job=Job(id="job_1", title="AIGC工程师", source=fixture_jd_text),
        candidate_profile=CandidateProfile(profile_basic=ProfileBasic(name="林知遥")),
    )
    updates = gap_node(state)

    assert updates["gaps"]
    assert updates["questions_to_ask"]
    assert updates["questions_to_ask"][0].status == "pending"
    assert updates["workflow_trace"][-1].node == "gap_agent"
    assert updates["workflow_trace"][-1].artifacts["gap_count"] >= 1


def test_content_and_render_nodes_generate_html(monkeypatch, fixture_jd_text, fixture_profile_text):
    llm = PromptRouterLLM(intent="upload_jd")
    patch_all_agent_llm(monkeypatch, llm)

    state = CopilotState(
        session_id="sess_content_render",
        current_intent="upload_jd",
        user_message=fixture_jd_text,
        job=Job(id="job_1", title="AIGC工程师", source=fixture_jd_text),
        candidate_profile=CandidateProfile(profile_basic=ProfileBasic(name="林知遥")),
    )

    content_updates = content_node(state)
    state = state.model_copy(update=content_updates)

    state = state.model_copy(update={"current_intent": "render_edit", "user_message": "改成双栏布局"})
    render_updates = render_node(state)

    assert content_updates["resume_content_json"].meta.version >= 1
    assert content_updates["workflow_trace"][-1].node == "content_agent"
    assert render_updates["render_config"].layout_mode == "double-column"
    assert "<html" in render_updates["resume_html"].html.lower()
    assert render_updates["meta"].dirty_flags.render_dirty is False
    assert render_updates["workflow_trace"][-1].node == "render_agent"
