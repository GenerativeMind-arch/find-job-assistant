"""boss 测试共用夹具。

背景：boss_state 的库是 backend/boss/.boss_profile/boss_state.db —— 开发时那是
真实数据（会话、消息、每日统计都在跑监控）。测试往里塞会话会污染会话列表，
还会把"今日回复数"这类计数算进去，所以默认整库换掉。

只影响显式声明 temp_db 的测试：test_boss_state.py 之类直接测 SQLite 的仍按各自
的写法来。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# conftest 比测试模块先加载，那会儿 boss/ 还没进 sys.path
_BOSS_DIR = Path(__file__).resolve().parents[2] / "boss"
if str(_BOSS_DIR) not in sys.path:
    sys.path.insert(0, str(_BOSS_DIR))

import boss_state  # noqa: E402


@pytest.fixture
def temp_db(monkeypatch, tmp_path):
    """把 boss_state 指向临时 SQLite，测试结束不留痕。"""
    monkeypatch.setattr(boss_state, "DB_PATH", tmp_path / "boss_state.db")
    # get_db() 把连接缓存在线程本地 —— 不清掉的话还会用连到真实库的旧连接。
    # 先把当前值交给 monkeypatch 记账，退出时按原样恢复。
    monkeypatch.setattr(boss_state._local, "conn", getattr(boss_state._local, "conn", None))
    boss_state._local.conn = None
    boss_state.init_db()
    yield
