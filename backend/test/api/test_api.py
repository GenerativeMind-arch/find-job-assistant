# 示例: 在项目根目录执行 pytest backend/test/api/test_api.py -sv
"""FastAPI API 接口测试。

使用 TestClient 测试各 API 路由的请求/响应结构。
"""

import json

import pytest
from fastapi.testclient import TestClient

from main import app
from workflow.state import CopilotState, Gap, InterviewQA, Job, Question


@pytest.fixture(scope="module")
def client():
    """FastAPI TestClient。"""
    return TestClient(app)


def _patch_resume_state_store(monkeypatch, saved_state):
    class DummyStore:
        def __init__(self, session_id, client=None):
            self.session_id = session_id

        async def load_state(self):
            return saved_state

    async def dummy_get_redis_client():
        return object()

    monkeypatch.setattr("api.resume.get_redis_client", dummy_get_redis_client)
    monkeypatch.setattr("api.resume.RedisSessionStore", DummyStore)


def _patch_export_state_store(monkeypatch, saved_state):
    class DummyStore:
        def __init__(self, session_id, client=None):
            self.session_id = session_id

        async def load_state(self):
            return saved_state

    async def dummy_get_redis_client():
        return object()

    monkeypatch.setattr("api.export.get_redis_client", dummy_get_redis_client)
    monkeypatch.setattr("api.export.RedisSessionStore", DummyStore)


class TestHealthEndpoint:

    def test_health_check(self, client):
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


class TestChatEndpoint:

    def test_chat_requires_message(self, client):
        """不提供 message 应返回 422。"""
        response = client.post("/api/chat", json={})
        assert response.status_code == 422

    def test_chat_runs_real_workflow_and_saves_session_snapshot(self, client, monkeypatch):
        from unittest.mock import AsyncMock

        store = AsyncMock()
        store.load_state.return_value = None
        archive = AsyncMock()
        monkeypatch.setattr("api.chat.get_redis_client", AsyncMock(return_value=object()))
        monkeypatch.setattr("api.chat.RedisSessionStore", lambda *args: store)
        monkeypatch.setattr("api.chat.archive_session", archive)

        classify = AsyncMock(side_effect=AssertionError("small talk should skip the LLM"))
        monkeypatch.setattr("agents.planner._classify_intent_async", classify)
        response = client.post("/api/chat", json={"session_id": "snapshot", "message": "你好"})

        assert response.status_code == 200
        assert "你好" in response.json()["reply_message"]
        classify.assert_not_awaited()
        snapshot = store.save_state.await_args.args[0]
        assert snapshot["session_id"] == "snapshot"
        assert "notice" not in snapshot
        assert "workflow_trace" not in snapshot
        archive.assert_awaited_once()


class TestResumeEndpoints:

    def test_get_content_without_session(self, client, monkeypatch):
        """不存在的 session 应返回 404。"""
        _patch_resume_state_store(monkeypatch, None)
        response = client.get("/api/resume/content", params={"session_id": "nonexistent_session"})
        assert response.status_code == 404

    def test_get_html_without_session(self, client, monkeypatch):
        _patch_resume_state_store(monkeypatch, None)
        response = client.get("/api/resume/html", params={"session_id": "nonexistent_session"})
        assert response.status_code == 404

    def test_preview_without_session(self, client, monkeypatch):
        _patch_resume_state_store(monkeypatch, None)
        response = client.get("/api/resume/preview", params={"session_id": "nonexistent_session"})
        assert response.status_code == 404

    def test_render_requires_session(self, client, monkeypatch):
        _patch_resume_state_store(monkeypatch, None)
        response = client.post("/api/resume/render", json={
            "session_id": "nonexistent_session",
            "render_instruction": "改大字号",
        })
        assert response.status_code == 404


class TestExportEndpoint:

    def test_export_without_session(self, client, monkeypatch):
        _patch_export_state_store(monkeypatch, None)
        response = client.post("/api/export", json={
            "session_id": "nonexistent_session",
            "format": "html",
        })
        assert response.status_code == 404

    def test_export_unsupported_format(self, client, monkeypatch):
        """不支持的格式应返回 400（如果 session 存在）或 404。"""
        _patch_export_state_store(monkeypatch, None)
        response = client.post("/api/export", json={
            "session_id": "nonexistent_session",
            "format": "xml",
        })
        # session 不存在时先返回 404
        assert response.status_code in (400, 404)

    def test_export_job_txt(self, client, monkeypatch):
        saved_state = CopilotState(
            session_id="sess_export_job",
            job=Job(
                id="job_1",
                title="AI Engineer Intern",
                industry="Internet",
                tech_stack=["Python", "LangChain"],
                hard_skills=["RAG"],
                responsibilities=["Build AI workflows"],
            ),
        ).model_dump()

        _patch_export_state_store(monkeypatch, saved_state)

        response = client.post("/api/export", json={
            "session_id": "sess_export_job",
            "target": "job",
            "format": "txt",
        })

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/plain")
        assert "岗位解析" in response.text
        assert "AI Engineer Intern" in response.text

    def test_export_gaps_json(self, client, monkeypatch):
        saved_state = CopilotState(
            session_id="sess_export_gaps",
            gaps=[
                Gap(
                    id="gap_1",
                    type="missing_skill",
                    severity="high",
                    description="缺少生产级 RAG 经验",
                )
            ],
            questions_to_ask=[
                Question(
                    id="q_1",
                    question="你是否做过线上 RAG 项目？",
                    reason="确认项目深度",
                    target_field="projects",
                    priority="high",
                )
            ],
        ).model_dump()

        _patch_export_state_store(monkeypatch, saved_state)

        response = client.post("/api/export", json={
            "session_id": "sess_export_gaps",
            "target": "gaps",
            "format": "json",
        })

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/json")
        payload = json.loads(response.text)
        assert payload["gaps"][0]["description"] == "缺少生产级 RAG 经验"
        assert payload["questions_to_ask"][0]["target_field"] == "projects"

    def test_export_interview_markdown(self, client, monkeypatch):
        saved_state = CopilotState(
            session_id="sess_export_interview",
            interview_qa=[
                InterviewQA(
                    id="qa_1",
                    category="technical",
                    question="How do you reduce hallucination in RAG?",
                    answer="I improve retrieval quality, grounding, and evaluation.",
                    source_refs=["proj_1"],
                )
            ],
        ).model_dump()

        _patch_export_state_store(monkeypatch, saved_state)

        response = client.post("/api/export", json={
            "session_id": "sess_export_interview",
            "target": "interview",
            "format": "md",
        })

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/markdown")
        assert "# 面试问答" in response.text
        assert "How do you reduce hallucination in RAG?" in response.text
