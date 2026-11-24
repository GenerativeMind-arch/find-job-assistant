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
    from api.boss import browser_wedged_handler, router as boss_router
    from boss.manager import BrowserWedgedError

    app = FastAPI()
    app.include_router(boss_router)
    # 与 main.py 保持一致：卡死要回 503 + 处置办法，而不是无信息量的 500
    app.add_exception_handler(BrowserWedgedError, browser_wedged_handler)
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
    # 批量投递现在是 manager 上的异步编排（每条岗位一次 run_pw），不再是一次 run_pw
    mock.apply_batch_async = AsyncMock(return_value=[])

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


@pytest.fixture(autouse=True)
def _no_real_llm(monkeypatch):
    """挡住真实 LLM 调用。

    /jobs/search 默认会用 LLM 复核岗位相关性 —— 不挡的话每次跑搜索相关的测试都会
    真的打一次 DeepSeek（实测单个 test_search_jobs 要 7s），既慢又依赖网络和 Key。
    """
    import models.llm

    async def _fake(system, user):
        return '{"mismatch": []}'

    monkeypatch.setattr(models.llm, "acall_llm", _fake)


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

    def test_update_settings_only_touches_sent_keys(self, client, monkeypatch):
        """只写调用方传了的字段。

        前端改成"只提交改动过的字段"，就依赖这条：漏掉任何一个字段都不能被
        清掉或改回默认值 —— 否则又会重演"改了 A，B 被旧快照覆盖"。
        """
        import api.boss as boss_api

        written = {}
        monkeypatch.setattr(boss_api, "set_setting", lambda k, v: written.__setitem__(k, v))

        resp = client.put("/api/boss/settings", json={"daily_apply_limit": "20"})

        assert resp.status_code == 200
        assert written == {"daily_apply_limit": "20"}

    def test_update_settings_writes_empty_string_for_text_field(self, client, monkeypatch):
        """空串是合法值：清空简历摘要 / 微信号必须真落库。

        以前 `v != ""` 的过滤把空串当成"没传"，这类清空操作被静默丢弃 ——
        用户改了设置，界面上也没有任何提示说它没生效。
        """
        import api.boss as boss_api

        written = {}
        monkeypatch.setattr(boss_api, "set_setting", lambda k, v: written.__setitem__(k, v))

        resp = client.put("/api/boss/settings", json={"resume_summary": ""})

        assert resp.status_code == 200
        assert written == {"resume_summary": ""}

    def test_update_settings_rejects_blank_numeric(self, client, monkeypatch):
        """数值项写成空串要当场 400。

        读取端是裸的 int(get_setting(...))，写进空串会让 /api/boss/status 直接
        500 —— 与其留给下一次轮询去炸，不如在入口把问题告诉调用方。
        """
        import api.boss as boss_api

        written = {}
        monkeypatch.setattr(boss_api, "set_setting", lambda k, v: written.__setitem__(k, v))

        resp = client.put("/api/boss/settings", json={"daily_apply_limit": ""})

        assert resp.status_code == 400
        assert "daily_apply_limit" in resp.json()["detail"]
        assert written == {}, "被拒绝的值不能落库"

    def test_send_returns_persisted_message(self, client, mock_manager, temp_db):
        """POST /conversations/{id}/send 要把落库后的那一行回带回来。

        前端靠它本地追加，省掉一次"发完再整段拉取"；自己拼时间戳的话显示的时间
        会和库里对不上，所以必须回真实的 id / created_at。
        """
        from boss_state import get_messages, get_or_create_conversation

        mock_manager.automation = MagicMock()
        mock_manager.automation.page = MagicMock()
        mock_manager.run_pw = AsyncMock(return_value=True)
        conv_id = get_or_create_conversation(None, "回带测试HR", "回带测试公司", "Agent")

        resp = client.post(f"/api/boss/conversations/{conv_id}/send", json={"content": "你好"})

        assert resp.status_code == 200
        sent = resp.json()["sent_message"]
        assert sent["content"] == "你好"
        assert sent["sender"] == "me"
        assert sent["id"] and sent["created_at"], "必须回带真实的 id/created_at"
        assert [m["content"] for m in get_messages(conv_id, 10)] == ["你好"]
        assert "warning" not in resp.json(), "确认发出去了就不该报警告"

    def test_send_warns_when_delivery_unconfirmed(self, client, mock_manager, temp_db):
        """没能确认发出时：回 200 + warning，而且**什么都不落库**。

        send_message 靠"消息区里出现了这段文字"来判断成功，页面重渲染慢或选择器
        失配都会误报失败，此时消息很可能其实已经发出去了。报错会让用户重发、可能
        发成两条；静默成功又会让用户以为发过了 —— 所以两个极端都不能走。

        不落库是因为这条错账不自愈：一旦记下，会话被标成 has_unreplied=0，自动
        回复就以为 HR 的消息回过了。反过来漏记一条真发出去的消息，下一轮扫描的
        replace_conversation_messages 会按 DOM 整段重写，能自愈。
        """
        from boss_state import (
            get_conversation,
            get_messages,
            get_or_create_conversation,
            replace_conversation_messages,
        )

        mock_manager.automation = MagicMock()
        mock_manager.automation.page = MagicMock()
        # 第一次 run_pw 是打开会话（必须成功），第二次是发消息（返回未确认）
        mock_manager.run_pw = AsyncMock(side_effect=[True, False])
        conv_id = get_or_create_conversation(None, "未确认测试HR", "未确认测试公司", "Agent")
        replace_conversation_messages(conv_id, [{"sender": "hr", "content": "在吗", "status": ""}])
        assert get_conversation(conv_id)["has_unreplied"] == 1

        resp = client.post(f"/api/boss/conversations/{conv_id}/send", json={"content": "你好"})

        assert resp.status_code == 200
        assert "warning" in resp.json()
        assert "sent_message" not in resp.json(), "没确认发出就不该回带消息体"
        assert [m["content"] for m in get_messages(conv_id, 10)] == ["在吗"], "不能落库"
        assert get_conversation(conv_id)["has_unreplied"] == 1, "不能把会话标成已回复"

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


