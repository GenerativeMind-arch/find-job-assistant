"""手动发送慢的四处修复的回归测试。

对应四条：
  1. lock_browser 先在锁上空睡 0.5s 才检查 —— 锁空闲时白白多等半秒
  2. 会话已打开也要重开一遍（坐标点击 + 固定 sleep）
  3. 扫描整轮持锁，前台操作要等它跑完 —— 扫描应见有人在等就让路
  4. 发送校验用 page.inner_text("body") 整页序列化

2 的行为改动直接在 send_message 的调用链上，靠代码审查保证；这里锁住 1/3/4 和
2 所依赖的"先看窄选择器、失配再兜底"的不变量。

不启动 Playwright：桩对象 + 假 page，沿用 test_boss_send_message.py 的做法。
"""

import asyncio
import sys
import time
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2]
BOSS_DIR = BACKEND_DIR / "boss"
for p in [str(BACKEND_DIR), str(BOSS_DIR)]:
    if p not in sys.path:
        sys.path.insert(0, p)

import boss_automation
import boss_state
from boss.manager import BrowserBusyError, BossManager
from boss_automation import BossAutomation, scan_should_yield, sent_text_visible


def _fresh_manager() -> BossManager:
    BossManager._instance = None
    return BossManager.get_instance()


# ══════════════════════════════════════
#  1) 等锁
# ══════════════════════════════════════

class TestLockBrowser:
    def test_acquires_immediately_when_free(self):
        """锁空闲时必须马上拿到，不能再空等一个 0.5s 轮询间隔。"""
        mgr = _fresh_manager()

        async def _main():
            t0 = time.perf_counter()
            async with mgr.lock_browser():
                pass
            return time.perf_counter() - t0

        assert asyncio.run(_main()) < 0.2

    def test_times_out_with_browser_busy_error(self):
        """锁被占住超过 timeout 时抛 BrowserBusyError，而不是无限等下去。"""
        mgr = _fresh_manager()

        async def _main():
            async with mgr.lock_browser():
                try:
                    async with mgr.lock_browser(timeout=0.1):
                        return "不该走到这里"
                except BrowserBusyError:
                    return "busy"

        assert asyncio.run(_main()) == "busy"

    def test_releases_lock_and_flags_on_body_exception(self):
        """with 体内抛异常也要释放锁、清掉等待计数。"""
        mgr = _fresh_manager()

        async def _main():
            try:
                async with mgr.lock_browser():
                    raise ValueError("boom")
            except ValueError:
                pass
            free = not mgr.browser_sync_lock.locked()
            async with mgr.lock_browser():
                pass
            return free, mgr.foreground_waiting, mgr.monitor_paused

        free, waiting, paused = asyncio.run(_main())
        assert free is True
        assert waiting is False
        assert paused is False

    def test_inner_timeout_is_not_reported_as_busy(self):
        """with 体内的 TimeoutError 是业务超时，不能被误判成"等不到锁"。"""
        mgr = _fresh_manager()

        async def _main():
            try:
                async with mgr.lock_browser(timeout=5):
                    raise asyncio.TimeoutError()
            except BrowserBusyError:
                return "busy"
            except asyncio.TimeoutError:
                return "inner"

        assert asyncio.run(_main()) == "inner"

    def test_foreground_waiting_survives_overlapping_waiters(self):
        """两个前台操作重叠时，先结束的不该把后者的信号清掉。"""
        mgr = _fresh_manager()

        async def _main():
            async with mgr.lock_browser():
                async def _waiter():
                    async with mgr.lock_browser():
                        pass
                a = asyncio.create_task(_waiter())
                await asyncio.sleep(0.05)
                assert mgr.foreground_waiting is True
                return True

        assert asyncio.run(_main()) is True
        assert mgr.foreground_waiting is False


# ══════════════════════════════════════
#  3) 扫描让路
# ══════════════════════════════════════

