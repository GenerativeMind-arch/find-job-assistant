""" Interview Assistant后端入口。"""

import logging
import sys
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path

# 确保 backend 目录在 sys.path（绝对路径），使 `from config_loader import ...`、
# `from api.chat import ...` 等内部导入无论从哪个工作目录/入口启动都能成功。
_BACKEND_DIR = Path(__file__).resolve().parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config_loader import get_fastapi_config
from log import setup_logging, get_logger
from observability import setup_observability
from api.chat import router as chat_router
from api.resume import router as resume_router
from api.export import router as export_router
from api.interview import router as interview_router
from storage.mysql_client import close_mysql_pool, get_mysql_pool
from storage.redis_client import close_redis_client, get_redis_client

# ── 控制台输出落盘 ──
# boss 自动化模块（boss_automation / boss_firefox / manager）整块用 print() 输出，不接
# logging，所以那些 🚀 投递 / 📡 JS返回 / ⚠️ 风控冷却 日志只存在于启动后端的终端里，
# 一闪而过、无法回查 —— 排查"界面显示已投递、BOSS 上其实没投"这类问题时完全抓瞎。
# 这里把 stdout/stderr 各复制一份到 log/boss.log。
#
# 必须装在模块级：uvicorn debug(reload) 模式下真正跑业务的是 import main:app 的子进程，
# 它的 __name__ 不是 "__main__"，装在 `if __name__ == "__main__"` 里等于没装。
_CONSOLE_LOG_INSTALLED = False
_CONSOLE_LOG_MAX_BYTES = 20 * 1024 * 1024


class _TeeStream:
    """把写入同时送到原始终端和日志文件。"""

    def __init__(self, original, log_file):
        self._original = original
        self._log_file = log_file

    def write(self, data):
        try:
            self._original.write(data)
        except Exception:
            pass
        try:
            self._log_file.write(data)
            self._log_file.flush()  # 长驻进程：不 flush 就没法边跑边看
        except Exception:
            pass
        return len(data)

    def flush(self):
        for stream in (self._original, self._log_file):
            try:
                stream.flush()
            except Exception:
                pass

    def __getattr__(self, name):
        # isatty / encoding / fileno 等委托给原 stream，别破坏依赖它们的库
        return getattr(self._original, name)


def setup_console_log() -> None:
    """把本进程的控制台输出同时写入 log/boss.log（幂等）。"""
    global _CONSOLE_LOG_INSTALLED
    if _CONSOLE_LOG_INSTALLED:
        return
    _CONSOLE_LOG_INSTALLED = True
    try:
        log_dir = _BACKEND_DIR / "log"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / "boss.log"
        # 纯控制台转储，不做行级轮转；启动时超限就整体归档一份
        if log_path.exists() and log_path.stat().st_size > _CONSOLE_LOG_MAX_BYTES:
            log_path.replace(log_dir / "boss.log.1")
        handle = open(log_path, "a", encoding="utf-8", errors="replace")
        sys.stdout = _TeeStream(sys.stdout, handle)
        sys.stderr = _TeeStream(sys.stderr, handle)
    except Exception:
        pass  # 落盘失败不能影响服务启动


# 初始化日志
setup_logging(logging.INFO)
setup_console_log()
logger = get_logger("app")
BACKEND_DIR = _BACKEND_DIR


@asynccontextmanager
async def lifespan(_app: FastAPI):
    async with AsyncExitStack() as resources:
        resources.push_async_callback(close_mysql_pool)
        resources.push_async_callback(close_redis_client)
        await _check_required_services()
        yield


app = FastAPI(
    title="Interview Assistant",
    description="多 Agent 求职辅助系统",
    version="0.1.0",
    lifespan=lifespan,
)

if __name__ != "__main__":
    setup_observability(app)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
app.include_router(chat_router)
app.include_router(resume_router)
app.include_router(export_router)
app.include_router(interview_router)

# BOSS 直聘自动化路由（可选模块，Playwright 未安装时跳过）
try:
    from api.boss import browser_wedged_handler, router as boss_router
    from boss.manager import BrowserWedgedError

    app.include_router(boss_router)
    # 浏览器线程卡死要回 503 + 处置办法，而不是被兜成没有信息量的 500
    app.add_exception_handler(BrowserWedgedError, browser_wedged_handler)
    logger.info("BOSS automation router registered")