# ── 一键投递（scan-and-apply）语义 ──

class TestBossScanAndApply:
    """一键投递改为「投岗位库里 status=pending 的岗位」。

    旧实现扫的是**浏览器当前页**的 DOM，而 apply_to_job 会 goto 到岗位详情页且不
    返回 —— 投过任意一个岗位之后「当前页」就不再是搜索结果页，扫描恒为 0。
    这些用例锁住新语义：投递集合来自数据库，与页面无关。
    """

    @staticmethod
    def _settings(monkeypatch, **overrides):
        values = {"daily_apply_limit": "15"}
        values.update(overrides)
        monkeypatch.setattr(
            "api.boss.get_setting", lambda k, d=None: values.get(k, d if d is not None else "")
        )

    @staticmethod
    def _allowance(monkeypatch, **overrides):
        """把「今天还能投几个」钉死成指定值。

        投递额度自 2026-10-13 起由 get_apply_allowance() 统一提供（自适应预算），
        端点不再自己拿 daily_limit 算减法 —— 所以测试要钉的是这个函数。
        """
        base = {
            "budget": 20, "applied": 0, "remaining": 20, "base": 20, "max": 50,
            "strikes": 0, "delay_multiplier": 1.0, "dynamic": True,
            "day_over": False, "reason": "",
        }
        base.update(overrides)
        monkeypatch.setattr("api.boss.get_apply_allowance", lambda: base)
        return base

    def test_applies_pending_jobs_from_library(self, client, mock_manager, monkeypatch, temp_db):
        pending = [
            {"status": "pending", "job_url": "https://test.com/1"},
            {"status": "pending", "job_url": "https://test.com/2"},
        ]
        mock_manager.automation = MagicMock()
        mock_manager.apply_batch_async = AsyncMock(return_value=[{"success": True}, {"success": False}])
        monkeypatch.setattr("api.boss.list_applications", lambda status=None, limit=50: pending)
        monkeypatch.setattr("api.boss.get_today_application_count", lambda: 0)
        self._settings(monkeypatch)

        resp = client.post("/api/boss/jobs/scan-and-apply", json={})
        assert resp.status_code == 200
        data = resp.json()
        assert data["scanned"] == 2
        assert data["applied"] == 1  # 只有第一个 success=True
        assert data["success"] is True

        # apply_batch_async(urls, greeting)：urls 必须来自库里的 job_url
        assert mock_manager.apply_batch_async.await_args.args[0] == [
            "https://test.com/1",
            "https://test.com/2",
        ]

    def test_only_pending_status_is_requested(self, client, mock_manager, monkeypatch, temp_db):
        """不能再退回"扫页面"——必须按 status=pending 查库。"""
        mock_manager.automation = MagicMock()
        mock_manager.run_pw = AsyncMock(return_value=[])
        seen = {}

        def _list(status=None, limit=50):
            seen["status"] = status
            return []

        monkeypatch.setattr("api.boss.list_applications", _list)
        monkeypatch.setattr("api.boss.get_today_application_count", lambda: 0)
        self._settings(monkeypatch)

        resp = client.post("/api/boss/jobs/scan-and-apply", json={})
        assert resp.status_code == 200
        assert seen["status"] == "pending"
        assert resp.json()["scanned"] == 0

    def test_empty_library_does_not_touch_browser(self, client, mock_manager, monkeypatch, temp_db):
        mock_manager.automation = MagicMock()
        mock_manager.run_pw = AsyncMock(return_value=[])
        monkeypatch.setattr("api.boss.list_applications", lambda status=None, limit=50: [])
        monkeypatch.setattr("api.boss.get_today_application_count", lambda: 0)
        self._settings(monkeypatch)

        resp = client.post("/api/boss/jobs/scan-and-apply", json={})
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert "没有待投递" in data["message"]
        mock_manager.run_pw.assert_not_awaited()

    def test_caps_to_daily_remaining(self, client, mock_manager, monkeypatch, temp_db):
        pending = [{"status": "pending", "job_url": f"https://test.com/{i}"} for i in range(5)]
        mock_manager.automation = MagicMock()
        monkeypatch.setattr("api.boss.list_applications", lambda status=None, limit=50: pending)
        monkeypatch.setattr("api.boss.get_today_application_count", lambda: 13)
        self._settings(monkeypatch)
        self._allowance(monkeypatch, budget=15, applied=13, remaining=2)  # 只剩 2 个额度

        client.post("/api/boss/jobs/scan-and-apply", json={})
        assert len(mock_manager.apply_batch_async.await_args.args[0]) == 2

    def test_daily_limit_returns_429(self, client, mock_manager, monkeypatch, temp_db):
        mock_manager.automation = MagicMock()
        monkeypatch.setattr("api.boss.get_today_application_count", lambda: 15)
        self._settings(monkeypatch)
        self._allowance(
            monkeypatch, budget=20, applied=20, remaining=0,
            day_over=True, reason="今日预算已用完",
        )

        resp = client.post("/api/boss/jobs/scan-and-apply", json={})
        assert resp.status_code == 429


