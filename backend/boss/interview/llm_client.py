"""LLM 适配器 —— 桥接 boos-ai 的 LLM 调用到面试助手的配置体系。

boos-ai 原始代码通过 sys.path 找到本文件，调用 llm_chat_deepseek() 和 get_embedding()。
本适配器将这两个函数的实现替换为使用面试助手的 config_loader 配置（config.yaml + .env），
而非 boos-ai 原始的 SQLite settings 表。

这样做的优点：
1. LLM 配置统一管理 —— 与面试助手其他 Agent 使用相同的 API Key / Model / Base URL
2. 无需在两个地方分别配置 LLM
3. boos-ai 核心文件（boss_replier.py, boss_rag.py 等）无需任何修改
"""

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import List, Optional

import httpx

# ── 确保能导入面试助手的 config_loader ──
# backend/ 目录通常已在 sys.path 中（uvicorn app_dir），
# 但在独立运行 boss 模块时也做一次保险
_BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

try:
    from config_loader import get_llm_config, get_embedding_config
except ImportError:
    # 如果 config_loader 不可用，提供降级实现
    get_llm_config = None
    get_embedding_config = None


# ── Ollama 配置（用于转人工分类的本地小模型，可选）──
OLLAMA_BASE = os.environ.get("OLLAMA_BASE", "http://localhost:11434")


def _load_ai_config() -> dict:
    """从面试助手的 config_loader 加载 LLM 配置。

    优先使用 config_loader（config.yaml + .env），
    如果不可用则回退到环境变量。
    """
    cfg = {
        "api_key": "",
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-chat",
        "debug_llm_context": False,
    }
    if get_llm_config is not None:
        try:
            llm_cfg = get_llm_config()
            cfg["api_key"] = llm_cfg.get("api_key", "")
            cfg["base_url"] = llm_cfg.get("api_base", "https://api.deepseek.com")
            cfg["model"] = llm_cfg.get("model", "deepseek-chat")
        except Exception:
            pass

    # 环境变量兜底
    if not cfg["api_key"]:
        cfg["api_key"] = os.environ.get("DEEPSEEK_API_KEY", "")
    if not cfg["base_url"]:
        cfg["base_url"] = os.environ.get("LLM_BASE_URL", "https://api.deepseek.com")
    if not cfg["model"]:
        cfg["model"] = os.environ.get("LLM_MODEL", "deepseek-chat")

    return cfg


def get_embedding(text: str) -> List[float]:
    """获取文本的 embedding 向量。

    优先使用面试助手的 DashScope embedding 配置，
    如果不可用则回退到 Ollama（boos-ai 原始行为）。
    """
    t0 = time.time()
    preview = text[:60].replace("\n", " ")
    print(f"[{datetime.now().strftime('%H:%M:%S')}] [Boss-RAG] embedding (text_len={len(text)}, preview=\"{preview}...\")")

    # 方案1: 使用面试助手的 embedding 配置（DashScope / OpenAI 兼容）
    if get_embedding_config is not None:
        try:
            emb_cfg = get_embedding_config()
            provider = (emb_cfg.get("provider") or "dashscope").lower()

            if provider in ("dashscope", "tongyi"):
                # DashScope embedding API
                resp = httpx.post(
                    f"{emb_cfg.get('api_base', 'https://dashscope.aliyuncs.com/api/v1')}/services/embeddings/text-embedding/text-embedding",
                    json={
                        "model": emb_cfg["model"],
                        "input": {"texts": [text]},
                    },
                    headers={
                        "Authorization": f"Bearer {emb_cfg.get('api_key', '')}",
                        "Content-Type": "application/json",
                    },
                    timeout=30,
                )
                resp.raise_for_status()
                data = resp.json()
                vec = data["output"]["embeddings"][0]["embedding"]
                elapsed = time.time() - t0
                print(f"[{datetime.now().strftime('%H:%M:%S')}] [Boss-RAG] DashScope embedding done ({elapsed*1000:.0f}ms)")
                return vec

            elif provider in ("openai", "deepseek", "qwen", "openai_compatible"):
                # OpenAI 兼容 embedding API
                resp = httpx.post(
                    f"{emb_cfg.get('api_base', '')}/embeddings",
                    json={
                        "model": emb_cfg["model"],
                        "input": text,
                    },
                    headers={
                        "Authorization": f"Bearer {emb_cfg.get('api_key', '')}",
                        "Content-Type": "application/json",
                    },
                    timeout=30,
                )
                resp.raise_for_status()
                data = resp.json()
                vec = data["data"][0]["embedding"]
                elapsed = time.time() - t0
                print(f"[{datetime.now().strftime('%H:%M:%S')}] [Boss-RAG] OpenAI embedding done ({elapsed*1000:.0f}ms)")
                return vec

        except Exception as e:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] [Boss-RAG] Primary embedding failed: {e}, trying Ollama fallback...")

    # 方案2: 回退到 Ollama（boos-ai 原始行为）
    try:
        resp = httpx.post(
            f"{OLLAMA_BASE}/api/embed",
            json={"model": "nomic-embed-text", "input": text},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        vec = data["embeddings"][0]
        elapsed = time.time() - t0
        print(f"[{datetime.now().strftime('%H:%M:%S')}] [Boss-RAG] Ollama embedding done ({elapsed*1000:.0f}ms)")
        return vec
    except Exception as e:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] [Boss-RAG] All embedding methods failed: {e}")
        raise


