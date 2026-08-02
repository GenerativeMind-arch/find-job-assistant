"""BossManager 单例管理器单元测试。

测试 BossManager 的单例模式、状态查询、Playwright 线程池管理等功能。
不实际启动 Playwright，通过 mock 验证管理器逻辑。
异步测试使用 asyncio.run() 包装，无需 pytest-asyncio 插件。
"""

import asyncio
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[2]
BOSS_DIR = BACKEND_DIR / "boss"
for p in [str(BACKEND_DIR), str(BOSS_DIR)]:
    if p not in sys.path:
        sys.path.insert(0, p)

from boss.manager import BossManager


class TestBossManagerSingleton:
    """测试 BossManager 单例模式。"""

    def test_singleton_returns_same_instance(self):
        """get_instance 应返回同一个实例。"""
        BossManager._instance = None
        mgr1 = BossManager.get_instance()
        mgr2 = BossManager.get_instance()
        assert mgr1 is mgr2

    def test_singleton_is_boss_manager(self):
        """实例应为 BossManager 类型。"""
        BossManager._instance = None
        mgr = BossManager.get_instance()
        assert isinstance(mgr, BossManager)

    def test_initial_state(self):
        """新实例的初始状态应正确。"""
        BossManager._instance = None
        mgr = BossManager.get_instance()
        assert mgr.automation is None
        assert mgr.monitor_task is None
        assert mgr.monitor_paused is False
        assert mgr.browser_sync_lock is None


class TestBossManagerStatus:
    """测试 BossManager 的状态查询。"""

    def test_get_status_returns_dict(self):
        """get_status 应返回字典。"""
        BossManager._instance = None
        mgr = BossManager.get_instance()
        status = mgr.get_status()
        assert isinstance(status, dict)

    def test_get_status_has_required_fields(self):
        """状态字典应包含所有必需字段。"""
        BossManager._instance = None
        mgr = BossManager.get_instance()
        status = mgr.get_status()
        required_fields = [
            "browser_running",
            "monitor_running",
            "monitor_paused",
            "auto_reply_enabled",
            "in_cooldown",
            "today_applications",
            "today_auto_replies",
            "daily_limit",
        ]
        for field in required_fields:
            assert field in status, f"Missing status field: {field}"

    def test_get_status_values_when_not_started(self):
        """未启动时状态值应正确。"""
        BossManager._instance = None
        mgr = BossManager.get_instance()
        status = mgr.get_status()
        assert status["browser_running"] is False
        assert status["monitor_running"] is False
        assert status["monitor_paused"] is False
        assert status["in_cooldown"] is False
        assert status["today_applications"] >= 0


class TestBossManagerMonitorControl:
    """测试监控暂停/恢复。"""

    def test_pause_monitor(self):
        """pause_monitor 应设置 monitor_paused 为 True。"""
        BossManager._instance = None
        mgr = BossManager.get_instance()
        result = mgr.pause_monitor()
        assert result["status"] == "paused"
        assert mgr.monitor_paused is True

    def test_resume_monitor(self):
        """resume_monitor 应设置 monitor_paused 为 False。"""
        BossManager._instance = None
        mgr = BossManager.get_instance()
        mgr.monitor_paused = True
        result = mgr.resume_monitor()
        assert result["status"] == "resumed"
        assert mgr.monitor_paused is False


class TestBossManagerEventBroadcast:
    """测试事件广播机制。"""

    def test_add_event_listener(self):
        """add_event_listener 应添加监听器。"""
        BossManager._instance = None
        mgr = BossManager.get_instance()
        listener = lambda event: None
        mgr.add_event_listener(listener)
        assert listener in mgr._event_listeners

    def test_broadcast_calls_listeners(self):
        """_broadcast 应调用所有监听器。"""
        BossManager._instance = None
        mgr = BossManager.get_instance()
        received = []
        mgr.add_event_listener(lambda event: received.append(event))
        asyncio.run(mgr._broadcast({"type": "test", "data": "hello"}))
        assert len(received) == 1
        assert received[0]["type"] == "test"

    def test_broadcast_async_listener(self):
        """_broadcast 应支持异步监听器。"""
        BossManager._instance = None
        mgr = BossManager.get_instance()
        received = []

        async def async_listener(event):
            received.append(event)

        mgr.add_event_listener(async_listener)
        asyncio.run(mgr._broadcast({"type": "async_test"}))
        assert len(received) == 1

    def test_broadcast_listener_error_does_not_crash(self):
        """监听器异常不应影响其他监听器。"""
        BossManager._instance = None
        mgr = BossManager.get_instance()
        received = []

        def bad_listener(event):
            raise RuntimeError("intentional error")

        def good_listener(event):
            received.append(event)

        mgr.add_event_listener(bad_listener)
        mgr.add_event_listener(good_listener)
        asyncio.run(mgr._broadcast({"type": "test"}))
        assert len(received) == 1  # good_listener 仍然被调用


class TestBossManagerLockBrowser:
    """测试浏览器锁机制。"""

    def test_lock_browser_acquires_and_releases(self):
        """lock_browser 上下文管理器应正确获取和释放锁。"""
        BossManager._instance = None
        mgr = BossManager.get_instance()

        async def _test():
            async with mgr.lock_browser():
                assert mgr.monitor_paused is True
            # 退出后应恢复
            assert mgr.monitor_paused is False

        asyncio.run(_test())

    def test_lock_browser_restores_previous_pause_state(self):
        """lock_browser 退出后应恢复之前的暂停状态。"""
        BossManager._instance = None
        mgr = BossManager.get_instance()
        mgr.monitor_paused = True  # 之前已暂停

        async def _test():
            async with mgr.lock_browser():
                assert mgr.monitor_paused is True
            # 退出后应恢复为 True（之前的状态）
            assert mgr.monitor_paused is True

        asyncio.run(_test())