# ── 搜索结果的相关性 LLM 复核 ──

class TestBossSearchLLMFilter:
    """token 命中判断不了语义，所以整批岗位再交给 LLM 复核一遍，只删明显不相关的。"""

    @staticmethod
    def _settings(monkeypatch, **overrides):
        values = {
            "relevance_rank_enabled": "true",
            "relevance_filter_enabled": "false",
            "relevance_min_score": "3",
            "relevance_llm_enabled": "true",
        }
        values.update(overrides)
        monkeypatch.setattr(
            "api.boss.get_setting", lambda k, d=None: values.get(k, d if d is not None else "")
        )

    @staticmethod
    def _arm_search(mock_manager):
        mock_manager.automation = MagicMock()
        mock_manager.automation.page = MagicMock()
        mock_manager.run_pw = AsyncMock(
            return_value=[
                {"title": "AI应用开发工程师", "url": "https://test.com/1", "description": "做大模型应用"},
                {"title": "外贸业务员", "url": "https://test.com/2", "description": "用AI工具获客"},
                {"title": "行政专员", "url": "https://test.com/3", "description": "日常行政事务"},
            ]
        )

    def test_llm_flagged_jobs_are_dropped(self, client, mock_manager, monkeypatch, temp_db):
        import models.llm

        self._arm_search(mock_manager)

        async def _fake(system, user):
            return '{"mismatch": [{"index": 2, "reason": "外贸业务员"}, {"index": 3, "reason": "行政专员"}]}'

        monkeypatch.setattr(models.llm, "acall_llm", _fake)
        self._settings(monkeypatch)

        resp = client.post("/api/boss/jobs/search", json={"keyword": "AI应用开发", "city": "", "limit": 10})
        assert resp.status_code == 200
        data = resp.json()
        titles = [j["job_title"] for j in data["jobs"]]
        assert titles == ["AI应用开发工程师"]
        rel = data["search_meta"]["relevance"]
        assert rel["llm_dropped"] == 2
        assert {d["title"] for d in rel["llm_dropped_jobs"]} == {"外贸业务员", "行政专员"}

    def test_llm_failure_keeps_everything(self, client, mock_manager, monkeypatch, temp_db):
        """复核失败（网络/解析）时一条都不能删 —— 宁可放过，不可误删。"""
        import models.llm

        self._arm_search(mock_manager)

        async def _boom(system, user):
            raise RuntimeError("deepseek timeout")

        monkeypatch.setattr(models.llm, "acall_llm", _boom)
        self._settings(monkeypatch)

        resp = client.post("/api/boss/jobs/search", json={"keyword": "AI应用开发", "city": "", "limit": 10})
        assert resp.status_code == 200
        assert len(resp.json()["jobs"]) == 3

    def test_disabled_switch_skips_llm(self, client, mock_manager, monkeypatch, temp_db):
        import models.llm

        self._arm_search(mock_manager)
        calls = []

        async def _fake(system, user):
            calls.append(1)
            return '{"mismatch": [1, 2, 3]}'

        monkeypatch.setattr(models.llm, "acall_llm", _fake)
        self._settings(monkeypatch, relevance_llm_enabled="false")

        resp = client.post("/api/boss/jobs/search", json={"keyword": "AI应用开发", "city": "", "limit": 10})
        assert resp.status_code == 200
        assert calls == [], "开关关闭时不该调用 LLM"
        assert len(resp.json()["jobs"]) == 3