except ImportError as exc:
    logger.info("BOSS automation module skipped (dependency missing): %s", exc)
except Exception as exc:
    logger.warning("BOSS automation module failed to load (non-fatal): %s", exc)


@app.get("/health")
async def health():
    return {"status": "ok"}


async def _check_mysql_connection() -> None:
    """启动前检查 MySQL 连通性。"""
    pool = await get_mysql_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute("SELECT 1")
            row = await cursor.fetchone()

    value = row.get("1") if isinstance(row, dict) else row[0] if row else None
    if value != 1:
        raise RuntimeError("MySQL 连通性检查失败：SELECT 1 未返回预期结果")


async def _check_redis_connection() -> None:
    """启动前检查 Redis 连通性。"""
    client = await get_redis_client()
    if not await client.ping():
        raise RuntimeError("Redis 连通性检查失败：PING 未返回成功")


async def _init_knowledge_base() -> None:
    """启动时初始化知识库（ChromaDB Collection 和目录）。"""
    try:
        from services.rag_retriever import _get_collection
        from config_loader import get_knowledge_base_config, resolve_data_path

        kb_config = get_knowledge_base_config()
        persist_dir = resolve_data_path(kb_config.get("chroma_persist_dir", "data/chroma_db"))
        data_dir = resolve_data_path(kb_config.get("data_directory", "data/knowledge_base"))

        # 确保目录存在
        Path(persist_dir).mkdir(parents=True, exist_ok=True)
        Path(data_dir).mkdir(parents=True, exist_ok=True)

        # 初始化 Collection
        collection = _get_collection()
        logger.info("Knowledge base initialized: collection='%s', docs=%d, persist_dir=%s",
                     collection.name, collection.count(), persist_dir)
    except ImportError:
        logger.info("ChromaDB not available, knowledge base disabled")
    except Exception as exc:
        logger.warning("Knowledge base initialization skipped (non-fatal): %s", exc)


async def _check_required_services() -> None:
    """在启动服务前验证关键依赖是否可用。"""
    logger.info("Checking MySQL connectivity before startup")
    await _check_mysql_connection()
    logger.info("MySQL connectivity check passed")

    logger.info("Checking Redis connectivity before startup")
    await _check_redis_connection()
    logger.info("Redis connectivity check passed")

    logger.info("Initializing knowledge base")
    await _init_knowledge_base()


if __name__ == "__main__":
    import asyncio

    # ── Windows + uvicorn reload 兼容补丁 ──
    # uvicorn 在 reload 或多 worker（use_subprocess=True）时，会在 Windows 上强制
    # 设置 WindowsSelectorEventLoopPolicy（uvicorn/loops/asyncio.py:asyncio_setup）。
    # 而 Playwright 同步 API 启动浏览器子进程依赖 ProactorEventLoop（asyncio
    # 子进程在 Windows 仅支持 Proactor），两者冲突导致 Boss 浏览器启动失败
    # （空 NotImplementedError）。
    # 这里覆盖 uvicorn 的 loop setup：不强制 Selector，保持 Windows 默认 Proactor。
    try:
        import uvicorn.loops.asyncio as _uv_asyncio_loops

        def _boss_friendly_asyncio_setup(use_subprocess: bool = False):
            if sys.platform == "win32":
                asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

        _uv_asyncio_loops.asyncio_setup = _boss_friendly_asyncio_setup
    except Exception:
        pass  # uvicorn 升级导致内部变化时静默降级，不影响启动

    cfg = get_fastapi_config()
    logger.info("Starting Interview Assistant server on %s:%s", cfg["host"], cfg["port"])
    is_debug = cfg.get("debug", False)
    workers = cfg.get("workers", 2)

    run_kwargs: dict = {
        "app": "main:app",
        "app_dir": str(BACKEND_DIR),
        "host": cfg["host"],
        "port": cfg["port"],
        "reload": is_debug,
    }
    # reload 模式下 uvicorn 强制单 worker，传 workers 会报警告
    if not is_debug:
        run_kwargs["workers"] = workers

    uvicorn.run(**run_kwargs)
