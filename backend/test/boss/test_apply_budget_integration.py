"""自适应预算接到投递链路上的集成行为。

纯策略在 test_apply_budget.py 里测过了，这里测"接线对不对"：
  - get_apply_allowance() 是唯一权威，动态关闭时退化成旧的固定行为
  - 撞风控 → 当日预算当场收缩
  - 连投成功 → 攒够就加码
  - apply_to_job 的闸门按 allowance 拦，并给出结构化 stop_batch
  - 批量投递见到 stop_batch 立刻停批（不再靠 `"上限" in message` 字符串嗅探）
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

from boss_apply_budget import BASE_BUDGET, MAX_BUDGET, RAMP_AFTER_CLEAN, RAMP_STEP
from boss_automation import BossAutomation


@pytest.fixture(autouse=True)
def _pin_base_budget(temp_db):
    """把起步额度钉成 BASE_BUDGET。

    临时库沿用 settings 的出厂默认（daily_apply_limit=15），而本文件的断言想表达的是
    **策略**（起步 → 加码 → 封顶），不是那个出厂数字。钉住它，测试才不会因为默认值
    调整而假红。单独要别的起步值的用例，自己再 set_setting 覆盖即可。
    """
    from boss_state import set_setting

    set_setting("daily_apply_limit", str(BASE_BUDGET))
    return temp_db


def _add_applied(url: str):
    """造一条"今天已投"的记录（计数靠 greeting_sent_at）。"""
    from boss_state import add_application, update_application_status

    aid = add_application({"url": url, "title": "岗位", "company": "公司"})
    update_application_status(aid, "applied", greeting_sent=True)
    return aid


def _run_batch(automation, urls):
    """跑一遍批量投递（BossManager.apply_batch_async）。

    造一个不经 __init__ 的 manager（那里会建线程池和锁，单测不需要），
    并让 run_pw 直接在本线程执行被测函数 —— 跨线程边界不是这里要测的东西。
    """
    from boss.manager import BossManager

    mgr = BossManager.__new__(BossManager)
    mgr.automation = automation
    mgr._pw_wedged = False

    async def _run_pw(fn, *args, timeout=None):
        return fn(*args)

    mgr.run_pw = _run_pw
    return asyncio.run(mgr.apply_batch_async(urls))


# ── allowance 本身 ──


def test_allowance_starts_at_base_with_dynamic_on(temp_db):
    from boss_state import get_apply_allowance

    a = get_apply_allowance()
    assert a["dynamic"] is True
    assert a["budget"] == BASE_BUDGET
    assert a["max"] == MAX_BUDGET
    assert a["remaining"] == BASE_BUDGET


def test_allowance_falls_back_to_fixed_when_dynamic_off(temp_db):
    """关掉动态 = 旧行为：固定额度、倍率恒 1.0。留一条可回退的路。"""
    from boss_state import get_apply_allowance, set_setting

    set_setting("apply_dynamic_enabled", "false")
    set_setting("daily_apply_limit", "7")
    _add_applied("https://example.com/x1")

    a = get_apply_allowance()
    assert a["dynamic"] is False
    assert a["budget"] == 7 == a["max"]
    assert a["remaining"] == 6
    assert a["delay_multiplier"] == 1.0


def test_allowance_counts_todays_applications(temp_db):
    from boss_state import get_apply_allowance

    for i in range(3):
        _add_applied(f"https://example.com/a{i}")

    a = get_apply_allowance()
    assert a["applied"] == 3
    assert a["remaining"] == BASE_BUDGET - 3


# ── 风控收缩 ──


def test_risk_shrinks_todays_budget(temp_db):
    from boss_state import get_apply_allowance, report_apply_risk

    before = get_apply_allowance()["budget"]
    report_apply_risk("captcha")
    after = get_apply_allowance()

    assert after["budget"] == before // 2
    assert after["strikes"] == 1
    assert after["delay_multiplier"] > 1.0, "撞了风控还要拉长间隔"
    assert after["reason"], "必须给出收缩原因，否则界面上看不出发生了什么"


def test_daily_quota_stops_the_day(temp_db):
    """平台明确说额度用完 → 当天收手（预算压到已投数）。"""
    from boss_state import get_apply_allowance, report_apply_risk

    for i in range(4):
        _add_applied(f"https://example.com/q{i}")
    report_apply_risk("daily_quota")

    a = get_apply_allowance()
    assert a["remaining"] == 0
    assert a["day_over"] is True


def test_risk_never_makes_remaining_negative(temp_db):
    """预算收缩不会把"已投的"变成负数剩余。"""
    from boss_state import get_apply_allowance, report_apply_risk

    for i in range(6):
        _add_applied(f"https://example.com/n{i}")
    for _ in range(4):
        report_apply_risk("captcha")

    assert get_apply_allowance()["remaining"] >= 0


# ── 加码 ──


def test_successes_ramp_the_budget(temp_db):
    from boss_state import get_apply_allowance, report_apply_success

    for _ in range(RAMP_AFTER_CLEAN):
        report_apply_success()

    a = get_apply_allowance()
    assert a["budget"] == BASE_BUDGET + RAMP_STEP


def test_ceiling_setting_bounds_the_budget(temp_db):
    """用户在设置页调小上限，已经涨上去的预算要被夹回来。"""
    from boss_state import get_apply_allowance, report_apply_success, set_setting

    for _ in range(RAMP_AFTER_CLEAN * 3):
        report_apply_success()
    assert get_apply_allowance()["budget"] > BASE_BUDGET

    set_setting("apply_daily_max", str(BASE_BUDGET))
    assert get_apply_allowance()["budget"] == BASE_BUDGET


# ── 闸门 ──


def _automation():
    a = BossAutomation.__new__(BossAutomation)
    a.page = None
    a._cooldown_until = 0.0
    a._risk_strikes = 0
    a._last_action_ts = 0.0
    a.should_yield = None
    return a


def test_gate_stops_when_budget_exhausted(temp_db):
    from boss_state import set_setting

    set_setting("daily_apply_limit", "2")
    for i in range(2):
        _add_applied(f"https://example.com/g{i}")

    r = _automation().apply_to_job("https://example.com/new")
    assert r["success"] is False
    assert r["stop_batch"] is True
    assert r["stop_reason"] == "daily_budget"


def test_cancelled_dispatch_never_reaches_click(monkeypatch):
    from concurrent.futures import Future
    from unittest.mock import MagicMock

    monkeypatch.setattr("boss_automation.pause", lambda *args: None)
    automation = _automation()
    automation.page = MagicMock()
    automation.page.evaluate.return_value = {}
    automation.inspect_page_safety = lambda: {"ok": True}
    automation._is_already_applied_on_detail_page = lambda: False
    automation._human_pace = lambda **kwargs: None
    automation._human_scroll = lambda: None
    dispatch = Future()
    dispatch.cancel()

    result = automation.apply_to_job("https://www.zhipin.com/job_detail/example", dispatch=dispatch)

    assert result["outcome"] == "not_attempted"
    assert result["message"] == "操作已超时，未发起点击"
    automation.page.goto.assert_called_once()
    assert not any("__bossStartChat" in call.args[0] for call in automation.page.evaluate.call_args_list)


def test_batch_stops_on_structured_flag_not_message_text(temp_db):
    """停批判据必须是结构化字段。

    旧实现靠 `"上限" in message` 嗅探，而 BOSS 自己报的额度用完是
    「BOSS直聘今日沟通次数已用完」—— 不含"上限"二字，于是永不停批。
    """
    from boss_state import set_setting

    for k in ("batch_delay_min_sec", "batch_delay_max_sec", "batch_rest_every"):
        set_setting(k, "0")

    a = _automation()
    a._save_state = lambda: None
    a.in_cooldown = lambda: False
    a._cooldown_remaining = lambda: 0.0

    seen = []

    def fake_apply(url, greeting=None, dispatch=None):
        seen.append(url)
        # 消息里**不带**"上限"二字，但结构化字段说了要停
        return {"success": False, "message": "BOSS直聘今日沟通次数已用完",
                "stop_batch": True, "stop_reason": "daily_quota"}

    a.apply_to_job = fake_apply

    results = _run_batch(a, ["https://example.com/1", "https://example.com/2", "https://example.com/3"])

    assert len(results) == 1, "见到 stop_batch 就该立刻停批，不能继续往下硬撞"
    assert seen == ["https://example.com/1"]


def test_batch_keeps_going_without_stop_flag(temp_db):
    """普通的失败（没有 stop_batch）不该中断整批。"""
    from boss_state import set_setting

    for k in ("batch_delay_min_sec", "batch_delay_max_sec", "batch_rest_every"):
        set_setting(k, "0")

    a = _automation()
    a._save_state = lambda: None
    a.in_cooldown = lambda: False
    a._cooldown_remaining = lambda: 0.0
    a.apply_to_job = lambda url, greeting=None, dispatch=None: {"success": False, "message": "安全检查未通过"}

    results = _run_batch(a, ["https://example.com/1", "https://example.com/2"])
    assert len(results) == 2


def test_batch_aborts_when_thread_wedged(temp_db):
    """线程已卡死时不再假装开投 —— 直接抛，交给 API 层回 503 + 指引。"""
    from boss.manager import BossManager, BrowserWedgedError

    a = _automation()
    mgr = BossManager.__new__(BossManager)
    mgr.automation = a
    mgr._pw_wedged = True

    async def _run_pw(fn, *args, timeout=None):
        raise AssertionError("线程已判定卡死时不该再往里投任务")

    mgr.run_pw = _run_pw
    with pytest.raises(BrowserWedgedError):
        asyncio.run(mgr.apply_batch_async(["https://example.com/1"]))


def test_batch_pause_aborts_when_browser_stopped(temp_db):
    """停顿期间浏览器被停掉 → 立刻收工，不把剩下的几百秒睡完。"""
    from boss.manager import BossManager

    mgr = BossManager.__new__(BossManager)
    mgr.automation = _automation()
    mgr._pw_wedged = False

    async def _go():
        mgr.automation = None  # 模拟用户点了「停止浏览器」
        return await mgr._batch_pause(600)

    assert asyncio.run(_go()) is False
