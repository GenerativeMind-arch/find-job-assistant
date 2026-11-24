"""监控扫描必须真的停在「未读」Tab 上。

真实事故（2026-10-12 实测）：一轮扫描报「扫描到 40 个会话 / 未读会话 40 个」——
可那 40 个是「全部」Tab 里的**全部联系人**，不是未读。于是本轮只处理列表前 3 个，
真正的 HR 回复只要排在后面就永远轮不到回复。

成因：sync_conversation_list_full() 为了回填字段会点「全部」，把 navigate_to_chat()
刚选好的「未读」顶掉；而紧随其后的切换写成了 `if not need_nav:`（本意是"刚导航过
就别重复点"），于是**只要本轮发生了导航**，列表就停在「全部」。
"""

import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[2]
BOSS_DIR = BACKEND_DIR / "boss"
for p in [str(BACKEND_DIR), str(BOSS_DIR)]:
    if p not in sys.path:
        sys.path.insert(0, p)

import boss_automation
from boss_automation import BossAutomation

CHAT_URL = "https://www.zhipin.com/web/geek/chat"
SEARCH_URL = "https://www.zhipin.com/web/geek/jobs?query=Agent"


class FakePage:
    def __init__(self, url):
        self.url = url

    def inner_text(self, _sel):
        return ""


def _make(url: str):
    a = BossAutomation.__new__(BossAutomation)
    a.page = FakePage(url)
    a.should_yield = None
    calls = {"tabs": [], "nav": 0, "sync": 0}

    def _nav():
        calls["nav"] += 1
        a.page.url = CHAT_URL  # 导航后落到聊天页
        return True

    def _sync():
        calls["sync"] += 1
        return 0

    def _tab(label):
        calls["tabs"].append(label)
        return True

    a.navigate_to_chat = _nav
    a.sync_conversation_list_full = _sync
    a._click_chat_tab = _tab
    a.check_page_safety = lambda: True
    a.poll_conversation_list = lambda: []
    return a, calls


@pytest.fixture(autouse=True)
def _no_db(monkeypatch, temp_db):
    """把扫描用到的 DB 侧函数挡掉，避免污染真实库。"""
    monkeypatch.setattr(boss_automation, "list_active_conversations", lambda: [], raising=False)
    import boss_state
    monkeypatch.setattr(boss_state, "list_active_conversations", lambda: [])


def test_after_navigation_scan_still_switches_to_unread(monkeypatch):
    """本轮发生了导航时，也必须切回「未读」—— 这正是出问题的分支。"""
    monkeypatch.setattr(boss_state_module(), "list_active_conversations", lambda: [])
    a, calls = _make(SEARCH_URL)  # 不在聊天页 → need_nav=True

    a.run_chat_monitor_cycle()

    assert calls["nav"] == 1, "离开聊天页时应先导航过去"
    assert "未读" in calls["tabs"], (
        "导航后没有切回「未读」：poll 会把「全部」里的所有联系人当成未读，"
        f"本轮只处理前 3 个。实际点击的 Tab={calls['tabs']}"
    )


def test_when_already_on_chat_page_switches_to_unread(monkeypatch):
    """本来就在聊天页时同样要切 —— 这条分支原实现是对的，别在修上面那条时弄坏。"""
    monkeypatch.setattr(boss_state_module(), "list_active_conversations", lambda: [])
    a, calls = _make(CHAT_URL)  # 已在聊天页 → need_nav=False

    a.run_chat_monitor_cycle()

    assert calls["nav"] == 0
    assert "未读" in calls["tabs"]


def boss_state_module():
    import boss_state
    return boss_state
