"""全局配置加载器 — 读取 config.yaml 并提供统一的配置访问接口

用法:
    from config_loader import get_llm_config, get_embedding_config, ...
    cfg = get_llm_config()   # {"model": ..., "api_base": ..., "api_key": ..., ...}
"""

import os
from pathlib import Path

import yaml
from dotenv import dotenv_values

_config: dict | None = None
_dotenv: dict | None = None
_BACKEND_DIR = Path(__file__).resolve().parent


def _resolve_env_override(env_var_name: str, default: str = "") -> str:
    """解析通用环境变量覆盖：优先 .env 文件，其次系统环境变量。"""
    dotenv = _get_dotenv()
    return dotenv.get(env_var_name) or os.environ.get(env_var_name, default)


def _resolve_int_override(env_var_name: str, default: int) -> int:
    """解析整型环境变量覆盖，无效值时回退默认值。"""
    raw = _resolve_env_override(env_var_name, "")
    if raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _get_dotenv() -> dict:
    """加载 backend/.env 文件内容（缓存），与当前工作目录无关。"""
    global _dotenv
    if _dotenv is None:
        env_path = _BACKEND_DIR / ".env"
        _dotenv = dotenv_values(env_path) if env_path.exists() else {}
    return _dotenv


def _resolve_api_key(env_var_name: str) -> str:
    """解析 API Key：优先 .env 文件，其次系统环境变量。"""
    dotenv = _get_dotenv()
    return dotenv.get(env_var_name) or os.environ.get(env_var_name, "")


def load_config(config_path: str | None = None) -> dict:
    """加载配置文件并缓存，默认读取 backend/config.yaml。"""
    global _config
    if config_path is None:
        config_path = str(_BACKEND_DIR / "config.yaml")
    with open(config_path, "r", encoding="utf-8") as f:
        _config = yaml.safe_load(f)
    return _config


def get_config() -> dict:
    """获取已加载的全局配置，未加载时自动从默认路径加载。"""
    if _config is None:
        load_config()
    return _config


# ------------------------------------------------------------------
# 各模块便捷配置访问
# ------------------------------------------------------------------

def get_server_config() -> dict:
    """返回服务端连接配置（host）。"""
    cfg = get_config().get("server", {})
    return {
        "host": _resolve_env_override("SERVER_HOST", cfg.get("host", "")),
    }


def get_server_host() -> str:
    """返回服务端 Host/IP。"""
    return get_server_config()["host"]

def get_llm_config() -> dict:
    """返回 LLM 配置（provider, model, api_base, api_key, temperature, max_tokens）。"""
    cfg = get_config()["llm"]
    api_key_env = cfg.get("api_key_env", "")
    return {
        "provider": cfg.get("provider", "openai"),
        "model": cfg["model"],
        "api_base": cfg.get("api_base", ""),
        "api_key": _resolve_api_key(api_key_env) if api_key_env else "",
        "temperature": cfg.get("temperature", 0.3),
        "max_tokens": cfg.get("max_tokens", 4096),
        "api_version": cfg.get("api_version", ""),
        "deployment": cfg.get("deployment", ""),
        "model_kwargs": cfg.get("model_kwargs", {}),
        "timeout": cfg.get("timeout", None),
    }


def get_embedding_config() -> dict:
    """返回 Embedding 配置（provider, model, api_base, api_key）。"""
    cfg = get_config()["embedding"]
    api_key_env = cfg.get("api_key_env", "")
    return {
        "provider": cfg.get("provider", "dashscope"),
        "model": cfg["model"],
        "api_base": cfg.get("api_base", ""),
        "api_key": _resolve_api_key(api_key_env) if api_key_env else "",
        "api_version": cfg.get("api_version", ""),
        "deployment": cfg.get("deployment", ""),
        "dimensions": cfg.get("dimensions", None),
        "model_kwargs": cfg.get("model_kwargs", {}),
    }


