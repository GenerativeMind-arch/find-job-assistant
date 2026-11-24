"""PW 线程卡死的防护测试。

事故背景：Playwright 在 BOSS 风控页上会永久卡住不返回，而 PW 线程池只有一条线程。
原先 `run_pw` 是裸的 `await loop.run_in_executor(...)` —— 没有任何超时，于是：

1. 卡住的那次调用永远占着唯一的线程
2. 之后**每一次**浏览器操作都只是排队等死
3. `/api/boss/status` 不碰浏览器，依旧毫秒返回 —— 用户看到的是"系统很卡"，而不是
   某个接口明确报错

这些用例锁住三条不变量：
  - 卡死必须被超时发现（而不是无限等）
  - 发现之后必须**立即失败**，不能继续排队
  - 必须能恢复：换掉那条线程、丢掉旧浏览器对象，重新启动
"""

import asyncio
import sys
import threading
import time
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[2]
BOSS_DIR = BACKEND_DIR / "boss"
for p in [str(BACKEND_DIR), str(BOSS_DIR)]:
    if p not in sys.path:
        sys.path.insert(0, p)

import boss.manager as manager_mod
from boss.manager import BossManager, BrowserWedgedError, _is_target_closed


@pytest.fixture
def fresh_manager(monkeypatch):
    """每个用例一个干净的 BossManager（不走单例，避免互相污染）。"""
    monkeypatch.setattr(BossManager, "_instance", None)
    m = BossManager()
    yield m
    # 用例里可能留了卡住的线程 —— 别让它拖住整个测试进程退出
    try:
        m._playwright_executor.shutdown(wait=False)
    except Exception:
        pass


# ── 超时发现 ──


def test_wedged_call_times_out_and_raises(fresh_manager):
    """一次永不返回的操作必须被超时打断，并抛 BrowserWedgedError。"""
    blocker = threading.Event()

    async def main():
        try:
            await fresh_manager.run_pw(lambda: blocker.wait(), timeout=0.3)
        finally:
            blocker.set()  # 放掉线程，否则进程退不出去

    with pytest.raises(BrowserWedgedError):
        asyncio.run(main())
    assert fresh_manager._pw_wedged is True, "超时后必须置卡死标记"


def test_fast_call_is_unaffected(fresh_manager):
    """正常情况下超时机制不该误伤。"""
    assert asyncio.run(fresh_manager.run_pw(lambda: 42, timeout=5)) == 42
    assert fresh_manager._pw_wedged is False


# ── 卡死后立即失败，不再排队 ──


def test_subsequent_calls_fail_fast(fresh_manager):
    """卡死之后不能再排队等那条死线程 —— 必须立刻抛错。

    这是"整个系统卡住"的直接成因：旧实现里每个请求都排进同一条队列，前端只能转圈。
    """
    blocker = threading.Event()
    fresh_manager._pw_wedged = True  # 模拟已经卡死

    started = time.monotonic()
    with pytest.raises(BrowserWedgedError):
        asyncio.run(fresh_manager.run_pw(lambda: blocker.set(), timeout=5))
    elapsed = time.monotonic() - started

    assert elapsed < 1.0, "卡死后必须立即失败，不能再去排队"
    assert not blocker.is_set(), "连函数都不该被投递到线程池"


def test_wedged_message_tells_user_what_to_do(fresh_manager):
    fresh_manager._pw_wedged = True
    with pytest.raises(BrowserWedgedError) as ei:
        asyncio.run(fresh_manager.run_pw(lambda: None))
    assert "停止浏览器" in str(ei.value), "错误信息必须给出可执行的处置办法"


# ── 恢复 ──


def test_discard_wedged_thread_clears_flag_and_swaps_executor(fresh_manager):
    old_executor = fresh_manager._playwright_executor
    fresh_manager._pw_wedged = True
    fresh_manager.monitor_paused = True

    fresh_manager._discard_wedged_thread()

    assert fresh_manager._pw_wedged is False
    assert fresh_manager.monitor_paused is False
    assert fresh_manager._playwright_executor is not old_executor, "必须换一条新线程"
    # 换完之后能正常干活
    assert asyncio.run(fresh_manager.run_pw(lambda: "ok", timeout=5)) == "ok"


def test_stop_recovers_from_wedge(fresh_manager):
    """卡死时 stop() 必须能走通（旧实现在这里也要 run_pw，等于点了没反应）。"""
    fresh_manager._pw_wedged = True
    fresh_manager.automation = object()  # 一个已经是尸体的浏览器对象
    old_executor = fresh_manager._playwright_executor

    result = asyncio.run(fresh_manager.stop())

    assert result["status"] == "stopped"
    assert fresh_manager.automation is None
    assert fresh_manager._pw_wedged is False
    assert fresh_manager._playwright_executor is not old_executor


def test_start_discards_wedged_state(monkeypatch, fresh_manager):
    """卡死后点「启动浏览器」应当先换线程、丢掉旧对象，而不是撞回同一堵墙。"""
    import boss_automation

    fresh_manager._pw_wedged = True
    fresh_manager.automation = None

    class FakeAutomation:
        def __init__(self, headless=False):
            self.page = object()
            self.should_yield = None

        def start(self):
            pass

    monkeypatch.setattr(boss_automation, "BossAutomation", FakeAutomation)
    old_executor = fresh_manager._playwright_executor

    result = asyncio.run(fresh_manager.start(headless=True))

    assert result["status"] == "started"
    assert fresh_manager._pw_wedged is False
    assert fresh_manager._playwright_executor is not old_executor


