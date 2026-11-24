"""_chat_monitor_loop 的双节拍行为测试。

不启动真实浏览器：用 FakeAutomation 记录调用，把节拍下限与等待时间
patch 到极小值，跑一小会儿再取消任务。重点验证：

  - 停止回复（auto_reply_enabled=false）时心跳照跑、扫描不跑
  - 到期且开启时才扫描，且一个周期内不重复扫描
  - 浏览器忙（monitor_paused）时推迟扫描但心跳继续
  - 心跳连续失败两次 → 广播 session_expired 并退出循环
  - 关闭分支必须推进 _next_scan_at（防空转回归）
"""

import asyncio
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2]
BOSS_DIR = BACKEND_DIR / "boss"
for p in [str(BACKEND_DIR), str(BOSS_DIR)]:
    if p not in sys.path:
        sys.path.insert(0, p)

import boss.manager as manager_mod
import boss_state
from boss.manager import BossManager


class FakeAutomation:
    """记录被调用的自动化方法，模拟一个可用的浏览器。"""

    def __init__(self, alive: bool = True):
        self.page = object()  # 非 None 即视为"浏览器已启动"
        self.calls: list[str] = []
        self._alive = alive

    def heartbeat(self) -> bool:
        self.calls.append("heartbeat")
        return self._alive

    def keep_alive(self):
        self.calls.append("keep_alive")

    def in_cooldown(self) -> bool:
        return False

    def run_chat_monitor_cycle(self) -> dict:
        self.calls.append("scan")
        return {"checked": 1, "new_messages": 1, "replies_sent": 1, "transfer_requested": False}

    def inspect_page_safety(self) -> dict:
        self.calls.append("safety")
        return {"ok": True}

    def count(self, name: str) -> int:
        return self.calls.count(name)


def _fresh_manager() -> BossManager:
    BossManager._instance = None
    return BossManager.get_instance()


def _patch_settings(monkeypatch, values: dict):
    """把 boss_state.get_setting 换成从 dict 取值。

    监控循环在函数开头 `from boss_state import get_setting`，所以必须在
    调用循环之前 patch 模块属性，局部名才会绑定到替身。
    """
    monkeypatch.setattr(
        boss_state, "get_setting", lambda key, default="": values.get(key, default)
    )


def _patch_fast(monkeypatch, hb: float = 0.05, scan: float = 0.05):
    """去掉启动等待与心跳重试等待，并把两个节拍下限压到极小值。"""
    monkeypatch.setattr(manager_mod, "_MONITOR_STARTUP_DELAY_SEC", 0)
    monkeypatch.setattr(manager_mod, "_HEARTBEAT_RETRY_SEC", 0)
    monkeypatch.setattr(manager_mod, "_MIN_HEARTBEAT_SEC", hb)
    monkeypatch.setattr(manager_mod, "_MIN_SCAN_SEC", scan)


def _run_loop_briefly(mgr: BossManager, seconds: float = 0.15) -> list[dict]:
    """跑一小会儿监控循环然后取消，返回广播过的事件。"""

    async def _main():
        events: list[dict] = []
        mgr.add_event_listener(events.append)
        task = asyncio.create_task(mgr._chat_monitor_loop())
        await asyncio.sleep(seconds)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        return events

    return asyncio.run(_main())


# 两个节拍都设成 0 → 由 patch 过的下限（0.05s）接管，测试才跑得快
FAST_INTERVALS = {"heartbeat_interval_sec": "0", "scan_interval_sec": "0"}
# 扫描周期很长，用来验证"一个周期内不重复扫描"
LONG_SCAN = {"heartbeat_interval_sec": "0", "scan_interval_sec": "60"}