def get_rerank_config() -> dict:
    """返回 Rerank 配置（provider, model, api_base, api_key, top_n）。"""
    cfg = get_config()["rerank"]
    api_key_env = cfg.get("api_key_env", "")
    return {
        "provider": cfg.get("provider", "dashscope"),
        "model": cfg["model"],
        "api_base": cfg.get("api_base", ""),
        "api_key": _resolve_api_key(api_key_env) if api_key_env else "",
        "top_n": cfg.get("top_n", 5),
        "api_version": cfg.get("api_version", ""),
        "model_kwargs": cfg.get("model_kwargs", {}),
    }


def get_redis_config() -> dict:
    """返回 Redis 配置（host, port, db, password）。"""
    cfg = get_config().get("redis", {})
    host = cfg.get("host", "")
    if not host and cfg.get("host_from") == "server":
        host = get_server_host()
    return {
        "host": _resolve_env_override("REDIS_HOST", host),
        "port": _resolve_int_override("REDIS_PORT", cfg.get("port", 6379)),
        "db": _resolve_int_override("REDIS_DB", cfg.get("db", 0)),
        "password": _resolve_env_override("REDIS_PASSWORD", cfg.get("password", "")),
    }


def get_mysql_config() -> dict:
    """返回 MySQL 配置（host, port, user, password, database, charset, pool_size）。"""
    cfg = get_config().get("mysql", {})
    host = cfg.get("host", "")
    if not host and cfg.get("host_from") == "server":
        host = get_server_host()
    password_env = cfg.get("password_env", "")
    return {
        "host": _resolve_env_override("MYSQL_HOST", host),
        "port": _resolve_int_override("MYSQL_PORT", cfg.get("port", 3306)),
        "user": _resolve_env_override("MYSQL_USER", cfg.get("user", "root")),
        "password": _resolve_env_override(
            "MYSQL_PASSWORD",
            _resolve_api_key(password_env) if password_env else cfg.get("password", ""),
        ),
        "database": _resolve_env_override("MYSQL_DATABASE", cfg.get("database", "interview_assistant")),
        "charset": _resolve_env_override("MYSQL_CHARSET", cfg.get("charset", "utf8mb4")),
        "pool_size": _resolve_int_override("MYSQL_POOL_SIZE", cfg.get("pool_size", 5)),
    }


def get_fastapi_config() -> dict:
    """返回 FastAPI 配置（host, port, debug）。"""
    cfg = get_config().get("fastapi", {})
    return {
        "host": _resolve_env_override("FASTAPI_HOST", cfg.get("host", "0.0.0.0")),
        "port": _resolve_int_override("FASTAPI_PORT", cfg.get("port", 8000)),
        "debug": _resolve_env_override("FASTAPI_DEBUG", str(cfg.get("debug", False))).lower() in {"1", "true", "yes", "on"},
        "workers": _resolve_int_override("FASTAPI_WORKERS", cfg.get("workers", 2)),
    }


def get_rag_config() -> dict:
    """返回 RAG 参数（chunk_size, chunk_overlap, search_top_k, rerank_top_n）。"""
    return get_config().get("rag", {})


def get_vector_store_config() -> dict:
    """返回向量数据库配置（persist_directory）。"""
    return get_config().get("vector_store", {})


def get_job_search_config() -> dict:
    """返回 JD 搜索配置（MCP 相关参数 + fallback 策略）。"""
    cfg = get_config().get("job_search", {})
    return {
        "mcp_enabled": cfg.get("mcp_enabled", True),
        "mcp_timeout": cfg.get("mcp_timeout", 30),
        "mcp_server_command": cfg.get("mcp_server_command", "npx"),
        "mcp_server_args": cfg.get("mcp_server_args", ["-y", "mcp-jobs"]),
        "default_city": cfg.get("default_city", ""),
        "fallback_to_llm": cfg.get("fallback_to_llm", True),
    }


