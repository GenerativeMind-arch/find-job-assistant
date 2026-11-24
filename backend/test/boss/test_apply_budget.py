"""自适应投递预算的策略测试（纯逻辑，不碰 DB）。

要守住的不变量：
  1. 预算永不越过 [已投数, MAX_BUDGET] —— 不能出现"负额度"，也不能无限涨
  2. **退得比进得快**：加码要攒 5 次干净成功（+5），撞一次 captcha 直接砍半
  3. 间隔倍率只会 ≥1.0 —— 状态再好也不会投得比用户设的间隔更快
  4. 平台明确说"额度用完"或 banned → 当天直接收手
"""

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2]
BOSS_DIR = BACKEND_DIR / "boss"
for p in [str(BACKEND_DIR), str(BOSS_DIR)]:
    if p not in sys.path:
        sys.path.insert(0, p)

import pytest

from boss_apply_budget import (
    BASE_BUDGET,
    MAX_BUDGET,
    MAX_DELAY_MULTIPLIER,
    RAMP_AFTER_CLEAN,
    RAMP_STEP,
    BudgetState,
    clamp_budget,
    delay_multiplier,
    dump_state,
    is_day_over,
    load_state,
    on_risk,
    on_success,
    remaining,
)


# ── 序列化往返 ──


def test_dump_load_roundtrip():
    s = BudgetState(budget=32, strikes=2, clean_streak=3)
    assert load_state(dump_state(s)) == s


def test_load_garbage_falls_back_to_fresh():
    """持久化数据坏掉不能让人投不出去 —— 退回全新状态（= 起步额度）。"""
    for bad in [None, "", "{不是 json}", "[]", '{"budget": "abc"}']:
        s = load_state(bad, base=20)
        assert s.budget == 20 and s.strikes == 0 and s.clean_streak == 0


def test_load_clamps_out_of_range_persisted_value():
    """被改坏/历史遗留的超大预算要夹回上限。"""
    assert load_state('{"budget": 9999}').budget == MAX_BUDGET
    assert load_state('{"budget": -5}').budget == 0


# ── 加码 ──


def test_ramps_only_after_enough_clean_successes():
    s = BudgetState(budget=BASE_BUDGET)
    for _ in range(RAMP_AFTER_CLEAN - 1):
        on_success(s)
    assert s.budget == BASE_BUDGET, "还没攒够就不该加码"

    on_success(s)  # 第 RAMP_AFTER_CLEAN 次
    assert s.budget == BASE_BUDGET + RAMP_STEP
    assert s.clean_streak == 0, "加完码要重新攒"


def test_ramp_stops_at_ceiling():
    s = BudgetState(budget=MAX_BUDGET - 1)
    for _ in range(RAMP_AFTER_CLEAN * 4):
        on_success(s)
    assert s.budget == MAX_BUDGET, "不能越过动态上限"


# ── 收缩 ──


def test_captcha_halves_budget():
    s = BudgetState(budget=40)
    on_risk(s, "captcha", applied=5)
    assert s.budget == 20
    assert s.strikes == 1
    assert s.clean_streak == 0, "撞风控就重新攒"


def test_rate_limit_is_a_light_trim():
    """只是投快了，没必要像 captcha 那样砍半。"""
    s = BudgetState(budget=40)
    on_risk(s, "rate_limit", applied=5)
    assert s.budget == 38


@pytest.mark.parametrize("category", ["banned", "daily_quota"])
def test_day_over_categories_stop_the_day(category):
    s = BudgetState(budget=40)
    on_risk(s, category, applied=7)
    assert s.budget == 7
    assert is_day_over(s, applied=7) is True


def test_budget_never_drops_below_applied():
    """已投的不能被"没投过" —— 否则额度变负数，判断全乱。"""
    s = BudgetState(budget=20)
    on_risk(s, "banned", applied=15)
    assert s.budget == 15
    assert remaining(s, 15) == 0

    s2 = BudgetState(budget=4)
    on_risk(s2, "captcha", applied=9)  # 已投比预算还多（历史上限改小过）
    assert s2.budget == 9
    assert remaining(s2, 9) == 0


def test_shrink_is_faster_than_ramp():
    """不对称性：撞一次 captcha 砍掉的口子，要比攒同样次数加回来的多。"""
    s = BudgetState(budget=20)
    on_risk(s, "captcha", applied=4)
    after_shrink = s.budget          # 10
    for _ in range(RAMP_AFTER_CLEAN):
        on_success(s)
    assert s.budget == after_shrink + RAMP_STEP
    assert s.budget < 20, "一次风控的代价应该大于一次加码"


# ── remaining / 边界 ──


def test_remaining_never_negative():
    assert remaining(BudgetState(budget=5), applied=5) == 0
    assert remaining(BudgetState(budget=5), applied=99) == 0
    assert remaining(BudgetState(budget=5), applied=0) == 5


def test_clamp_budget_respects_floor_and_ceiling():
    assert clamp_budget(3, floor=8) == 8
    assert clamp_budget(999) == MAX_BUDGET


# ── 间隔倍率 ──


def test_delay_multiplier_never_below_one():
    """状态再好也不能比用户设的间隔更快。"""
    s = BudgetState(strikes=0, clean_streak=99)
    assert delay_multiplier(s) == 1.0


def test_delay_multiplier_grows_with_strikes_and_caps():
    s = BudgetState(strikes=1, clean_streak=0)
    assert delay_multiplier(s) == pytest.approx(1.5)
    s.strikes = 99
    assert delay_multiplier(s) == MAX_DELAY_MULTIPLIER


def test_delay_multiplier_recovers_with_clean_streak():
    s = BudgetState(strikes=2, clean_streak=0)   # 2.0
    assert delay_multiplier(s) == pytest.approx(2.0)
    s.clean_streak = 4
    assert delay_multiplier(s) == pytest.approx(1.0)