class TestTwoCadences:
    """心跳与扫描彼此独立。"""

    def test_heartbeats_but_never_scans_when_disabled(self, monkeypatch):
        """停止回复时：心跳继续（会话保活），扫描一次都不跑。"""
        _patch_fast(monkeypatch)
        _patch_settings(monkeypatch, {**FAST_INTERVALS, "auto_reply_enabled": "false"})
        mgr = _fresh_manager()
        auto = FakeAutomation()
        mgr.automation = auto

        _run_loop_briefly(mgr, seconds=0.3)

        assert auto.count("heartbeat") >= 1, "停止回复时心跳必须继续跑"
        assert auto.count("scan") == 0, "停止回复时不得扫描"
        assert auto.count("safety") == 0

    def test_scans_when_enabled_and_due(self, monkeypatch):
        """开启且到期 → 扫一轮，并做安全检查。"""
        _patch_fast(monkeypatch)
        _patch_settings(monkeypatch, {**FAST_INTERVALS, "auto_reply_enabled": "true"})
        mgr = _fresh_manager()
        auto = FakeAutomation()
        mgr.automation = auto

        _run_loop_briefly(mgr, seconds=0.15)

        assert auto.count("scan") >= 1
        assert auto.count("safety") >= 1

    def test_scan_not_repeated_within_interval(self, monkeypatch):
        """扫描周期未到时不得重复扫描。"""
        _patch_fast(monkeypatch)
        # 用 LONG_SCAN：扫描周期 60s 远大于被压小的下限，窗口内只该扫一次
        _patch_settings(monkeypatch, {**LONG_SCAN, "auto_reply_enabled": "true"})
        mgr = _fresh_manager()
        auto = FakeAutomation()
        mgr.automation = auto

        _run_loop_briefly(mgr, seconds=0.4)

        assert auto.count("scan") == 1

    def test_disabled_branch_advances_next_scan(self, monkeypatch):
        """回归：关闭分支若不推进 _next_scan_at，wait_for 超时会变成 0，循环空转烧 CPU。"""
        _patch_fast(monkeypatch)
        _patch_settings(monkeypatch, {**FAST_INTERVALS, "auto_reply_enabled": "false"})
        mgr = _fresh_manager()
        auto = FakeAutomation()
        mgr.automation = auto

        _run_loop_briefly(mgr, seconds=0.3)

        # 空转的话 0.3s 内会转出成百上千次；正常应受 hb_iv 限制
        assert auto.count("heartbeat") <= 20

    def test_defers_scan_while_browser_busy_but_still_heartbeats(self, monkeypatch):
        """monitor_paused（浏览器忙）时推迟扫描，但心跳照跑。"""
        _patch_fast(monkeypatch)
        _patch_settings(monkeypatch, {**FAST_INTERVALS, "auto_reply_enabled": "true"})
        mgr = _fresh_manager()
        auto = FakeAutomation()
        mgr.automation = auto
        mgr.monitor_paused = True

        _run_loop_briefly(mgr, seconds=0.3)

        assert auto.count("scan") == 0
        assert auto.count("heartbeat") >= 1


class TestSessionExpiry:
    """心跳失败处理。"""

    def test_two_missed_heartbeats_broadcast_and_break(self, monkeypatch):
        """连续两次心跳失败 → 广播 session_expired 并退出循环。"""
        _patch_fast(monkeypatch)
        _patch_settings(monkeypatch, {**FAST_INTERVALS, "auto_reply_enabled": "false"})
        mgr = _fresh_manager()
        auto = FakeAutomation(alive=False)
        mgr.automation = auto

        events = _run_loop_briefly(mgr, seconds=0.5)

        assert any(e.get("type") == "session_expired" for e in events)

    def test_healthy_heartbeat_never_broadcasts_expiry(self, monkeypatch):
        """心跳正常时不该误报登录过期。"""
        _patch_fast(monkeypatch)
        _patch_settings(monkeypatch, {**FAST_INTERVALS, "auto_reply_enabled": "false"})
        mgr = _fresh_manager()
        auto = FakeAutomation(alive=True)
        mgr.automation = auto

        events = _run_loop_briefly(mgr, seconds=0.3)

        assert not any(e.get("type") == "session_expired" for e in events)