def get_job_knowledge_config() -> dict:
    """返回岗位知识库配置（ChromaDB collection 名称、检索参数等）。"""
    cfg = get_config().get("job_knowledge", {})
    return {
        "enabled": cfg.get("enabled", True),
        "chroma_collection": cfg.get("chroma_collection", "job_knowledge"),
        "search_top_k": cfg.get("search_top_k", 10),
        "search_top_n": cfg.get("search_top_n", 5),
    }


def get_knowledge_base_config() -> dict:
    """返回知识库配置（数据目录、ChromaDB 持久化目录、collection 名称等）。"""
    cfg = get_config().get("knowledge_base", {})
    return {
        "data_directory": cfg.get("data_directory", "data/knowledge_base"),
        "chroma_persist_dir": cfg.get("chroma_persist_dir", "data/chroma_db"),
        "collection_name": cfg.get("collection_name", "interview_knowledge"),
        "chunk_size": cfg.get("chunk_size", 512),
        "chunk_overlap": cfg.get("chunk_overlap", 64),
        "search_top_k": cfg.get("search_top_k", 10),
        "rerank_top_n": cfg.get("rerank_top_n", 5),
    }


def get_template_config() -> dict:
    """返回模板路径配置（default_md, default_tex）。"""
    return get_config().get("templates", {})


def get_resume_config() -> dict:
    """返回简历生成配置（max_internships, max_projects）。"""
    cfg = get_config().get("resume", {})
    return {
        "max_internships": cfg.get("max_internships", 1),
        "max_projects": cfg.get("max_projects", 1),
    }


def get_output_config() -> dict:
    """返回输出目录配置（directory）。"""
    return get_config().get("output", {})


def get_testing_config() -> dict:
    """返回测试相关配置。"""
    cfg = get_config().get("testing", {})
    integration = cfg.get("integration", {})
    return {
        "integration": {
            "run_real_llm_tests": bool(integration.get("run_real_llm_tests", False)),
        }
    }


def should_run_real_llm_integration_tests() -> bool:
    """返回是否启用真实 LLM integration 测试。"""
    return get_testing_config()["integration"]["run_real_llm_tests"]


def get_boss_config() -> dict:
    """返回 BOSS 直聘自动化配置。"""
    cfg = get_config().get("boss", {})
    return {
        "enabled": cfg.get("enabled", True),
        "headless": cfg.get("headless", False),
        "daily_apply_limit": cfg.get("daily_apply_limit", 15),
        "auto_reply_enabled": cfg.get("auto_reply_enabled", False),
        "min_reply_delay_sec": cfg.get("min_reply_delay_sec", 15),
        "max_reply_delay_sec": cfg.get("max_reply_delay_sec", 20),
        "batch_delay_min_sec": cfg.get("batch_delay_min_sec", 30),
        "batch_delay_max_sec": cfg.get("batch_delay_max_sec", 90),
        "ai_reply_style": cfg.get("ai_reply_style", "professional"),
        "greeting_mode": cfg.get("greeting_mode", "template"),
        "default_city": cfg.get("default_city", "全国"),
    }


def get_boss_agent_config() -> dict:
    """返回 BOSS Agent（Plan-then-Execute Harness）配置。"""
    cfg = get_config().get("boss", {}).get("agent", {})
    return {
        "enabled": cfg.get("enabled", True),
        "max_steps": cfg.get("max_steps", 12),
        "max_replans": cfg.get("max_replans", 2),
        "auto_execute": cfg.get("auto_execute", True),
        "inter_step_delay_min": cfg.get("inter_step_delay_min", 2.0),
        "inter_step_delay_max": cfg.get("inter_step_delay_max", 6.0),
        "smart_scan_top_n": cfg.get("smart_scan_top_n", 5),
        "smart_apply_min_score": cfg.get("smart_apply_min_score", 60),
    }


def get_boss_workflow_bridge_config() -> dict:
    """返回 Boss → 主工作流桥接配置。"""
    cfg = get_config().get("boss", {}).get("workflow_bridge", {})
    return {
        "enabled": cfg.get("enabled", True),
        "auto_parse_jd": cfg.get("auto_parse_jd", True),
    }
