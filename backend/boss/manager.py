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
from concurrent.futures import Future, ThreadPoolExecutor
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

# 扫描被推迟（浏览器正忙 / 风控冷却中）时的重试间隔
_SCAN_RETRY_SEC = 60
# 监控循环启动后先等一会儿，让浏览器界面稳定下来
_MONITOR_STARTUP_DELAY_SEC = 3
# 心跳失败后的重试前等待
_HEARTBEAT_RETRY_SEC = 5
# 两个节拍的下限，防止被设成 0 导致循环空转
_MIN_HEARTBEAT_SEC = 15
_MIN_SCAN_SEC = 60

# 交互式端点等浏览器锁的上限。后台扫描整轮持锁（开会话 + LLM + 拟人停顿，
# 动辄几分钟），不设上限的话用户点一次"发送"就只能干看着转圈。
LOCK_WAIT_TIMEOUT_SEC = 20.0

# 单次浏览器操作的兜底超时。
#
# 为什么必须有：Playwright 在 BOSS 的风控/验证页上会**永久卡住不返回**（项目里
# 已记录过 locator.click 卡到超时的情况），而 PW 线程池只有一条线程（Playwright
# 同步 API 要求同一线程）。没有上限的话，一次卡死就会把那条线程永久占死，之后
# **所有**浏览器操作都只是排队等死。
#
# 症状极具迷惑性：/api/boss/status 这类不碰浏览器的接口依旧毫秒返回，只有碰浏览器
# 的操作全部转圈 —— 用户看到的就是"系统很卡、一点都不流畅"。
#
# 取值只求把"永远"变成"有限"，不追求精确：搜索最坏情况（翻 6 页 + 补抓 JD 预算
# 45s + 重试退避）约 110s，所以留到 180s。真正耗时更久的那几处（批量投递、整轮
# 扫描、扫码登录）在各自调用点显式传更大的 timeout，见对应注释。
RUN_PW_TIMEOUT_SEC = 180.0

# 排队等待的上限（等 PW 线程空出来），与"执行超时"分开计。
#
# 为什么必须分开：线程池只有一条线程，长操作（批量投递按 45-120s/条插停顿、
# 整轮扫描几分钟）会把后面提交的调用排很久。如果把排队时间也算进超时，一次正常
# 的长投递就会把排在它后面的监控心跳判成"线程卡死"——实测投递刚成功，浏览器就被
# 标记成 wedged，整个系统立刻瘫痪。所以超时只从「真正开始执行」起算。
# 这个上限给得很宽，只兜底"排了半小时都没轮到我"这种明显异常的情况。
RUN_PW_QUEUE_TIMEOUT_SEC = 1800.0

# 整轮会话扫描的上限。扫描本来就是分钟级的（多会话 + LLM 拟回复 + 拟人停顿），
# 所以给得比默认值宽得多；但它**仍然有限**，否则"扫描卡死"会重演原始故障 ——
# 一条死线程把全系统的浏览器操作拖死。
SCAN_CYCLE_TIMEOUT_SEC = 1800.0

# 单条投递的上限（见 apply_batch_async）。
# apply_to_job 内部：goto 45s + 拟人滚动/停顿若干秒 + 详情页 DOM 抽取 + 点击确认，
# 正常 30-60s 走完。这里只兜"永不返回"那一类，不追求贴近真实耗时。
APPLY_JOB_TIMEOUT_SEC = 240.0


def summarize_apply_results(results: list, requested: int) -> dict:
    """Keep batch outcome counts identical across API and agent callers."""
    already = sum(1 for row in results if row.get("already_applied"))
    applied = sum(1 for row in results if row.get("success")) - already
    unknown = sum(1 for row in results if row.get("outcome") == "unknown")
    not_attempted = requested - len(results) + sum(
        1 for row in results if row.get("outcome") == "not_attempted"
    )
    stop = next((row for row in results if row.get("stop_batch")), {})
    return {
        "applied": applied,
        "already_applied": already,
        "unknown": unknown,
        "not_attempted": not_attempted,
        "failed": requested - applied - already - unknown - not_attempted,
        "stop_reason": stop.get("stop_reason"),
        "stop_message": stop.get("message"),
    }


