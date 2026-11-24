"""「今日计数」的时区一致性。

这些表的 created_at / greeting_sent_at / last_message_at 都是
`DEFAULT CURRENT_TIMESTAMP` —— **SQLite 的 CURRENT_TIMESTAMP 是 UTC**。
而「今天」是用户概念上的本地日期。把 UTC 日期直接跟 `date('now','localtime')`
比，在 UTC+8 下会把本地 00:00~08:00 的记录算成"昨天"：

  - 今日投递数偏小 → 「一键投递」可以突破每日上限（凌晨那批不计入）
  - 今日自动回复数偏小 → 统计面板数字不对
  - 「近 N 小时 HR 回复」整体偏移一个时区

这些用例用一个"本地凌晨"的时间戳把差异钉死。对 UTC 负偏移的时区同样成立
（本地深夜对应的 UTC 日期会是"明天"），所以断言写成"必须被计入"而不是依赖具体偏移。
"""

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2]
BOSS_DIR = BACKEND_DIR / "boss"
for p in [str(BACKEND_DIR), str(BOSS_DIR)]:
    if p not in sys.path:
        sys.path.insert(0, p)


def _local_utc_offset_hours(db) -> float:
    row = db.execute(
        "SELECT (julianday(datetime('now','localtime')) - julianday(datetime('now'))) * 24 AS off"
    ).fetchone()
    return float(row[0] if not isinstance(row, dict) else row["off"])


def test_today_application_count_includes_local_early_morning(temp_db):
    """本地今天 01:00 投递的记录，必须计入「今日投递」。"""
    from boss_state import add_application, get_db, get_today_application_count
    import datetime as _dt

    db = get_db()
    off = _local_utc_offset_hours(db)
    if abs(off) < 1:
        # 本地时区≈UTC 时这个 bug 不显现，用例没有意义 —— 跳过而不是假装通过
        import pytest
        pytest.skip("本机时区接近 UTC，无法复现该 bug")

    # 本地今天 01:00 对应的 UTC 时刻
    local_today_0100 = _dt.datetime.now().replace(hour=1, minute=0, second=0, microsecond=0)
    utc_dt = local_today_0100 - _dt.timedelta(hours=off)

    aid = add_application({
        "title": "时区用例岗位",
        "company": "时区用例公司",
        "url": "https://example.com/tz-test",
    })
    assert aid
    db.execute(
        "UPDATE applications SET status='applied', greeting_sent_at=? WHERE id=?",
        (utc_dt.strftime("%Y-%m-%d %H:%M:%S"), aid),
    )
    db.commit()

    assert get_today_application_count() == 1, (
        "本地今天凌晨的投递被漏算了 —— 每日投递上限会因此被突破"
    )


def test_today_auto_reply_count_includes_local_early_morning(temp_db):
    from boss_state import (
        add_application,
        add_message,
        get_db,
        get_or_create_conversation,
        get_today_auto_reply_count,
    )
    import datetime as _dt

    db = get_db()
    off = _local_utc_offset_hours(db)
    if abs(off) < 1:
        import pytest
        pytest.skip("本机时区接近 UTC，无法复现该 bug")

    aid = add_application({"title": "时区岗位", "company": "时区公司", "url": "https://example.com/tz-a"})
    conv_id = get_or_create_conversation(aid, "时区HR", "时区公司", "时区岗位")
    assert conv_id
    mid = add_message(conv_id, "me", "你好", ai_generated=True)
    assert mid

    local_today_0100 = _dt.datetime.now().replace(hour=1, minute=0, second=0, microsecond=0)
    utc_dt = local_today_0100 - _dt.timedelta(hours=off)
    db.execute(
        "UPDATE messages SET created_at=? WHERE id=?",
        (utc_dt.strftime("%Y-%m-%d %H:%M:%S"), mid),
    )
    db.commit()

    assert get_today_auto_reply_count() == 1


def test_replied_within_hours_uses_same_clock(temp_db):
    """近 2 小时内 HR 的回复必须被计入，不能整体偏移一个时区。"""
    from boss_state import (
        add_application,
        count_hours_replied_in_range,
        get_db,
        get_or_create_conversation,
    )
    import datetime as _dt

    db = get_db()
    off = _local_utc_offset_hours(db)
    aid = add_application({"title": "岗位", "company": "近期公司", "url": "https://example.com/tz-b"})
    conv_id = get_or_create_conversation(aid, "近期HR", "近期公司", "岗位")
    assert conv_id

    # 本地 1 小时前 = UTC (1 小时前)
    utc_dt = _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None) - _dt.timedelta(hours=1)
    db.execute(
        "UPDATE conversations SET last_message_from='hr', last_message_at=? WHERE id=?",
        (utc_dt.strftime("%Y-%m-%d %H:%M:%S"), conv_id),
    )
    db.commit()

    assert count_hours_replied_in_range(2) == 1