class TestShouldYield:
    def test_defaults_false_without_hook(self):
        """没挂钩子（CLI 直建实例、测试桩）时行为不变。"""
        stub = type("S", (), {})()
        assert scan_should_yield(stub) is False

    def test_honors_hook(self):
        stub = type("S", (), {})()
        stub.should_yield = lambda: True
        assert scan_should_yield(stub) is True

    def test_hook_exception_does_not_break_scan(self):
        """钩子坏了就当没人等 —— 不能因此中断扫描。"""
        stub = type("S", (), {})()

        def _boom():
            raise RuntimeError("hook broken")

        stub.should_yield = _boom
        assert scan_should_yield(stub) is False


class _YieldPage:
    url = "https://www.zhipin.com/web/geek/chat"

    def inner_text(self, selector):
        return ""


class _YieldStub:
    """只够跑到会话循环开头：一旦让路就该在碰任何会话之前退出。"""

    def __init__(self, should_yield):
        self.page = _YieldPage()
        self.should_yield = should_yield
        self.opened = []

    def sync_conversation_list_full(self):
        pass

    def _click_chat_tab(self, name):
        pass

    def check_page_safety(self):
        return True

    def poll_conversation_list(self):
        return [{"text": "某HR", "has_unread": True, "hr_name": "某HR"}]

    def open_conversation_by_name(self, name):
        self.opened.append(name)
        return True

    def read_visible_messages(self):
        return []


class TestScanYieldsToForeground:
    """temp_db 来自 test/boss/conftest.py。"""

    def test_manager_hook_reports_waiters(self):
        """把 manager 的钩子接到扫描端：有人等锁时 should_yield 必须为真。

        这是让路机制唯一的接线点 —— 钩子没挂上（比如新增了别的建 automation
        的路径却忘了挂），扫描就永远不会让路，前台又要等整轮。
        """
        mgr = _fresh_manager()
        fake = type("A", (), {})()
        mgr.automation = fake
        mgr._attach_yield_hook()

        async def _main():
            async with mgr.lock_browser():
                return scan_should_yield(fake)

        assert asyncio.run(_main()) is True
        assert scan_should_yield(fake) is False, "操作结束后就该恢复"

    def test_cycle_stops_before_touching_conversations(self, temp_db, monkeypatch):
        """有人在等浏览器时，扫描一个会话都不该处理。"""
        import boss_state

        monkeypatch.setattr(boss_state, "list_active_conversations", lambda: [])
        monkeypatch.setattr(boss_automation, "pause", lambda *a, **k: None)
        stub = _YieldStub(should_yield=lambda: True)

        result = BossAutomation.run_chat_monitor_cycle(stub)

        assert stub.opened == [], "让路时必须一个会话都没打开"
        assert result["replies_sent"] == 0

    def test_cycle_proceeds_when_nobody_waits(self, temp_db, monkeypatch):
        """没人等时正常往下走（打开会话即说明没被误拦）。"""
        import boss_state

        monkeypatch.setattr(boss_state, "list_active_conversations", lambda: [])
        monkeypatch.setattr(boss_automation, "pause", lambda *a, **k: None)
        stub = _YieldStub(should_yield=lambda: False)

        BossAutomation.run_chat_monitor_cycle(stub)

        assert "某HR" in stub.opened


# ══════════════════════════════════════
#  2) 会话已打开就别重开
# ══════════════════════════════════════

class _OpenPage:
    """右栏已打开某会话的假页面：头部文本可配，记录是否被点过。"""

    url = "https://www.zhipin.com/web/geek/chat"

    def __init__(self, header_text):
        self._header = header_text
        self.goto_calls = 0
        self.clicks = 0

    # _conversation_is_open / _current_conversation_header 都走 evaluate + locator
    def evaluate(self, script):
        return '.chat-conversation' in script and 'top-info-content' in script

    def locator(self, selector):
        return _HeaderLocator(self._header, self)

    def goto(self, *a, **k):
        self.goto_calls += 1


class _HeaderLocator:
    def __init__(self, header_text, page):
        self._header = header_text
        self._page = page

    @property
    def first(self):
        return self

    def count(self):
        return 1

    def is_visible(self):
        return True

    def inner_text(self):
        return self._header

    def evaluate(self, script):
        return True