# ── 搜索的城市语义 ──

class TestBossSearchCity:
    """城市留空 = 全国。不能静默 fallback 到 default_city。

    事故背景：库里 default_city="淄博"，而前端把空选项标成「全国」——界面写全国、
    实际在淄博里搜，结果和手机端完全对不上（实测搜 "ai" 全国上万条、淄博 27 条）。
    """

    @staticmethod
    def _arm(mock_manager):
        mock_manager.automation = MagicMock()
        mock_manager.automation.page = MagicMock()
        mock_manager.run_pw = AsyncMock(return_value=[])

    def test_blank_city_searches_nationwide(self, client, mock_manager, monkeypatch, temp_db):
        self._arm(mock_manager)
        # 就算库里存着"淄博"，空城市也必须走全国
        monkeypatch.setattr(
            "api.boss.get_setting",
            lambda k, d=None: "淄博" if k == "default_city" else d,
        )
        resp = client.post("/api/boss/jobs/search", json={"keyword": "ai", "city": "", "limit": 10})
        assert resp.status_code == 200
        # run_pw(automation.search, keyword, city_code, ...) → args[2] 是 city_code
        assert mock_manager.run_pw.await_args.args[2] == "100010000"

    def test_explicit_city_is_honored(self, client, mock_manager, monkeypatch, temp_db):
        self._arm(mock_manager)
        monkeypatch.setattr("api.boss.get_setting", lambda k, d=None: d)
        resp = client.post("/api/boss/jobs/search", json={"keyword": "ai", "city": "北京", "limit": 10})
        assert resp.status_code == 200
        assert mock_manager.run_pw.await_args.args[2] == "101010100"


# ── 浏览器线程卡死 → 503 + 处置办法 ──

class TestBossBrowserWedged:
    """run_pw 卡死时，端点必须回 503 并把处置办法带给用户。

    没有这个映射，异常会被 FastAPI 兜成 500 "Internal Server Error"，
    用户只看到一句无用的话，不知道该点「停止浏览器」。
    """

    def test_wedged_run_pw_returns_503_with_guidance(self, client, mock_manager, monkeypatch, temp_db):
        from boss.manager import BrowserWedgedError

        mock_manager.automation = MagicMock()
        mock_manager.automation.page = MagicMock()
        # 线程已判定卡死时，真实实现（BossManager.apply_batch_async）开头就抛这个异常
        # （它自己的单测见 test_apply_budget_integration.test_batch_aborts_when_thread_wedged）。
        # 这里只验 API 层把它翻成 503 + 指引。
        mock_manager._pw_wedged = True
        mock_manager.apply_batch_async = AsyncMock(
            side_effect=BrowserWedgedError("浏览器线程已卡死。请点「停止浏览器」，再重新「启动浏览器」。")
        )
        # 得先有可投的岗位，请求才会真的走到 run_pw（空库会在前面就早退 200）
        monkeypatch.setattr(
            "api.boss.list_applications",
            lambda status=None, limit=50: [{"status": "pending", "job_url": "https://test.com/1"}],
        )

        resp = client.post("/api/boss/jobs/scan-and-apply", json={})
        assert resp.status_code == 503
        assert "停止浏览器" in resp.json()["detail"]

    def test_batch_reports_timeout_without_counting_it_as_failure(
        self, client, mock_manager, monkeypatch, temp_db,
    ):
        mock_manager.automation = MagicMock()
        monkeypatch.setattr("api.boss.get_apply_allowance", lambda: {"remaining": 5})
        mock_manager.apply_batch_async = AsyncMock(return_value=[
            {"success": False},
            {"success": True},
            {"success": False, "outcome": "unknown", "stop_batch": True,
             "stop_reason": "pw_wedged", "message": "平台结果待核对"},
        ])

        response = client.post("/api/boss/jobs/apply-batch", json={
            "job_urls": [f"https://www.zhipin.com/job_detail/{i}" for i in range(4)],
        })

        assert response.status_code == 200
        assert response.json()["summary"] == {
            "applied": 1, "already_applied": 0, "failed": 1, "unknown": 1,
            "not_attempted": 1, "stop_reason": "pw_wedged", "stop_message": "平台结果待核对",
        }
