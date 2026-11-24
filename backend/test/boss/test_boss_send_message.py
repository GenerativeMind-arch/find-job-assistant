"""send_message 的换行安全性测试。

背景：Playwright 的 keyboard.type() 逐字符派发键盘事件，遇到 "\\n" 会派发
**Enter 键**，而 BOSS 聊天框收到 Enter 就直接发送 —— 于是任何多行回复都会
被拆成多条消息发出去。这里用假的 page.keyboard 记录事件序列，锁住修复：
多行必须逐行键入、行间用 Shift+Enter，且只在全部内容输出完后按一次 Enter。

不构造真实 BossAutomation（其 __init__ 会拉起 Playwright），
直接把未绑定的 send_message 作用在只带 page 的桩对象上。
"""

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2]
BOSS_DIR = BACKEND_DIR / "boss"
for p in [str(BACKEND_DIR), str(BOSS_DIR)]:
    if p not in sys.path:
        sys.path.insert(0, p)

import boss_automation
from boss_automation import BossAutomation


class FakeKeyboard:
    def __init__(self):
        self.events: list[tuple] = []

    def press(self, key):
        self.events.append(("press", key))

    def type(self, text, delay=None):
        self.events.append(("type", text))


class FakeLocator:
    def first(self):
        return self

    def click(self):
        pass


class FakePage:
    def __init__(self, body_text: str = ""):
        self.keyboard = FakeKeyboard()
        self._body = body_text

    def locator(self, selector):
        return FakeLocator()

    def inner_text(self, selector):
        return self._body


class StubAutomation:
    """只提供 send_message 需要的 page 属性与聚焦钩子。"""

    def __init__(self, body_text: str = ""):
        self.page = FakePage(body_text)
        self.focus_calls = 0

    def _focus_chat_editor(self) -> bool:
        """桩：真实实现用坐标点击聊天输入框（见 _click_by_coordinates）。

        真实版本之所以不用 locator.click()，是因为它在 BOSS 聊天页会卡到超时。
        """
        self.focus_calls += 1
        return True

    def _in_chat_context(self) -> bool:
        # 桩里没有真实聊天上下文，清空输入框的逻辑直接跳过
        return False


# 发送前会先清空输入框，这两个事件属于准备动作，不属于正文键入
_CLEAR_EVENTS = [("press", "Control+a"), ("press", "Backspace")]


def _send(text: str, body_text: str = ""):
    stub = StubAutomation(body_text)
    ok = BossAutomation.send_message(stub, text)
    return ok, stub.page.keyboard.events


def _typed_part(events: list) -> list:
    """剥掉开头清空输入框的事件，只留正文键入 + 发送序列。"""
    i = 0
    while i < len(events) and events[i] in _CLEAR_EVENTS:
        i += 1
    return events[i:]


class TestSendMessageNewlines:
    def test_single_line_types_then_presses_enter_once(self, monkeypatch):
        monkeypatch.setattr(boss_automation, "pause", lambda *a, **k: None)

        ok, events = _send("你好", body_text="你好")

        assert ok
        assert _typed_part(events) == [("type", "你好"), ("press", "Enter")]

    def test_multiline_never_hands_raw_newline_to_type(self, monkeypatch):
        """核心回归：含换行的整段文本绝不能直接交给 keyboard.type。"""
        monkeypatch.setattr(boss_automation, "pause", lambda *a, **k: None)
        text = "第一行\n[AI助手回复]"

        ok, events = _send(text, body_text="第一行")

        assert ok
        assert ("type", text) not in events, "整段含换行文本交给 type 会被 Enter 拆成多条"
        assert _typed_part(events) == [
            ("type", "第一行"),
            ("press", "Shift+Enter"),
            ("type", "[AI助手回复]"),
            ("press", "Enter"),
        ]

    def test_only_trailing_enter_sends(self, monkeypatch):
        """中间只能出现 Shift+Enter，发送用的 Enter 有且仅有一次。"""
        monkeypatch.setattr(boss_automation, "pause", lambda *a, **k: None)

        ok, events = _send("甲\n乙\n丙", body_text="甲")

        assert ok
        assert [e for e in events if e == ("press", "Enter")] == [("press", "Enter")]
        assert events.count(("press", "Shift+Enter")) == 2

    def test_blank_line_between_paragraphs(self, monkeypatch):
        monkeypatch.setattr(boss_automation, "pause", lambda *a, **k: None)

        ok, events = _send("甲\n\n乙", body_text="甲")

        assert ok
        assert _typed_part(events) == [
            ("type", "甲"),
            ("press", "Shift+Enter"),
            ("press", "Shift+Enter"),
            ("type", "乙"),
            ("press", "Enter"),
        ]

    def test_single_line_with_marker_appended_inline(self, monkeypatch):
        """同一行的标记（回退方案）也必须能正常发出。"""
        monkeypatch.setattr(boss_automation, "pause", lambda *a, **k: None)
        text = "正文 [AI助手回复]"

        # body_text 需包含校验用的前 8 个字符，否则会走"再按一次 Enter"的兜底分支
        ok, events = _send(text, body_text=text)

        assert ok
        assert _typed_part(events) == [("type", text), ("press", "Enter")]