def cosine_similarity(vec_a: List[float], vec_b: List[float]) -> float:
    """计算余弦相似度"""
    import numpy as np
    a = np.array(vec_a, dtype=np.float32)
    b = np.array(vec_b, dtype=np.float32)
    if np.linalg.norm(a) == 0 or np.linalg.norm(b) == 0:
        return 0.0
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))


def llm_chat_deepseek(messages: list, system_prompt: Optional[str] = None, temperature: float = 0.3) -> str:
    """调用 AI API（从面试助手 config_loader 读取配置）。

    与 boos-ai 原始函数签名完全一致，确保 boss_replier.py 无需修改。
    """
    t0 = time.time()
    cfg = _load_ai_config()
    if not cfg["api_key"]:
        raise RuntimeError("AI API Key 未配置。请在 backend/.env 中设置 DEEPSEEK_API_KEY 或在 config.yaml 中配置 llm.api_key_env")

    if system_prompt:
        messages = [{"role": "system", "content": system_prompt}] + messages

    msg_count = len(messages)
    print(f"[{datetime.now().strftime('%H:%M:%S')}] [Boss-LLM] model={cfg['model']}, url={cfg['base_url']}, messages={msg_count}, temp={temperature}")

    payload = {
        "model": cfg["model"],
        "messages": messages,
        "temperature": temperature,
        "stream": False,
    }

    _api_url = f"{cfg['base_url']}/chat/completions"
    _timeout = httpx.Timeout(120.0, connect=15.0)

    try:
        resp = httpx.post(
            _api_url,
            json=payload,
            headers={
                "Authorization": f"Bearer {cfg['api_key']}",
                "Content-Type": "application/json",
            },
            timeout=_timeout,
            verify=False,
        )
        resp.raise_for_status()
    except httpx.ConnectTimeout:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] [Boss-LLM] Connection timeout to {_api_url}")
        raise
    except httpx.ReadTimeout:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] [Boss-LLM] Read timeout (120s)")
        raise

    data = resp.json()
    elapsed = time.time() - t0
    result = data["choices"][0]["message"]["content"]
    print(f"[{datetime.now().strftime('%H:%M:%S')}] [Boss-LLM] Response ({len(result)} chars, {elapsed*1000:.0f}ms)")
    return result


def parse_json_from_llm(text: str) -> Optional[dict]:
    """从 LLM 返回文本中提取 JSON"""
    import re
    json_match = re.search(r"\{.*\}", text, re.DOTALL)
    if json_match:
        try:
            return json.loads(json_match.group())
        except json.JSONDecodeError:
            pass
    return None


def llm_chat_ollama(messages: list, system_prompt: Optional[str] = None, temperature: float = 0.7) -> str:
    """调用 Ollama 大模型（保留兼容性，用于本地小模型任务）"""
    if system_prompt:
        messages = [{"role": "system", "content": system_prompt}] + messages

    payload = {
        "model": "qwen2.5:14b",
        "messages": messages,
        "temperature": temperature,
        "stream": False,
    }

    resp = httpx.post(f"{OLLAMA_BASE}/api/chat", json=payload, timeout=120)
    resp.raise_for_status()
    data = resp.json()
    return data["message"]["content"]