class BrowserBusyError(RuntimeError):
    """在超时时间内没拿到浏览器锁（后台扫描正占用）。

    单独一个类型，是为了让 API 层能把"等不到锁"和"操作本身失败"分开 ——
    前者该回 409 让用户稍后重试，后者才是 500。
    """


class BrowserWedgedError(RuntimeError):
    """浏览器操作超时未返回 —— 那条唯一的 PW 线程已被卡死的调用占死。

    与 BrowserBusyError 的区别：那个是"锁在别人手里，等一会儿就有"，这个是
    "线程已经废了，等多久都没用"。所以它必须让请求立即失败（而不是继续排队），
    并且只能靠「停止浏览器 → 重新启动」重建。
    """


def _swallow_future_result(fut: "asyncio.Future") -> None:
    """取走已放弃等待的 future 的异常，避免 asyncio 打 "never retrieved" 噪声。"""
    if not fut.cancelled():
        fut.exception()


def _is_target_closed(exc: BaseException) -> bool:
    """判断异常是不是"浏览器/页面已经没了"。

    按类名 + 文案双重判断：不直接 import playwright 的异常类是为了不让 manager
    依赖它的私有路径（`playwright._impl._errors`），而文案在 Playwright 各版本里
    是稳定的（"Target page, context or browser has been closed"）。
    """
    if type(exc).__name__ in ("TargetClosedError", "BrowserClosedError"):
        return True
    return "Target page, context or browser has been closed" in str(exc)


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
        # 「有几个前台操作在等浏览器」。与 monitor_paused 的区别：monitor_paused 是
        # 已经拿到锁（浏览器真被占用），这个从开始等锁就计数 —— 正在跑的扫描靠它
        # 提前收尾，把锁让出来，否则前台要等整轮扫描跑完。
        # 用计数而不是布尔：两个前台操作重叠时，先结束的那个不该把后者的信号清掉。
        self._foreground_waiters: int = 0
        self.browser_sync_lock: Optional[asyncio.Lock] = None
        self._playwright_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="boss-pw")
        # 那条唯一的 PW 线程是否已卡死。置位后所有 run_pw 立即失败，不再排队等死。
        self._pw_wedged: bool = False
        self._event_listeners: list[Callable] = []
        # 用户拨动「自动回复」开关时用来立刻唤醒监控循环，
        # 否则最长要等一个节拍（心跳 2 分钟 / 扫描 1 小时）才会响应，看起来像没生效。
        self._monitor_wake = asyncio.Event()
        # 监控循环的两个独立节拍，存的是 time.monotonic() 绝对时刻。
        # 必须是实例属性而非循环局部变量，才能跨迭代存活。
        self._next_heartbeat_at: float = 0.0
        self._next_scan_at: float = 0.0

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

    async def run_pw(
        self,
        fn: Callable,
        *args,
        timeout: Optional[float] = RUN_PW_TIMEOUT_SEC,
    ) -> Any:
        """在 Playwright 专属线程中执行同步操作。

        Playwright 同步 API 检测到 event loop 会拒绝运行，
        所以需要清除该线程的 asyncio 状态。

        注意：uvicorn reload 模式下 worker 进程默认使用 SelectorEventLoop，
        而 Playwright 同步 API 启动 driver 子进程依赖 ProactorEventLoop（Windows），
        因此在 PW 线程内强制使用 WindowsProactorEventLoopPolicy。

        Args:
            timeout: 本次操作的最长等待秒数，默认 RUN_PW_TIMEOUT_SEC。
                传 None 表示不设上限 —— 只有"慢是正常的、且无法预估"的操作才该这么用
                （如扫码登录要等用户掏手机），普通操作一律用默认值。
        """
        # 线程已经废了就别再排队：排队等一条死线程，表现就是前端无声转圈，
        # 连「停止浏览器」都点不动，只能重启进程。
        if self._pw_wedged:
            raise BrowserWedgedError(
                "浏览器线程已卡死（此前的操作超时未返回）。"
                "请点「停止浏览器」，再重新「启动浏览器」。"
            )

        loop = asyncio.get_running_loop()
        # 用它标记"这条调用真的开始执行了"，从而把排队等待排除在超时之外。
        # 用 asyncio.Event + call_soon_threadsafe 而不是轮询 threading.Event：
        # 轮询要选粒度，粗了给每次调用凭空加上几十毫秒延迟，细了就是空转。
        started = asyncio.Event()

        def _wrapper():
            try:
                loop.call_soon_threadsafe(started.set)
            except Exception:
                pass
            try:
                if sys.platform == "win32":
                    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
                asyncio.set_event_loop(None)
            except Exception:
                pass
            return fn(*args)

        fut = loop.run_in_executor(self._playwright_executor, _wrapper)

        # ── 阶段一：排队。等它开始执行，不设执行超时 ──
        starter = asyncio.ensure_future(started.wait())
        try:
            await asyncio.wait(
                {starter, fut},
                timeout=RUN_PW_QUEUE_TIMEOUT_SEC,
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            starter.cancel()
        if not started.is_set() and not fut.done():
            self._pw_wedged = True
            self.monitor_paused = True
            fut.add_done_callback(_swallow_future_result)
            raise BrowserWedgedError(
                f"排队等待浏览器线程超过 {RUN_PW_QUEUE_TIMEOUT_SEC:.0f}s 仍未轮到，"
                "线程可能已卡死。请点「停止浏览器」，再重新「启动浏览器」。"
            )

        # ── 阶段二：已开始执行（或早已结束），从这里起算超时 ──
        try:
            if fut.done() or timeout is None:
                return await fut
            # shield：超时只放弃"等"，不能让 fut 被取消 —— 线程已经在跑了，
            # 取消 futures 对象不会让线程停下来，反而可能让后续状态更乱。
            return await asyncio.wait_for(asyncio.shield(fut), timeout)
        except asyncio.TimeoutError:
            self._pw_wedged = True
            # 线程还攥着"浏览器正被占用"的语义，别让监控再往上撞
            self.monitor_paused = True
            # fut 仍在后台跑，终会落地。这之后再没人 await 它，所以要主动取走
            # 结果/异常，否则 asyncio 会打 "exception was never retrieved"。
            fut.add_done_callback(_swallow_future_result)
            print(
                f"  ⛔ 浏览器操作超过 {timeout:.0f}s 未返回，判定 PW 线程卡死"
                f"（{getattr(fn, '__name__', fn)}）"
            )
            raise BrowserWedgedError(
                f"浏览器操作超过 {timeout:.0f}s 未返回，浏览器线程已卡死。"
                "请点「停止浏览器」，再重新「启动浏览器」。"
            ) from None
        except Exception as e:
            # 浏览器被关掉了（手动关窗口 / 进程崩溃）：必须清掉陈旧引用，
            # 否则 browser_running 会一直报 true，用户点什么都是在撞一具尸体
            # （日志里那句 "Target page, context or browser has been closed"）。
            if _is_target_closed(e):
                self._forget_dead_browser(e)
            raise

    def _forget_dead_browser(self, exc: BaseException) -> None:
        """浏览器已关闭 —— 丢弃引用，让 UI 回到「未启动」而不是继续假装在跑。"""
        print(f"  ⛔ 浏览器已关闭（{type(exc).__name__}: {exc}），清理陈旧引用")
        self.automation = None
        self.monitor_paused = False
        self._broadcast_sync({"type": "system", "event": "browser_closed"})

    def _broadcast_sync(self, event: dict) -> None:
        """从非 async 上下文广播（事件循环可能正忙，不能 await）。"""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self._broadcast(event))

    def _discard_wedged_thread(self) -> None:
        """丢弃卡死的 PW 线程，换一条新的。

        Python 不能强杀线程，所以那条线程（连同它攥着的 Playwright/browser 对象）
        只能被遗弃。换新线程之后功能立刻恢复 —— 卡死的那条只是泄漏着，什么都不做。

        代价要提前知道：ThreadPoolExecutor 的工作线程是**非守护线程**，解释器退出时
        （concurrent.futures 的 atexit 钩子和 threading._shutdown 都会）去 join 它，
        而它永远不会返回。所以卡死过之后，后端进程可能无法优雅退出，需要强制结束
        （关掉终端窗口 / 任务管理器）。这是"不能杀线程"的固有代价，不是本处能绕开的。

        Playwright 的同步对象绑死在创建它的线程上，所以光换线程不够，还必须重建
        浏览器对象 —— 这就是「卡死只能靠停止/重启浏览器恢复」的原因。
        """
        old = self._playwright_executor
        self._playwright_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="boss-pw")
        self._pw_wedged = False
        self.monitor_paused = False
        try:
            old.shutdown(wait=False)  # 不 wait：卡住的线程不会返回，等它就是再死一次
        except Exception:
            pass

    async def _batch_pause(self, sec: float) -> bool:
        """批次停顿（可中断）。返回 False = 该中止（浏览器已停止 / PW 线程已卡死）。

        分片睡而不是一觉睡到点：一次「停止浏览器」或一次卡死判定都应当立刻结束
        批次，而不是把剩下的几百秒睡完 —— 实测出现过 stop 之后批次还在往下投。
        """
        deadline = time.monotonic() + max(0.0, sec)
        while True:
            if self._pw_wedged or self.automation is None:
                return False
            remain = deadline - time.monotonic()
            if remain <= 0:
                return True
            await asyncio.sleep(min(1.0, remain))

    async def apply_job_async(self, url: str, greeting: Optional[str] = None) -> dict:
        """Synchronize the request timeout with the browser's click boundary."""
        dispatch = Future()
        try:
            return await self.run_pw(
                self.automation.apply_to_job, url, greeting, dispatch,
                timeout=APPLY_JOB_TIMEOUT_SEC,
            )
        except asyncio.CancelledError:
            dispatch.cancel()
            raise
        except BrowserWedgedError as exc:
            before_click = dispatch.cancel()
            detail = "已阻止该岗位继续投递。" if before_click else "平台结果待核对，请先检查 BOSS 会话。"
            return {
                "success": False,
                "outcome": "not_attempted" if before_click else "unknown",
                "message": f"{exc} {detail}",
                "stop_batch": True,
                "stop_reason": "pw_wedged",
            }

    async def apply_batch_async(self, job_urls: list, greeting: Optional[str] = None) -> list:
        """批量投递：逐条 apply_to_job，拟人节奏与停顿放在异步层。

        为什么循环不在 PW 线程里：BOSS 的风控/验证页会让 Playwright 调用**永不返回**，
        而兜底超时只有包着它的那次 run_pw 才算数。把整批（含 45-120s/条的停顿、
        300-600s 的休息）塞进一次 run_pw，等于把上限设成"整批"——按条数放大到几十分钟；
        一条卡住就把唯一那条 PW 线程占死，监控心跳排队到 1800s 判定卡死，全系统浏览器
        操作一起瘫痪（实测日志正是这条路径）。改为每条一次 run_pw（上限
        APPLY_JOB_TIMEOUT_SEC）之后：单条卡住最多等一次上限就被兜底超时抓住、干净地走
        卡死流程；停顿期间 PW 线程是空的，心跳照常。

        停顿仍按原语义：条间 batch_delay_min/max 乘风控倍率，每 batch_rest_every 条
        长休息 300-600s，冷却期结束后由用户决定是否发起新批次。
        """
        from boss_state import get_apply_allowance, get_setting

        # 这批开始之前线程就已经废了：直接抛，让 API 层回 503 + 指引。
        # 不这么做的话会先生成"投了一条、失败一条"的假结果 —— 用户只看到"成功 0 个"，
        # 反而看不出真正该做的是「停止浏览器 → 重新启动」。
        if self._pw_wedged:
            raise BrowserWedgedError(
                "浏览器线程已卡死（此前的操作超时未返回）。"
                "请点「停止浏览器」，再重新「启动浏览器」。"
            )

        results: list = []
        min_delay = int(get_setting("batch_delay_min_sec", "30") or 30)
        max_delay = int(get_setting("batch_delay_max_sec", "90") or 90)
        rest_every = int(get_setting("batch_rest_every", "8") or 0)
        applied_since_rest = 0

        for i, url in enumerate(job_urls):
            if self._pw_wedged or self.automation is None:
                print("  ⛔ 浏览器已停止或线程已卡死，中止本批")
                break

            automation = self.automation
            # 每轮重取倍率：本批中途撞了风控，后面的间隔要立刻变慢
            mult = float(get_apply_allowance().get("delay_multiplier", 1.0) or 1.0)

            if automation.in_cooldown():
                results.append({
                    "success": False, "outcome": "not_attempted",
                    "stop_batch": True, "stop_reason": "cooldown",
                    "message": "平台风控冷却中，本批已停止。",
                })
                break

            if i > 0:
                delay = random.uniform(min_delay * mult, max_delay * mult)
                print(f"  ⏳ 等待 {delay:.0f}s 后投递下一条..." + (f"（风控倍率 ×{mult:.1f}）" if mult > 1 else ""))
                if not await self._batch_pause(delay):
                    break

            # 周期性长休息（打散机械节奏）
            if rest_every > 0 and applied_since_rest >= rest_every:
                rest = random.uniform(300, 600)
                print(f"  ☕ 已连续投递 {applied_since_rest} 条，休息 {rest:.0f}s 降低风控风险...")
                if not await self._batch_pause(rest):
                    break
                applied_since_rest = 0

            # 停顿期间浏览器可能被停掉/重建（重登），这里重取一次，避免拿着旧对象调
            automation = self.automation
            if automation is None:
                print("  ⛔ 浏览器已停止，中止本批")
                break

            result = await self.apply_job_async(url, greeting)
            results.append(result)

            if result.get("success"):
                applied_since_rest += 1

            if result.get("stop_batch"):
                print(f"  ⛔ 停止本批（{result.get('stop_reason') or '已达上限'}）")
                break
            # 账号异常等强风控：立即停止，交由上层人工处理
            safety = result.get("safety") or {}
            if safety.get("category") in ("banned", "captcha"):
                print(f"  ⛔ 触发强风控({safety.get('category')})，停止本批等待人工处理")
                break

        return results

    @asynccontextmanager
    async def lock_browser(self, timeout: Optional[float] = None):
        """上下文管理器：标记浏览器忙 + 获取浏览器锁。

        确保不跟 chat_monitor_loop 争夺浏览器资源。
        任何异常都会安全释放锁和恢复忙标记。

        注意 monitor_paused 现在的语义是「浏览器正被前台操作占用」（瞬时），
        不是「用户关闭了自动回复」——后者由持久化的 auto_reply_enabled 承担。
        监控循环在浏览器忙时会推迟扫描，但心跳照常。

        Args:
            timeout: 获取锁的最长等待秒数；None 表示一直等。
                后台扫描整轮持有这把锁（开会话 + 调 LLM + 拟人停顿，动辄几分钟），
                手动操作要是不设上限，前端就会无声地转上几分钟 —— 所以交互式
                端点应当传一个超时，超时后由调用方回一句明确的提示。

        另外会置 foreground_waiting：正在跑的扫描看到它就提前收尾让路
        （见 boss_automation.scan_should_yield），否则要等它把这轮跑完。
        """
        was_paused = self.monitor_paused
        self.monitor_paused = True
        self._foreground_waiters += 1
        lock = None
        acquired = False
        try:
            if self.browser_sync_lock is None:
                self.browser_sync_lock = asyncio.Lock()
            lock = self.browser_sync_lock
            # 锁空闲时直接拿 —— 旧实现在检查之前先 sleep(0.5)，等于给每次
            # 手动操作都强加半秒，而绝大多数时候锁根本没人占。
            # 注意超时只包住 acquire：with 体内自己的 TimeoutError 不该被误判成"锁忙"。
            try:
                if timeout is None:
                    await lock.acquire()
                else:
                    await asyncio.wait_for(lock.acquire(), timeout)
            except asyncio.TimeoutError:
                raise BrowserBusyError(
                    f"等待浏览器锁超过 {timeout} 秒（后台扫描正占用浏览器）"
                ) from None
            acquired = True
            yield
        finally:
            if acquired and lock is not None and lock.locked():
                lock.release()
            self.monitor_paused = was_paused
            self._foreground_waiters -= 1

    @property
    def foreground_waiting(self) -> bool:
        """是否有前台操作在等浏览器（供扫描让路和诊断用）。"""
        return self._foreground_waiters > 0

    # ── 系统控制 ──

    def _attach_yield_hook(self) -> None:
        """告诉自动化层"有前台操作在等浏览器"。

        扫描每处理完一个会话就查一次（见 boss_automation.scan_should_yield），
        有人等着就提前结束本轮，把手动发送的等待从"整轮扫描"压到"一个会话"以内。
        每次新建 automation 后都要挂 —— start() 和 relogin() 各有一处。
        """
        if self.automation is not None:
            self.automation.should_yield = lambda: self.foreground_waiting

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

        # 上一次卡死过：那条线程和被它攥着的 Playwright 对象都已经废了，
        # 先换线程、丢掉旧对象，否则这里的每一步都会撞回同一堵墙。
        if self._pw_wedged:
            print("[BossManager] 检测到 PW 线程卡死，丢弃旧线程后重建浏览器")
            self.automation = None
            self._discard_wedged_thread()

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

        self._attach_yield_hook()

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
        # 新浏览器意味着全新的生命周期：清掉可能残留的「浏览器忙」标记，
        # 并把两个节拍时钟归零，让首轮心跳（以及开启时的首轮扫描）立即执行。
        self.monitor_paused = False
        self._next_heartbeat_at = 0.0
        self._next_scan_at = 0.0
        if self.monitor_task is None or self.monitor_task.done():
            self.monitor_task = asyncio.create_task(self._chat_monitor_loop())

        await self._broadcast({"type": "system", "event": "started"})
        return {"status": "started", "login_status": login_status}

    async def stop(self) -> dict:
        """停止浏览器自动化 + 后台监控。

        这也是「浏览器卡死」的唯一出口：卡死时不去做优雅关闭（那会排在同一条死线程
        后面，等于点了没反应），直接把线程和浏览器对象一起丢掉。
        """
        if self.monitor_task and not self.monitor_task.done():
            self.monitor_task.cancel()
            self.monitor_task = None
        if self.automation:
            if self._pw_wedged:
                print("[BossManager] PW 线程卡死，跳过优雅关闭，直接丢弃浏览器对象")
            else:
                try:
                    await self.run_pw(self.automation._save_state, timeout=15)
                except Exception:
                    pass
                try:
                    await self.run_pw(self.automation.close, timeout=20)
                except Exception:
                    pass
            self.automation = None
        # 无论卡死与否都换一条干净的线程：卡死的线程必须丢（等它没用），
        # 没卡死时重建也几乎零成本，换来的是"下一次启动一定是干净的"。
        self._discard_wedged_thread()
        await self._broadcast({"type": "system", "event": "stopped"})
        return {"status": "stopped"}

    async def relogin(self) -> dict:
        """重新扫码登录 BOSS直聘。"""
        from boss_automation import BossAutomation

        if self.monitor_task and not self.monitor_task.done():
            self.monitor_task.cancel()
            self.monitor_task = None
        if self.automation:
            if self._pw_wedged:
                print("[BossManager] PW 线程卡死，跳过优雅关闭，直接丢弃浏览器对象")
            else:
                try:
                    await self.run_pw(self.automation.close, timeout=20)
                except Exception:
                    pass
            self.automation = None

        # 同 start()：卡死过就先换一条干净的线程再重建
        if self._pw_wedged:
            self._discard_wedged_thread()

        def _do_relogin():
            a = BossAutomation(headless=False)
            a.start()
            a.login()
            return a

        try:
            # a.login() 要等用户掏手机扫码，耗时不可预估 → 不设上限
            self.automation = await self.run_pw(_do_relogin, timeout=None)
        except Exception as e:
            self.automation = None
            return {"status": "error", "message": f"登录失败: {e}"}

        if self.automation is None or self.automation.page is None:
            self.automation = None
            return {"status": "error", "message": "登录后页面异常，请重试"}

        # 同 start()：重新登录后重置瞬时状态与节拍时钟
        self.monitor_paused = False
        self._attach_yield_hook()
        self._next_heartbeat_at = 0.0
        self._next_scan_at = 0.0
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
        """标记浏览器忙（运行期临时，不改持久化的自动回复开关）。

        遗留端点 /api/boss/monitor/pause。真正控制"是否回复"的是
        set_auto_reply_enabled（持久化）；这里只是让循环暂时退让。
        """
        self.monitor_paused = True
        return {"status": "paused", **self.get_status()}

    def resume_monitor(self) -> dict:
        """清除浏览器忙标记（运行期临时）。注意：它不会让已停止的自动回复恢复，
        那个只能由 set_auto_reply_enabled(True) 完成。"""
        self.monitor_paused = False
        self._monitor_wake.set()
        return {"status": "resumed", **self.get_status()}

    async def set_auto_reply_enabled(self, enabled: bool) -> dict:
        """开关「自动回复」——只写持久化设置，它是唯一真值来源。

        UI 上那颗「停止回复 / 恢复回复」按钮的唯一入口。
        auto_reply_enabled 落在 SQLite，所以后端重启、浏览器重启都不会丢；
        只有再点一次「恢复回复」才会重新开始回复。

        刻意不动 monitor_paused（那是「浏览器忙」的瞬时标记），
        这样心跳/保活不受本开关影响 —— 停止回复期间会话依然保活。
        """
        from boss_state import set_setting

        set_setting("auto_reply_enabled", "true" if enabled else "false")

        if enabled:
            # 循环可能因为登录过期等原因已经结束，重新拉起来
            if self.monitor_task is None or self.monitor_task.done():
                if self.automation and self.automation.page is not None:
                    self.monitor_task = asyncio.create_task(self._chat_monitor_loop())
            # 让恢复后的第一轮扫描立刻发生，而不是干等一个扫描周期（1 小时）。
            # 先重置到期时刻再唤醒：即使事件理论上丢了，下一次心跳（≤2 分钟）
            # 也会发现扫描已到期，不会退化成等满 1 小时。
            self._next_scan_at = 0.0
            self._monitor_wake.set()

        return self.get_status()

    def _monitor_state(self, auto_reply_enabled: bool) -> str:
        """给 UI 用的单一状态：off / running / no_browser。

        只由「持久化开关」+「浏览器是否存在」+「循环是否还活着」决定。
        monitor_paused 是「浏览器忙」的瞬时标记，不参与 UI 状态 —— 否则用户
        点完停止回复会看到 paused，而它的真实语义应该是 off。
        """
        if self.automation is None or self.automation.page is None:
            return "no_browser"
        if not auto_reply_enabled:
            return "off"
        # 循环可能因登录过期已 break 退出，此时不该显示"运行中"
        if self.monitor_task is None or self.monitor_task.done():
            return "off"
        return "running"

    def get_status(self) -> dict:
        """获取当前状态。"""
        from boss_state import (
            get_apply_allowance,
            get_today_application_count,
            get_today_auto_reply_count,
            get_setting,
        )

        auto_reply_enabled = get_setting("auto_reply_enabled", "false") == "true"
        # 预算算失败不能连状态接口一起挂掉（前端每 15s 轮询一次，500 会刷屏）
        try:
            _allowance = get_apply_allowance()
        except Exception:
            _allowance = {
                "budget": 0, "applied": 0, "remaining": 0,
                "base": 0, "max": 0, "strikes": 0,
                "delay_multiplier": 1.0, "dynamic": False,
                "day_over": False, "reason": "",
            }
        return {
            "browser_running": self.automation is not None and self.automation.page is not None,
            # 卡死时必须让前端知道：此时所有浏览器操作都会立即失败，
            # 唯一的出路是「停止浏览器 → 重新启动」。
            "browser_wedged": self._pw_wedged,
            "monitor_running": self.monitor_task is not None and not self.monitor_task.done(),
            "monitor_paused": self.monitor_paused,
            "auto_reply_enabled": auto_reply_enabled,
            "monitor_state": self._monitor_state(auto_reply_enabled),
            "in_cooldown": self.automation.in_cooldown() if self.automation else False,
            "today_applications": get_today_application_count(),
            "today_auto_replies": get_today_auto_reply_count(),
            # daily_limit = **今日当前**预算（动态模式下会涨会缩），不是设置页那个固定值。
            # 前端状态栏显示的就是它。完整明细见 apply_budget。
            "daily_limit": _allowance["budget"],
            "apply_budget": _allowance,
        }

    # ── 后台监控循环 ──

    async def _chat_monitor_loop(self):
        """后台轮询聊天消息 + 自动回复，跑在两个独立节拍上：

        - 心跳/保活（heartbeat_interval_sec，默认 2 分钟）：与「自动回复」开关无关，
          停止回复期间照跑，保证会话不过期。
        - 未读扫描 + 自动回复（scan_interval_sec，默认 1 小时）：由持久化的
          auto_reply_enabled 控制，只有它才能让回复真正停下。

        点「恢复回复」会重置扫描到期时刻并唤醒本循环，因此是立即扫一轮，
        而不是干等满一个扫描周期。
        """
        from boss_state import get_setting

        await asyncio.sleep(_MONITOR_STARTUP_DELAY_SEC)

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

        # 两个节拍时钟归零 → 首轮立即心跳；若自动回复为开，也立即扫一轮。
        # （取代了原先单独的「首次立即跑一轮监控」块）
        self._next_heartbeat_at = 0.0
        self._next_scan_at = 0.0
        _heartbeat_misses = 0

        while True:
            try:
                hb_iv = max(
                    _MIN_HEARTBEAT_SEC, int(get_setting("heartbeat_interval_sec", "120") or 120)
                )
                scan_iv = max(
                    _MIN_SCAN_SEC, int(get_setting("scan_interval_sec", "3600") or 3600)
                )

                # ── 1) 心跳 / 保活：与「自动回复」开关无关 ──
                # 停止回复期间也要保活，否则会话会在两次扫描之间过期。
                now = time.monotonic()
                if now >= self._next_heartbeat_at:
                    self._next_heartbeat_at = now + hb_iv
                    if self.automation:
                        alive = await self.run_pw(self.automation.heartbeat)
                        if not alive:
                            await asyncio.sleep(_HEARTBEAT_RETRY_SEC)
                            alive = await self.run_pw(self.automation.heartbeat)

                        if alive:
                            _heartbeat_misses = 0
                            # 浏览器正被前台操作占用时不去抢页面做 keep_alive
                            if not self.monitor_paused:
                                await self.run_pw(self.automation.keep_alive)
                        else:
                            _heartbeat_misses += 1
                            if _heartbeat_misses >= 2:
                                await self._broadcast({
                                    "type": "session_expired",
                                    "message": "BOSS直聘登录已过期，请重新扫码登录",
                                })
                                break

                # ── 2) 未读扫描 + 自动回复（默认 1 小时一次）──
                now = time.monotonic()
                if now >= self._next_scan_at:
                    if get_setting("auto_reply_enabled", "false") != "true":
                        # 已停止回复：不扫不回。推迟到下一个心跳点 —— 既不会空转，
                        # 也让从设置页开启自动回复最迟在 hb_iv 内生效。
                        self._next_scan_at = now + hb_iv
                    elif self.automation is None or self.monitor_paused:
                        # 浏览器不可用或正忙，稍后重试
                        self._next_scan_at = now + _SCAN_RETRY_SEC
                    elif self._in_cooldown_safe():
                        print("[Boss监控] 风控冷却中，跳过本轮扫描")
                        self._next_scan_at = now + _SCAN_RETRY_SEC
                    else:
                        # 先排期再扫描，避免扫描耗时期间被重复触发
                        self._next_scan_at = now + scan_iv
                        await self._run_scan_cycle()

                # ── 3) 睡到下一个节拍，或被 _monitor_wake 提前唤醒 ──
                wait_for = max(
                    0.0,
                    min(self._next_heartbeat_at, self._next_scan_at) - time.monotonic(),
                )
                self._monitor_wake.clear()  # 到这里之前不能有 await，否则会丢唤醒
                try:
                    await asyncio.wait_for(self._monitor_wake.wait(), timeout=wait_for)
                except asyncio.TimeoutError:
                    pass

            except asyncio.CancelledError:
                print("[Boss监控] 监控任务被取消")
                break
            except Exception as e:
                print(f"[Boss监控] 监控循环异常: {e}")
                await asyncio.sleep(10)

    def _in_cooldown_safe(self) -> bool:
        """风控冷却判断；异常时视为不在冷却，避免因异常卡住扫描。"""
        try:
            return bool(self.automation and self.automation.in_cooldown())
        except Exception:
            return False

    async def _run_scan_cycle(self):
        """执行一轮未读扫描 + 自动回复，并广播结果与安全告警。"""
        if self.browser_sync_lock is None:
            self.browser_sync_lock = asyncio.Lock()
        async with self.browser_sync_lock:
            # 整轮扫描要开会话、调 LLM、还带拟人停顿，几分钟是正常的 —— 用一个
            # 远高于默认 180s 但**仍然有限**的上限：既不误杀正常扫描，
            # 也不让"扫描卡死"重新变成那个占死线程、全系统转圈的原始故障。
            result = await self.run_pw(self.automation.run_chat_monitor_cycle, timeout=SCAN_CYCLE_TIMEOUT_SEC)

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
