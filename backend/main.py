""" Interview Assistant后端入口。"""

from pathlib import Path
import sys

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config_loader import get_fastapi_config
from log import setup_logging, get_logger
from api.chat import router as chat_router
from api.resume import router as resume_router
from api.export import router as export_router
from storage.mysql_client import get_mysql_pool
from storage.redis_client import get_redis_client

# 初始化日志
setup_logging()
logger = get_logger("app")
BACKEND_DIR = Path(__file__).resolve().parent

app = FastAPI(
    title="Interview Assistant",
    description="多 Agent 求职辅助系统",
    version="0.1.0",
)

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

# BOSS 直聘自动化路由（可选模块，Playwright 未安装时跳过）
try:
    from api.boss import router as boss_router
    app.include_router(boss_router)
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
        from config_loader import get_knowledge_base_config

        kb_config = get_knowledge_base_config()
        persist_dir = kb_config.get("chroma_persist_dir", "data/chroma_db")
        data_dir = kb_config.get("data_directory", "data/knowledge_base")

        # 确保目录存在
        Path(BACKEND_DIR.parent / persist_dir).mkdir(parents=True, exist_ok=True)
        Path(BACKEND_DIR.parent / data_dir).mkdir(parents=True, exist_ok=True)

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

    cfg = get_fastapi_config()
    try:
        asyncio.run(_check_required_services())
    except Exception as exc:  
        logger.critical("Startup aborted because dependency check failed: %s", exc, exc_info=True)
        sys.exit(1)

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
