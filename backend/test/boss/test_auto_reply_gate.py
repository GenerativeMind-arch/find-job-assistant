"""「自动回复」总开关的最后一道闸（boss_automation.auto_reply_allowed）测试。

背景：开关只在监控周期开头查一次是不够的。LLM 生成回复要数秒，之后还有
_human_pace 的 5~15 秒拟人停顿 —— 用户完全可能在这段窗口里点下「停止回复」。
此时若不在发送前重读开关，消息照样发出去，表现就是"设了停止却没真停"。
追回回复（recheck）那条路径此前更是从头到尾没查过开关。

做法沿用 test_boss_send_message.py：不启动 Playwright，用一个只提供所需表面的
桩对象直接驱动 BossAutomation.run_chat_monitor_cycle，断言 send_message 的调用。
SQLite 指向临时库（见 test/boss/conftest.py），不碰开发库。
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
import boss_replier
import boss_state
from boss_automation import BossAutomation

A_HR = "闸门测试HR甲"
A_COMPANY = "闸门测试公司甲"
B_HR = "闸门测试HR乙"
B_COMPANY = "闸门测试公司乙"

HR_MSG = "你好，在吗？"
REPLY_TEXT = "在的，方便聊聊"


@pytest.fixture
def no_wait(monkeypatch):
    """去掉所有拟人停顿 —— 循环里的 sleep 加起来有十几秒。"""
    monkeypatch.setattr(boss_automation, "pause", lambda *a, **k: None)
    monkeypatch.setattr(boss_automation.time, "sleep", lambda *a, **k: None)
    monkeypatch.setattr(boss_automation, "increment_daily_stat", lambda *a, **k: None)


def _patch_settings(monkeypatch, values: dict) -> dict:
    """把 boss_automation 里的 get_setting 换成从 dict 取值，返回该 dict 供中途改写。

    auto_reply_allowed 用的是 boss_automation 命名空间里的 get_setting（模块顶层
    import 进来的），所以要 patch 在 boss_automation 上，而不是 boss_state 上。
    """
    monkeypatch.setattr(
        boss_automation, "get_setting", lambda key, default="": values.get(key, default)
    )
    return values


def _patch_generate_reply(monkeypatch, factory):
    """替换 LLM 回复生成。factory(index) 返回一条回复，可在其中改写开关。"""
    calls = {"n": 0}

    def _fake(*args, **kwargs):
        i = calls["n"]
        calls["n"] += 1
        return {"reply": factory(i), "interest": "", "transfer": False}

    monkeypatch.setattr(boss_replier, "generate_reply", _fake)
    return calls


class FakeKeyboard:
    def press(self, key):
        pass


class FakeLocator:
    def first(self):
        return self

    def inner_text(self):
        return ""      # 输入框无残留 → 不走清空分支

    def click(self):
        pass


class FakePage:
    url = "https://www.zhipin.com/web/geek/chat"   # 已在聊天页 → 不触发导航
    keyboard = FakeKeyboard()

    def locator(self, selector):
        return FakeLocator()

    def inner_text(self, selector):
        return ""


class StubAutomation:
    """只提供 run_chat_monitor_cycle 需要的表面（真 BossAutomation.__init__ 会拉起 Playwright）。"""

    def __init__(self, dom_convs, messages_by_name, recheck_messages=None, on_recheck=None):
        self.page = FakePage()
        self._dom_convs = dom_convs
        self._messages = messages_by_name
        # recheck_messages: 回复后再读到的消息（用于触发追回回复路径）
        self._recheck = recheck_messages
        # on_recheck: 第二次读消息前执行，用来在"发出第一条之后"制造状态变化
        self._on_recheck = on_recheck
        self._opened = None
        self._reads = 0
        self.sent: list[str] = []

    # ── 页面操作（全部桩掉）──
    def sync_conversation_list_full(self):
        pass

    def _click_chat_tab(self, name):
        pass

    def check_page_safety(self):
        return True

    def poll_conversation_list(self):
        return self._dom_convs

    def open_conversation_by_name(self, name):
        if name in self._messages:
            self._opened = name
            return True
        return False

    def read_visible_messages(self):
        self._reads += 1
        if self._recheck is not None and self._reads > 1:
            if self._on_recheck:
                self._on_recheck()
            return list(self._recheck)
        return list(self._messages.get(self._opened, []))

    def _respect_cooldown(self):
        return True

    def _human_pace(self, *a, **k):
        pass

    def send_message(self, text):
        self.sent.append(text)
        return True


def _make_conversation(hr_name: str, company: str) -> int:
    return boss_state.get_or_create_conversation(None, hr_name, company, "Agent 应用开发")


def _conv_row(conv_id: int) -> dict:
    return boss_state.get_conversation(conv_id)


def _msg(sender: str, content: str) -> dict:
    return {"sender": sender, "content": content, "status": ""}


def _run_cycle(monkeypatch, stub, known_convs, unreplied_rows=None):
    """把凭 DB 查询的入口换成可控数据，然后跑一轮监控周期。"""
    monkeypatch.setattr(boss_state, "list_active_conversations", lambda: known_convs)
    if unreplied_rows is not None:
        monkeypatch.setattr(boss_state, "list_unreplied_conversations", lambda: unreplied_rows)
    return BossAutomation.run_chat_monitor_cycle(stub)


# ══════════════════════════════════════
#  闸门函数本身
# ══════════════════════════════════════

class TestGate:
    def test_open_switch_allows(self, monkeypatch):
        _patch_settings(monkeypatch, {"auto_reply_enabled": "true"})
        assert boss_automation.auto_reply_allowed() is True

    def test_closed_switch_blocks(self, monkeypatch):
        _patch_settings(monkeypatch, {"auto_reply_enabled": "false"})
        assert boss_automation.auto_reply_allowed() is False

    def test_unreadable_setting_blocks(self, monkeypatch):
        """读设置失败时保守不发 —— 宁可不发，也不要在关掉之后漏发。"""
        def _boom(key, default=""):
            raise RuntimeError("db locked")

        monkeypatch.setattr(boss_automation, "get_setting", _boom)
        assert boss_automation.auto_reply_allowed() is False


# ══════════════════════════════════════
#  主回复路径
# ══════════════════════════════════════

class TestMainReplyPath:
    def _stub(self):
        return StubAutomation(
            dom_convs=[{"text": A_HR, "has_unread": True, "hr_name": A_HR}],
            messages_by_name={A_HR: [_msg("hr", HR_MSG)]},
        )

    def test_sends_when_switch_on(self, temp_db, no_wait, monkeypatch):
        """对照组：开关开着，回复正常发出。"""
        conv_id = _make_conversation(A_HR, A_COMPANY)
        _patch_settings(monkeypatch, {"auto_reply_enabled": "true"})
        _patch_generate_reply(monkeypatch, lambda i: REPLY_TEXT)
        stub = self._stub()

        _run_cycle(monkeypatch, stub, [_conv_row(conv_id)])

        assert stub.sent == [REPLY_TEXT]

    def test_sends_nothing_when_switch_off(self, temp_db, no_wait, monkeypatch):
        """开关关着：一条都不发。"""
        conv_id = _make_conversation(A_HR, A_COMPANY)
        _patch_settings(monkeypatch, {"auto_reply_enabled": "false"})
        _patch_generate_reply(monkeypatch, lambda i: REPLY_TEXT)
        stub = self._stub()

        result = _run_cycle(monkeypatch, stub, [_conv_row(conv_id)])

        assert stub.sent == []
        assert result["replies_sent"] == 0

    def test_reply_dropped_when_switched_off_mid_generation(self, temp_db, no_wait, monkeypatch):
        """核心回归：生成回复期间用户点了「停止回复」→ 这条回复必须被丢弃。

        模拟的是真实时序 —— 生成要数秒，之后还有 5~15 秒拟人停顿，
        这段时间里用户完全可能去点停止。只在周期开头查一次开关就会漏发。
        """
        conv_id = _make_conversation(A_HR, A_COMPANY)
        settings = _patch_settings(monkeypatch, {"auto_reply_enabled": "true"})

        def _flip_during_generation(i):
            settings["auto_reply_enabled"] = "false"   # 用户在这一刻点了停止
            return REPLY_TEXT

        calls = _patch_generate_reply(monkeypatch, _flip_during_generation)
        stub = self._stub()

        result = _run_cycle(monkeypatch, stub, [_conv_row(conv_id)])

        assert calls["n"] == 1, "回复确实生成了，被拦下来的只该是发送这一步"
        assert stub.sent == []
        assert result["replies_sent"] == 0


# ══════════════════════════════════════
#  追回回复路径（回复后再扫，HR 又发了一条）
# ══════════════════════════════════════

class TestRecheckPath:
    def test_recheck_reply_dropped_when_switched_off_mid_generation(
        self, temp_db, no_wait, monkeypatch
    ):
        """追回回复也要过闸：第二条（追回）生成期间关闭开关 → 只发出第一条。

        此前这条路径只查日配额和风控冷却，从没查过开关 —— 点停止后还能再发 2 条。
        """
        conv_id = _make_conversation(A_HR, A_COMPANY)
        settings = _patch_settings(monkeypatch, {"auto_reply_enabled": "true"})

        def _flip_on_second(i):
            if i >= 1:
                settings["auto_reply_enabled"] = "false"
            return REPLY_TEXT

        calls = _patch_generate_reply(monkeypatch, _flip_on_second)
        stub = StubAutomation(
            dom_convs=[{"text": A_HR, "has_unread": True, "hr_name": A_HR}],
            messages_by_name={A_HR: [_msg("hr", HR_MSG)]},
            recheck_messages=[
                _msg("hr", HR_MSG),
                _msg("me", REPLY_TEXT),
                _msg("hr", "方便发一份简历吗"),
            ],
        )

        result = _run_cycle(monkeypatch, stub, [_conv_row(conv_id)])

        assert calls["n"] == 2, "应当先发一条、再为追回消息生成一次"
        assert stub.sent == [REPLY_TEXT]
        assert result["replies_sent"] == 1

    def test_recheck_skipped_entirely_when_switch_off(self, temp_db, no_wait, monkeypatch):
        """第一条发出后立刻关掉开关 → 追回路径连 LLM 都不该调（不白花一次生成）。"""
        conv_id = _make_conversation(A_HR, A_COMPANY)
        settings = _patch_settings(monkeypatch, {"auto_reply_enabled": "true"})
        calls = _patch_generate_reply(monkeypatch, lambda i: REPLY_TEXT)
        stub = StubAutomation(
            dom_convs=[{"text": A_HR, "has_unread": True, "hr_name": A_HR}],
            messages_by_name={A_HR: [_msg("hr", HR_MSG)]},
            recheck_messages=[
                _msg("hr", HR_MSG),
                _msg("me", REPLY_TEXT),
                _msg("hr", "方便发一份简历吗"),
            ],
            on_recheck=lambda: settings.update(auto_reply_enabled="false"),
        )

        result = _run_cycle(monkeypatch, stub, [_conv_row(conv_id)])

        assert calls["n"] == 1, "追回回复在生成前就该被闸门拦下"
        assert stub.sent == [REPLY_TEXT]
        assert result["replies_sent"] == 1


# ══════════════════════════════════════
#  DB 兜底路径
# ══════════════════════════════════════

class TestDbFallbackPath:
    def _stub_with_two_convs(self):
        """会话甲已答完（占住"DOM 扫到会话"这个前置条件），会话乙只在库里。"""
        return StubAutomation(
            dom_convs=[{"text": A_HR, "has_unread": True, "hr_name": A_HR}],
            messages_by_name={
                A_HR: [_msg("hr", HR_MSG), _msg("me", REPLY_TEXT)],
                B_HR: [_msg("hr", "你好，还在看机会吗")],
            },
        )

    def test_db_fallback_sends_when_switch_on(self, temp_db, no_wait, monkeypatch):
        conv_a = _make_conversation(A_HR, A_COMPANY)
        conv_b = _make_conversation(B_HR, B_COMPANY)
        _patch_settings(monkeypatch, {"auto_reply_enabled": "true"})
        _patch_generate_reply(monkeypatch, lambda i: REPLY_TEXT)
        stub = self._stub_with_two_convs()

        _run_cycle(monkeypatch, stub, [_conv_row(conv_a)], unreplied_rows=[_conv_row(conv_b)])

        assert stub.sent == [REPLY_TEXT]

    def test_db_fallback_sends_nothing_when_switch_off(self, temp_db, no_wait, monkeypatch):
        conv_a = _make_conversation(A_HR, A_COMPANY)
        conv_b = _make_conversation(B_HR, B_COMPANY)
        _patch_settings(monkeypatch, {"auto_reply_enabled": "false"})
        _patch_generate_reply(monkeypatch, lambda i: REPLY_TEXT)
        stub = self._stub_with_two_convs()

        result = _run_cycle(monkeypatch, stub, [_conv_row(conv_a)], unreplied_rows=[_conv_row(conv_b)])

        assert stub.sent == []
        assert result["replies_sent"] == 0
