"""每日投递预算的自适应控制。

**为什么不是固定值**：求职者侧 BOSS 没有公开的固定投递上限（网上查到的 10/50/100/200
全是企业招聘方口径），所以"每天投几个"本来就是我们自己拍的数字。而真正拦人的是
**风控** —— 实测 2026-10-12 连着「搜索 → 投递」，第 2 个岗位就撞了验证码。

**这个模块做什么**：从一个保守的起步值开始，一路顺利就逐步加码；一撞风控立刻收缩，
并把投递间隔成倍拉长。进退不对称 —— 退得比进得快。

**边界（别误解）**：这**不能保证不被禁**。平台风控策略不公开且在变，"最多且安全"没有
可证明的解。这里只是把"凭感觉设个 20"换成"看着信号自动调"，并且调的方向永远偏保守。

本模块是**纯逻辑**：不碰数据库、不碰网络。持久化由 boss_state 负责（读出来喂给
load_state、把 dump_state 的结果写回去），这样策略本身可以脱离 DB 单独测。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any, Dict, Optional

# ── 预算档位 ──
BASE_BUDGET = 20          # 起步额度（= 用户设置的 daily_apply_limit）
MAX_BUDGET = 50           # 动态上限，一路顺利最多涨到这里
RAMP_STEP = 5             # 每次加码的幅度
RAMP_AFTER_CLEAN = 5      # 连续多少个"干净的成功投递"后加一次码

# ── 风控后的降速 ──
MAX_DELAY_MULTIPLIER = 4.0   # 间隔最多拉长到几倍
_DELAY_PER_STRIKE = 0.5      # 每撞一次风控，间隔倍率 +0.5
_DELAY_RECOVER_PER_CLEAN = 0.25  # 每攒一个干净成功，倍率回落 0.25

# 各风控类别的收缩力度。
# 之所以按类别分开：rate_limit 只是"投太快了"，削狠了没必要；captcha 是实测最常撞的，
# 要明显收缩；banned / 平台明确拒绝额度用完，当天直接收手。
_SHRINK_RATE_LIMIT = 2       # 预算直接减 2
_SHRINK_CAPTCHA_HALVE = True  # 预算砍半
DAY_OVER_CATEGORIES = ("banned", "daily_quota")


@dataclass
class BudgetState:
    """今日的预算状态。整份按日重置，由调用方保证"today 变了就重置"。"""

    budget: int = BASE_BUDGET       # 今日当前预算（能投的总数）
    strikes: int = 0                # 今日撞了几次风控
    clean_streak: int = 0           # 距上次风控的连续成功数

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


def clamp_budget(value: int, floor: int = 0, ceiling: int = MAX_BUDGET) -> int:
    """预算必须落在 [floor, ceiling]。floor 一般是"今日已投数"。

    ceiling 可配（设置项 apply_daily_max）—— 用户调小上限后，已经涨上去的预算
    必须跟着被夹下来，否则"设置里写着 30、实际还在按 50 投"。
    """
    ceiling = max(int(ceiling), int(floor))
    return max(int(floor), min(int(value), ceiling))


def load_state(raw: Optional[str], base: int = BASE_BUDGET, ceiling: int = MAX_BUDGET) -> BudgetState:
    """从持久化的 JSON 还原状态。任何异常/缺失都退回全新状态。

    "跨日重置"不在这里做：调用方存的是**当天**那一行（daily_stats 按 date 主键），
    换了一天自然读到的是空值 —— 比在本模块里比较日期更不容易错。
    """
    fresh = BudgetState(budget=clamp_budget(base, ceiling=ceiling))
    if not raw:
        return fresh
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return fresh
    if not isinstance(data, dict):
        return fresh
    try:
        return BudgetState(
            budget=clamp_budget(int(data.get("budget", base)), ceiling=ceiling),
            strikes=max(0, int(data.get("strikes", 0))),
            clean_streak=max(0, int(data.get("clean_streak", 0))),
        )
    except (TypeError, ValueError):
        return fresh


def dump_state(state: BudgetState) -> str:
    return json.dumps(state.as_dict(), ensure_ascii=False)


def on_success(state: BudgetState, ceiling: int = MAX_BUDGET) -> BudgetState:
    """一次成功的投递。攒够 RAMP_AFTER_CLEAN 次干净成功就加一档码。"""
    state.clean_streak += 1
    if state.clean_streak >= RAMP_AFTER_CLEAN:
        state.budget = clamp_budget(state.budget + RAMP_STEP, ceiling=ceiling)
        state.clean_streak = 0  # 重新攒
    return state


def on_risk(state: BudgetState, category: str, applied: int, ceiling: int = MAX_BUDGET) -> BudgetState:
    """撞了一次风控。按类别收缩预算。

    `applied` 是今日**已投**数 —— 预算永不低于它，否则会出现"负额度"这种怪状态
    （已投的显然不能因为撞了风控就变得没投过）。
    """
    if category in DAY_OVER_CATEGORIES:
        state.budget = clamp_budget(applied, floor=applied, ceiling=ceiling)
    elif _SHRINK_CAPTCHA_HALVE and category == "captcha":
        state.budget = clamp_budget(state.budget // 2, floor=applied, ceiling=ceiling)
    else:
        # rate_limit 及其它未知类别：轻削
        state.budget = clamp_budget(state.budget - _SHRINK_RATE_LIMIT, floor=applied, ceiling=ceiling)

    state.clean_streak = 0
    state.strikes += 1
    return state


def remaining(state: BudgetState, applied: int) -> int:
    """今天还能投几个。永不为负。"""
    return max(0, state.budget - max(0, applied))


def delay_multiplier(state: BudgetState) -> float:
    """投递间隔的倍率：撞风控就拉长，连续干净就慢慢收回来。

    夹在 [1.0, MAX_DELAY_MULTIPLIER]。下限是 1.0 —— 只会比配置的间隔更慢，
    绝不会因为"状态很好"就投得比用户设的间隔还快。
    """
    raw = 1.0 + _DELAY_PER_STRIKE * max(0, state.strikes) - _DELAY_RECOVER_PER_CLEAN * max(0, state.clean_streak)
    return max(1.0, min(raw, MAX_DELAY_MULTIPLIER))


def is_day_over(state: BudgetState, applied: int) -> bool:
    """今天是否已经收手（预算被压到已投数或以下）。"""
    return remaining(state, applied) <= 0