# ── 浏览器被关掉（日志里那句 TargetClosedError）──


def test_is_target_closed_matches_by_name_and_message():
    class TargetClosedError(Exception):
        pass

    assert _is_target_closed(TargetClosedError("boom"))
    assert _is_target_closed(
        RuntimeError("Mouse.click: Target page, context or browser has been closed")
    )
    assert not _is_target_closed(ValueError("something else"))


def test_closed_browser_clears_stale_reference(fresh_manager):
    """浏览器被关掉后必须清掉自动化引用。

    否则 browser_running 一直报 true，用户点什么都是在撞一具尸体
    （真实日志：Firefox <gracefully close start> 之后前端仍显示"浏览器已启动"）。
    """
    class FakePage:
        pass

    class FakeAutomation:
        page = FakePage()

        def in_cooldown(self):
            return False

    fresh_manager.automation = FakeAutomation()
    assert fresh_manager.get_status()["browser_running"] is True

    def _raise_closed():
        raise RuntimeError(
            "Locator.count: Target page, context or browser has been closed"
        )

    with pytest.raises(RuntimeError):
        asyncio.run(fresh_manager.run_pw(_raise_closed, timeout=5))

    assert fresh_manager.automation is None
    assert fresh_manager.get_status()["browser_running"] is False


# ── 状态暴露 ──


def test_status_exposes_wedged_flag(fresh_manager, monkeypatch):
    from boss_state import get_setting  # noqa: F401  (确保 import 路径没问题)

    monkeypatch.setattr("boss_state.get_setting", lambda k, d=None: d if d is not None else "")
    monkeypatch.setattr("boss_state.get_today_application_count", lambda: 0)
    monkeypatch.setattr("boss_state.get_today_auto_reply_count", lambda: 0)
    monkeypatch.setattr(manager_mod, "time", time)

    assert fresh_manager.get_status()["browser_wedged"] is False
    fresh_manager._pw_wedged = True
    assert fresh_manager.get_status()["browser_wedged"] is True


# ── 排队 ≠ 卡死（回归用例）──

def test_queue_wait_does_not_count_as_wedge(fresh_manager):
    """排队等前面那条长操作，不能被判成"线程卡死"。

    真实事故：批量投递（每条之间插 45-120s 拟人停顿）占着唯一的 PW 线程，
    监控的心跳调用排在它后面等了 180s 就被判成 wedged —— 投递刚成功，整个
    浏览器就被标记成不可用。超时只能从**真正开始执行**起算。
    """
    release = threading.Event()

    async def main():
        # 先占住唯一那条线程约 0.5s（像一个"慢但正常"的操作）
        slow = asyncio.ensure_future(
            fresh_manager.run_pw(lambda: release.wait(), timeout=30)
        )
        await asyncio.sleep(0.1)  # 确保 slow 已在执行

        async def _releaser():
            await asyncio.sleep(0.4)
            release.set()

        rel = asyncio.ensure_future(_releaser())
        # 这条排在 slow 后面。超时是 0.3s，但它排队约 0.4s ——
        # 只要排队不算超时就应该成功；旧行为（从提交起算）会在这里误判。
        quick = await fresh_manager.run_pw(lambda: "done", timeout=0.3)
        await slow
        await rel
        return quick

    assert asyncio.run(main()) == "done"
    assert fresh_manager._pw_wedged is False, "排队不该被误判成卡死"


def test_actually_hanging_call_still_detected(fresh_manager):
    """但真卡住（开始执行后不返回）仍然要被抓到 —— 别把上面那条修过头。"""
    blocker = threading.Event()

    async def main():
        try:
            await fresh_manager.run_pw(lambda: blocker.wait(), timeout=0.3)
        finally:
            blocker.set()

    with pytest.raises(BrowserWedgedError):
        asyncio.run(main())
    assert fresh_manager._pw_wedged is True


@pytest.mark.parametrize("dispatch_started", [False, True])
def test_apply_timeout_blocks_late_dispatch_or_reports_unknown(
    fresh_manager, monkeypatch, dispatch_started,
):
    release, finished = threading.Event(), threading.Event()
    dispatched = []
    monkeypatch.setattr(manager_mod, "APPLY_JOB_TIMEOUT_SEC", 0.1)

    class FakeAutomation:
        def apply_to_job(self, url, greeting, dispatch):
            try:
                if dispatch_started:
                    assert dispatch.set_running_or_notify_cancel()
                    dispatched.append(url)
                assert release.wait(3)
                if not dispatch_started and dispatch.set_running_or_notify_cancel():
                    dispatched.append(url)
            finally:
                finished.set()

    fresh_manager.automation = FakeAutomation()

    async def main():
        try:
            return await fresh_manager.apply_job_async("job-url")
        finally:
            release.set()
            assert await asyncio.to_thread(finished.wait, 3)

    result = asyncio.run(main())
    assert result["stop_batch"] is True
    assert result["stop_reason"] == "pw_wedged"
    assert result["outcome"] == ("unknown" if dispatch_started else "not_attempted")
    assert dispatched == (["job-url"] if dispatch_started else [])
