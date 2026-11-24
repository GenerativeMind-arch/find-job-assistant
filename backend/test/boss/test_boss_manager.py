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


class TestAutoReplyDurability:
    """「停止回复」必须持久 —— 只有持久化开关能控制回复。

    背景：按钮过去同时写持久化的 auto_reply_enabled 和内存的 monitor_paused，
    而循环里 monitor_paused 的闸门在心跳之前，导致后端重启后行为不一致。
    现在 auto_reply_enabled 是唯一真值来源，monitor_paused 只表示"浏览器忙"。

    注意：这些测试一律 monkeypatch 掉 set_setting，避免污染开发者本机的
    真实 auto_reply_enabled 配置。
    """

    def test_disable_writes_persisted_setting_only(self, monkeypatch):
        """点停止回复只写持久化设置，不得把浏览器标记为忙。"""
        import boss_state

        written = {}
        monkeypatch.setattr(boss_state, "set_setting", lambda k, v: written.__setitem__(k, v))

        BossManager._instance = None
        mgr = BossManager.get_instance()
        mgr.monitor_paused = False

        asyncio.run(mgr.set_auto_reply_enabled(False))

        assert written == {"auto_reply_enabled": "false"}
        assert mgr.monitor_paused is False, "停止回复不应顺带把浏览器标记为忙"

    def test_disable_keeps_monitor_task_alive(self, monkeypatch):
        """停止回复不取消监控任务 —— 心跳仍需继续跑。"""
        import boss_state

        monkeypatch.setattr(boss_state, "set_setting", lambda k, v: None)

        BossManager._instance = None
        mgr = BossManager.get_instance()

        async def _test():
            mgr.monitor_task = asyncio.create_task(asyncio.sleep(30))
            await mgr.set_auto_reply_enabled(False)
            assert mgr.monitor_task is not None
            assert not mgr.monitor_task.done()
            mgr.monitor_task.cancel()

        asyncio.run(_test())

    def test_enable_resets_scan_deadline_and_wakes_loop(self, monkeypatch):
        """恢复回复要立即扫一轮：重置扫描到期时刻 + 唤醒等待中的循环。"""
        import boss_state

        monkeypatch.setattr(boss_state, "set_setting", lambda k, v: None)

        BossManager._instance = None
        mgr = BossManager.get_instance()
        mgr.automation = None  # 避免走到 create_task 分支
        mgr._next_scan_at = 12345.0
        mgr._monitor_wake.clear()

        asyncio.run(mgr.set_auto_reply_enabled(True))

        assert mgr._next_scan_at == 0.0, "恢复回复必须重置扫描到期时刻"
        assert mgr._monitor_wake.is_set(), "恢复回复必须唤醒等待中的循环"

    def test_monitor_state_off_when_switch_disabled(self):
        """UI 状态跟随持久化开关，与 monitor_paused 无关。"""
        from types import SimpleNamespace

        BossManager._instance = None
        mgr = BossManager.get_instance()
        mgr.automation = SimpleNamespace(page=object())

        async def _test():
            mgr.monitor_task = asyncio.create_task(asyncio.sleep(30))
            assert mgr._monitor_state(False) == "off"
            # 即使把 monitor_paused 设成 True，开着开关仍是 running
            mgr.monitor_paused = True
            assert mgr._monitor_state(True) == "running"
            mgr.monitor_task.cancel()

        asyncio.run(_test())

    def test_monitor_state_off_when_loop_dead(self):
        """循环因登录过期退出后，不该还显示"运行中"。"""
        from types import SimpleNamespace

        BossManager._instance = None
        mgr = BossManager.get_instance()
        mgr.automation = SimpleNamespace(page=object())
        mgr.monitor_task = None

        assert mgr._monitor_state(True) == "off"

    def test_monitor_state_no_browser(self):
        BossManager._instance = None
        mgr = BossManager.get_instance()
        mgr.automation = None

        assert mgr._monitor_state(True) == "no_browser"
