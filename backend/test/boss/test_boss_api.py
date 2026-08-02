"""Boss 模块 API 端点测试。

使用 FastAPI TestClient 测试 /api/boss/ 下的所有端点。
BossManager 被 mock，不实际启动 Playwright 浏览器。
"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

BACKEND_DIR = Path(__file__).resolve().parents[2]
BOSS_DIR = BACKEND_DIR / "boss"
for p in [str(BACKEND_DIR), str(BOSS_DIR)]:
    if p not in sys.path:
        sys.path.insert(0, p)


@pytest.fixture(scope="module")
def client():
    """FastAPI TestClient（仅加载 boss 路由）。"""
    from fastapi import FastAPI
    from api.boss import router as boss_router

    app = FastAPI()
    app.include_router(boss_router)
    return TestClient(app)


@pytest.fixture
def mock_manager(monkeypatch):
    """Mock BossManager 单例，避免实际启动浏览器。"""
    mock = MagicMock()
    mock.automation = None
    mock.monitor_task = None
    mock.monitor_paused = False
    mock.browser_sync_lock = None

    # Mock async methods
    mock.start = AsyncMock(return_value={"status": "started", "login_status": {"logged_in": True}})
    mock.stop = AsyncMock(return_value={"status": "stopped"})
    mock.relogin = AsyncMock(return_value={"status": "ok", "message": "登录成功"})
    mock.heartbeat = AsyncMock(return_value={"status": "ok", "alive": True})
    mock.run_pw = AsyncMock(return_value=None)

    # Mock sync methods
    mock.pause_monitor = MagicMock(return_value={"status": "paused"})
    mock.resume_monitor = MagicMock(return_value={"status": "resumed"})
    mock.get_status = MagicMock(return_value={
        "browser_running": False,
        "monitor_running": False,
        "monitor_paused": False,
        "auto_reply_enabled": False,
        "in_cooldown": False,
        "today_applications": 0,
        "today_auto_replies": 0,
        "daily_limit": 15,
    })

    # Patch the get_instance method
    monkeypatch.setattr("boss.manager.BossManager.get_instance", classmethod(lambda cls: mock))
    return mock


# ── Health & Status 端点 ──

class TestBossHealthAndStatus:
    """测试健康检查和状态端点。"""

    def test_health(self, client):
        """GET /api/boss/health 应返回 200。"""
        resp = client.get("/api/boss/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"

    def test_status(self, client, mock_manager):
        """GET /api/boss/status 应返回状态字典。"""
        resp = client.get("/api/boss/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "browser_running" in data
        assert "monitor_running" in data
        assert "today_applications" in data
        assert "daily_limit" in data


# ── 系统控制端点 ──

class TestBossSystemControl:
    """测试系统控制端点（启动/停止/登录/心跳）。"""

    def test_start(self, client, mock_manager):
        """POST /api/boss/system/start 应返回启动状态。"""
        resp = client.post("/api/boss/system/start")
        assert resp.status_code == 200
        mock_manager.start.assert_called_once()

    def test_stop(self, client, mock_manager):
        """POST /api/boss/system/stop 应返回停止状态。"""
        resp = client.post("/api/boss/system/stop")
        assert resp.status_code == 200
        mock_manager.stop.assert_called_once()

    def test_relogin(self, client, mock_manager):
        """POST /api/boss/system/relogin 应返回登录状态。"""
        resp = client.post("/api/boss/system/relogin")
        assert resp.status_code == 200
        mock_manager.relogin.assert_called_once()

    def test_heartbeat(self, client, mock_manager):
        """POST /api/boss/system/heartbeat 应返回心跳状态。"""
        resp = client.post("/api/boss/system/heartbeat")
        assert resp.status_code == 200

    def test_pause_monitor(self, client, mock_manager):
        """POST /api/boss/monitor/pause 应返回暂停状态。"""
        resp = client.post("/api/boss/monitor/pause")
        assert resp.status_code == 200
        assert resp.json()["status"] == "paused"

    def test_resume_monitor(self, client, mock_manager):
        """POST /api/boss/monitor/resume 应返回恢复状态。"""
        resp = client.post("/api/boss/monitor/resume")
        assert resp.status_code == 200
        assert resp.json()["status"] == "resumed"


# ── 岗位搜索 & 投递端点 ──

class TestBossJobs:
    """测试岗位搜索和投递端点。"""

    def test_search_jobs(self, client, mock_manager):
        """POST /api/boss/jobs/search 应返回岗位列表。"""
        # 模拟浏览器已启动
        mock_manager.automation = MagicMock()
        mock_manager.automation.page = MagicMock()
        mock_manager.run_pw = AsyncMock(return_value=[
            {"job_url": "https://test.com/1", "job_title": "Python开发", "company": "测试公司", "salary": "15-25K"}
        ])
        resp = client.post("/api/boss/jobs/search", json={
            "keyword": "Python",
            "city": "上海",
            "welfare": "",
            "limit": 10,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "jobs" in data

    def test_list_jobs(self, client):
        """GET /api/boss/jobs 应返回岗位列表。"""
        resp = client.get("/api/boss/jobs")
        assert resp.status_code == 200
        data = resp.json()
        assert "jobs" in data

    def test_scan_page(self, client, mock_manager):
        """POST /api/boss/jobs/scan 应返回扫描结果。"""
        # 模拟浏览器已启动
        mock_manager.automation = MagicMock()
        mock_manager.automation.page = MagicMock()
        mock_manager.run_pw = AsyncMock(return_value=[])
        resp = client.post("/api/boss/jobs/scan")
        assert resp.status_code == 200

    def test_analyze_job(self, client, mock_manager):
        """POST /api/boss/jobs/analyze 应返回分析结果。"""
        mock_manager.run_pw = AsyncMock(return_value={
            "match_score": 85,
            "decision": "recommend",
            "gap": "需要加强 React 经验",
        })
        resp = client.post("/api/boss/jobs/analyze", json={
            "job_url": "https://test.com/1",
            "job_title": "全栈工程师",
            "company": "测试公司",
            "description": "需要 Python + React",
        })
        assert resp.status_code == 200


# ── 会话端点 ──

class TestBossConversations:
    """测试会话相关端点。"""

    def test_list_conversations(self, client):
        """GET /api/boss/conversations 应返回会话列表。"""
        resp = client.get("/api/boss/conversations")
        assert resp.status_code == 200
        data = resp.json()
        assert "conversations" in data

    def test_conversation_messages(self, client):
        """GET /api/boss/conversations/{id}/messages 应返回消息列表。"""
        resp = client.get("/api/boss/conversations/1/messages")
        assert resp.status_code == 200
        data = resp.json()
        assert "messages" in data


# ── 统计 & 设置端点 ──

class TestBossStatsAndSettings:
    """测试统计和设置端点。"""

    def test_stats(self, client):
        """GET /api/boss/stats 应返回统计数据。"""
        resp = client.get("/api/boss/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert "today_applications" in data

    def test_get_settings(self, client):
        """GET /api/boss/settings 应返回设置。"""
        resp = client.get("/api/boss/settings")
        assert resp.status_code == 200
        data = resp.json()
        assert "settings" in data

    def test_update_settings(self, client):
        """PUT /api/boss/settings 应更新设置。"""
        resp = client.put("/api/boss/settings", json={
            "daily_apply_limit": "20",
            "auto_reply_enabled": "true",
        })
        assert resp.status_code == 200

    def test_shortlists(self, client):
        """GET /api/boss/shortlists 应返回收藏列表。"""
        resp = client.get("/api/boss/shortlists")
        assert resp.status_code == 200
        data = resp.json()
        assert "shortlists" in data

    def test_geo_cities(self, client):
        """GET /api/boss/geo/cities 应返回城市列表。"""
        resp = client.get("/api/boss/geo/cities")
        assert resp.status_code == 200
        data = resp.json()
        assert "cities" in data
        assert len(data["cities"]) > 0
