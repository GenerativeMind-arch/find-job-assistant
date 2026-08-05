"""BossAutomation 管理器 —— 单例模式管理浏览器自动化实例的生命周期。

职责:
  1. 管理 BossAutomation 实例的创建/启动/停止
  2. 管理 Playwright 线程池（同步 API 必须在单一线程运行）
  3. 管理后台聊天监控循环（自动回复）
  4. 提供浏览器锁（Agent 操作与监控循环互斥）
  5. 提供事件广播（供 WebSocket 或日志使用）

线程模型:
  Playwright 同步 API 要求所有操作在同一线程执行。
  使用 ThreadPoolExecutor(max_workers=1) 保证线程安全。
  _run_pw(fn, *args) 将同步操作投递到 PW 线程执行。
"""

import asyncio
import random
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Callable, Optional

# ── 确保 boss 模块目录在 sys.path 中 ──
_BOSS_DIR = Path(__file__).resolve().parent
if str(_BOSS_DIR) not in sys.path:
    sys.path.insert(0, str(_BOSS_DIR))

# ── 确保 backend 目录在 sys.path 中（用于导入 config_loader 等）──
_BACKEND_DIR = _BOSS_DIR.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))


class BossManager:
    """BossAutomation 单例管理器。

    使用方式:
        manager = BossManager.get_instance()
        await manager.start(headless=False)
        result = await manager.run_pw(lambda: manager.automation.search("Python", "101020100"))
        await manager.stop()
    """

    _instance: Optional["BossManager"] = None

    def __init__(self):
        self.automation: Any = None  # BossAutomation 实例
        self.monitor_task: Optional[asyncio.Task] = None
        self.monitor_paused: bool = False
        self.browser_sync_lock: Optional[asyncio.Lock] = None
        self._playwright_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="boss-pw")
        self._event_listeners: list[Callable] = []

    @classmethod
    def get_instance(cls) -> "BossManager":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ── 事件广播 ──

    def add_event_listener(self, listener: Callable):
        """添加事件监听器（用于 WebSocket 推送等）。"""
        self._event_listeners.append(listener)

    async def _broadcast(self, event: dict):
        """广播事件给所有监听器。"""
        for listener in self._event_listeners:
            try:
                if asyncio.iscoroutinefunction(listener):
                    await listener(event)
                else:
                    listener(event)
            except Exception as e:
                print(f"[BossManager] Event listener error: {e}")

    # ── Playwright 线程池 ──

    async def run_pw(self, fn: Callable, *args) -> Any:
        """在 Playwright 专属线程中执行同步操作。

        Playwright 同步 API 检测到 event loop 会拒绝运行，
        所以需要清除该线程的 asyncio 状态。

        注意：uvicorn reload 模式下 worker 进程默认使用 SelectorEventLoop，
        而 Playwright 同步 API 启动 driver 子进程依赖 ProactorEventLoop（Windows），
        因此在 PW 线程内强制使用 WindowsProactorEventLoopPolicy。
        """
        def _wrapper():
            try:
                if sys.platform == "win32":
                    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
                asyncio.set_event_loop(None)
            except Exception:
                pass
            return fn(*args)

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._playwright_executor, _wrapper)

    @asynccontextmanager
    async def lock_browser(self):
        """上下文管理器：暂停监控 + 获取浏览器锁。

        确保不跟 chat_monitor_loop 争夺浏览器资源。
        任何异常都会安全释放锁和恢复监控状态。
        """
        was_paused = self.monitor_paused
        self.monitor_paused = True
        try:
            for _ in range(20):
                await asyncio.sleep(0.5)
                if self.browser_sync_lock is None or not self.browser_sync_lock.locked():
                    break
            if self.browser_sync_lock is None:
                self.browser_sync_lock = asyncio.Lock()
            await self.browser_sync_lock.acquire()
            yield
        finally:
            if self.browser_sync_lock and self.browser_sync_lock.locked():
                self.browser_sync_lock.release()
            self.monitor_paused = was_paused

    # ── 系统控制 ──

    async def start(self, headless: Optional[bool] = None) -> dict:
        """启动浏览器自动化 + 后台监控。

        Args:
            headless: 是否无头模式。None 时读取 config.yaml 的 boss.headless。
                      首次登录（无有效登录态）必须为 False（可见浏览器扫码）。
        """
        from boss_automation import BossAutomation

        if headless is None:
            try:
                from config_loader import get_boss_config
                headless = bool(get_boss_config().get("headless", False))
            except Exception:
                headless = False

        if self.automation is not None and self.automation.page is not None:
            return {"status": "already_started"}

        def _do_start():
            a = BossAutomation(headless=headless)
            a.start()
            # 导航到 BOSS 直聘：未登录时自动进入登录/扫码页，已登录则直达首页
            try:
                a.page.goto("https://www.zhipin.com/", wait_until="domcontentloaded", timeout=25000)
            except Exception as _nav_e:
                print(f"[BossManager] 启动后导航到 BOSS 直聘失败: {_nav_e}")
            return a

        try:
            self.automation = await self.run_pw(_do_start)
        except Exception as e:
            self.automation = None
            import traceback as _tb
            _tb.print_exc()  # 完整异常打到后端控制台，便于定位
            msg = str(e) or repr(e) or "未知错误（详见后端控制台日志）"
            return {"status": "error", "message": f"浏览器启动失败: {msg}"}

        if self.automation is None or self.automation.page is None:
            self.automation = None
            return {"status": "error", "message": "浏览器启动后页面为空，请重试"}

        # 检查登录状态
        def _check_login():
            try:
                url = self.automation.page.url
                if "passport" in url or "security" in url:
                    return {"logged_in": False, "reason": "触发安全验证", "url": url}
                if self.automation._login_prompt_visible():
                    return {"logged_in": False, "reason": "需要登录", "url": url}
                return {"logged_in": True, "reason": "", "url": url}
            except Exception:
                return {"logged_in": True, "reason": "", "url": ""}

        login_status = await self.run_pw(_check_login)
        if not login_status.get("logged_in"):
            msg = f"浏览器已启动但登录态异常: {login_status.get('reason')}。请手动登录或使用 relogin。"
            return {"status": "warning", "message": msg, "login_status": login_status}

        # 启动后台监控
        if self.monitor_task is None or self.monitor_task.done():
            self.monitor_task = asyncio.create_task(self._chat_monitor_loop())

        await self._broadcast({"type": "system", "event": "started"})
        return {"status": "started", "login_status": login_status}

    async def stop(self) -> dict:
        """停止浏览器自动化 + 后台监控。"""
        if self.monitor_task and not self.monitor_task.done():
            self.monitor_task.cancel()
            self.monitor_task = None
        if self.automation:
            try:
                await self.run_pw(self.automation._save_state)
            except Exception:
                pass
            try:
                await self.run_pw(self.automation.close)
            except Exception:
                pass
            self.automation = None
        await self._broadcast({"type": "system", "event": "stopped"})
        return {"status": "stopped"}

    async def relogin(self) -> dict:
        """重新扫码登录 BOSS直聘。"""
        from boss_automation import BossAutomation

        if self.monitor_task and not self.monitor_task.done():
            self.monitor_task.cancel()
            self.monitor_task = None
        if self.automation:
            try:
                await self.run_pw(self.automation.close)
            except Exception:
                pass
            self.automation = None

        def _do_relogin():
            a = BossAutomation(headless=False)
            a.start()
            a.login()
            return a

        try:
            self.automation = await self.run_pw(_do_relogin)
        except Exception as e:
            self.automation = None
            return {"status": "error", "message": f"登录失败: {e}"}

        if self.automation is None or self.automation.page is None:
            self.automation = None
            return {"status": "error", "message": "登录后页面异常，请重试"}

        if self.monitor_task is None or self.monitor_task.done():
            self.monitor_task = asyncio.create_task(self._chat_monitor_loop())

        await self._broadcast({"type": "system", "event": "relogin_ok"})
        return {"status": "ok", "message": "扫码登录成功"}

    async def heartbeat(self) -> dict:
        """手动心跳保活。"""
        if not self.automation or self.automation.page is None:
            return {"status": "error", "message": "浏览器未启动"}
        alive = await self.run_pw(self.automation.heartbeat)
        if not alive:
            return {"status": "error", "message": "登录已过期，请重新登录"}
        return {"status": "ok", "alive": True}

    def pause_monitor(self) -> dict:
        """暂停自动回复监控。"""
        self.monitor_paused = True
        return {"status": "paused"}

    def resume_monitor(self) -> dict:
        """恢复自动回复监控。"""
        self.monitor_paused = False
        return {"status": "resumed"}

    def get_status(self) -> dict:
        """获取当前状态。"""
        from boss_state import get_today_application_count, get_today_auto_reply_count, get_setting

        auto_reply_enabled = get_setting("auto_reply_enabled", "false") == "true"
        return {
            "browser_running": self.automation is not None and self.automation.page is not None,
            "monitor_running": self.monitor_task is not None and not self.monitor_task.done(),
            "monitor_paused": self.monitor_paused,
            "auto_reply_enabled": auto_reply_enabled,
            "in_cooldown": self.automation.in_cooldown() if self.automation else False,
            "today_applications": get_today_application_count(),
            "today_auto_replies": get_today_auto_reply_count(),
            "daily_limit": int(get_setting("daily_apply_limit", "50")),
        }

    # ── 后台监控循环 ──

    async def _chat_monitor_loop(self):
        """后台轮询聊天消息 + 自动回复。"""
        from boss_state import get_setting

        await asyncio.sleep(3)

        if self.automation:
            print("[Boss监控] 后台监控任务已启动")
            await self.run_pw(self.automation.keep_alive)

        # 验证 AI 回复系统
        try:
            sys.path.insert(0, str(_BOSS_DIR / "interview"))
            from llm_client import _load_ai_config

            cfg = _load_ai_config()
            if cfg["api_key"] and len(cfg["api_key"]) > 10:
                print(f"[Boss监控] AI API 已配置（{cfg['model']}），自动回复就绪")
            else:
                print("[Boss监控] AI API Key 未配置，自动回复将不可用")
        except Exception as e:
            print(f"[Boss监控] AI 回复系统加载失败: {e}")

        # 首次立即跑一轮监控
        if self.automation and not self.monitor_paused:
            print("[Boss监控] 执行首次会话扫描...")
            try:
                if self.browser_sync_lock is None:
                    self.browser_sync_lock = asyncio.Lock()
                async with self.browser_sync_lock:
                    result = await self.run_pw(self.automation.run_chat_monitor_cycle)
                if result.get("new_messages", 0) > 0:
                    await self._broadcast({"type": "new_messages", "summary": result})
                if result.get("replies_sent", 0) > 0:
                    await self._broadcast({"type": "auto_reply_sent", "summary": result})
                if result.get("transfer_requested"):
                    await self._broadcast({"type": "transfer_requested"})
            except Exception as e:
                print(f"  [Boss监控] 首次扫描异常: {e}")

        _heartbeat_count = 0
        _heartbeat_misses = 0

        while True:
            try:
                min_delay = int(get_setting("min_reply_delay_sec", "15"))
                max_delay = int(get_setting("max_reply_delay_sec", "20"))
                delay = random.randint(min(min_delay, max_delay), max(min_delay, max_delay) + 5)
                await asyncio.sleep(delay)

                if self.monitor_paused:
                    continue

                if not self.automation:
                    continue

                _heartbeat_count += 1
                alive = await self.run_pw(self.automation.heartbeat)
                if not alive:
                    await asyncio.sleep(5)
                    alive = await self.run_pw(self.automation.heartbeat)

                if not alive:
                    _heartbeat_misses += 1
                else:
                    _heartbeat_misses = 0

                if _heartbeat_misses >= 2:
                    await self._broadcast({
                        "type": "session_expired",
                        "message": "BOSS直聘登录已过期，请重新扫码登录",
                    })
                    break

                if _heartbeat_count >= 1:
                    await self.run_pw(self.automation.keep_alive)

                if get_setting("auto_reply_enabled", "false") != "true":
                    continue

                # 风控冷却期跳过
                try:
                    if self.automation.in_cooldown():
                        print("[Boss监控] 风控冷却中，跳过本轮")
                        continue
                except Exception:
                    pass

                # 执行监控周期
                if self.browser_sync_lock is None:
                    self.browser_sync_lock = asyncio.Lock()
                async with self.browser_sync_lock:
                    result = await self.run_pw(self.automation.run_chat_monitor_cycle)

                if result.get("new_messages", 0) > 0:
                    await self._broadcast({"type": "new_messages", "summary": result})
                if result.get("replies_sent", 0) > 0:
                    await self._broadcast({"type": "auto_reply_sent", "summary": result})
                if result.get("transfer_requested"):
                    await self._broadcast({"type": "transfer_requested"})

                # 安全检查
                try:
                    safety = await self.run_pw(self.automation.inspect_page_safety)
                    if safety and not safety.get("ok"):
                        category = safety.get("category", "")
                        if category in ("captcha", "banned"):
                            await self._broadcast({
                                "type": "safety_alert",
                                "category": category,
                                "message": safety.get("reason", "安全风险"),
                            })
                except Exception:
                    pass

            except asyncio.CancelledError:
                print("[Boss监控] 监控任务被取消")
                break
            except Exception as e:
                print(f"[Boss监控] 监控循环异常: {e}")
                await asyncio.sleep(10)