class _OpenStub:
    """只够跑 open_conversation_by_name 的桩；记录走没走"坐标点击"那条路。"""

    _norm_text = staticmethod(BossAutomation._norm_text)

    def __init__(self, header_text):
        self.page = _OpenPage(header_text)
        self._header = header_text
        self.clicks = 0

    def _conversation_is_open(self):
        return True

    def _current_conversation_header(self):
        return self._header

    def _header_starts_with(self, header, hr_name):
        return BossAutomation._header_starts_with(self, header, hr_name)

    def _click_by_coordinates(self, loc):
        self.clicks += 1
        return {"ok": True, "reason": "stub"}

    def _wait_conversation_open(self, timeout_sec=5.0):
        return True


class TestReuseOpenConversation:
    def test_same_conversation_is_reused(self):
        """右栏已经是目标会话 → 直接返回，不该再坐标点击一次。"""
        stub = _OpenStub("马先生 上海马帮 招聘专员 python开发")

        assert BossAutomation.open_conversation_by_name(stub, "马先生") is True
        assert stub.clicks == 0

    def test_different_conversation_is_not_reused(self):
        """右栏是别人 → 必须走完整的定位流程。

        这是这条捷径唯一危险的地方：省掉校验就会顺手在**错的会话**里发消息。
        """
        stub = _OpenStub("李女士 别的公司")

        BossAutomation.open_conversation_by_name(stub, "马先生")

        assert stub.clicks == 1, "头部对不上时必须老老实实重新定位"

    def test_name_elsewhere_in_header_does_not_count(self):
        """名字出现在头部中段（别人的公司名/岗位名里）不能算命中。

        HR 名永远在头部最前面，所以只认前缀。用包含匹配的话，两字的名字
        在别人的头部里也能命中 —— 而误判的代价是在错的会话里发消息。
        """
        stub = _OpenStub("")

        assert stub._header_starts_with("张先生 某公司", "张先生") is True
        assert stub._header_starts_with("李先生 张先生科技", "张先生") is False
        assert stub._header_starts_with("李女士 别的公司", "先生") is False


# ══════════════════════════════════════
#  4) 发送校验
# ══════════════════════════════════════

class _Bubble:
    """模拟 locator(...).last。"""

    def __init__(self, text, count):
        self._text = text
        self._count = count

    @property
    def last(self):
        return self

    def count(self):
        return self._count

    def inner_text(self):
        return self._text


class _VerifyPage:
    def __init__(self, bubble_text, bubble_count=1, body_text=""):
        self._bubble_text = bubble_text
        self._bubble_count = bubble_count
        self._body_text = body_text
        self.body_reads = 0

    def locator(self, selector):
        return _Bubble(self._bubble_text, self._bubble_count)

    def inner_text(self, selector):
        self.body_reads += 1
        return self._body_text


class TestSentTextVisible:
    def test_bubble_hit_skips_full_page_read(self):
        """气泡里能看到就是发出去了 —— 不该再去序列化整个页面。"""
        page = _VerifyPage(bubble_text="在的，方便聊聊", body_text="在的，方便聊聊")

        assert sent_text_visible(page, "在的，方便聊") is True
        assert page.body_reads == 0

    def test_falls_back_to_full_page_when_bubble_selectors_miss(self):
        """气泡选择器失配时必须退回整页校验。

        宁可多花一次整页读取，也不能因为 BOSS 改版导致选择器失效，就把
        发成功的消息判成失败 —— 那会触发清空输入框，把已发消息当作没发。
        """
        page = _VerifyPage(bubble_text="", bubble_count=0, body_text="在的，方便聊聊")

        assert sent_text_visible(page, "在的，方便聊") is True
        assert page.body_reads == 1

    def test_reports_false_when_text_absent(self):
        page = _VerifyPage(bubble_text="别的消息", body_text="别的消息")

        assert sent_text_visible(page, "在的，方便聊") is False

    def test_locator_exception_falls_back(self):
        class _BrokenPage:
            body_reads = 0

            def locator(self, selector):
                raise RuntimeError("detached")

            def inner_text(self, selector):
                self.body_reads += 1
                return "在的，方便聊聊"

        page = _BrokenPage()

        assert sent_text_visible(page, "在的，方便聊") is True
        assert page.body_reads == 1

    def test_empty_needle_is_false(self):
        page = _VerifyPage(bubble_text="任意", body_text="任意")
        assert sent_text_visible(page, "") is False
