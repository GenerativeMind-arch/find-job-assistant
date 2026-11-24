"""Embedding 统一封装：基于 config.yaml 的 provider 动态创建 Embeddings。"""

import asyncio
from typing import Any

from config_loader import get_embedding_config


# 模块级单例：对齐 models/llm.py 的 get_llm()，避免每次检索都重建客户端
_embedding_instance: Any | None = None


def _normalize_provider(provider: str) -> str:
    return (provider or "dashscope").strip().lower()


def _create_dashscope_embedding(cfg: dict) -> Any:
    from langchain_community.embeddings import DashScopeEmbeddings

    kwargs = {"model": cfg["model"]}
    if cfg.get("api_key"):
        kwargs["dashscope_api_key"] = cfg["api_key"]
    return DashScopeEmbeddings(**kwargs)


def _create_openai_embedding(cfg: dict) -> Any:
    from langchain_openai import OpenAIEmbeddings

    kwargs: dict[str, Any] = {"model": cfg["model"]}
    if cfg.get("api_key"):
        kwargs["openai_api_key"] = cfg["api_key"]
    if cfg.get("api_base"):
        kwargs["openai_api_base"] = cfg["api_base"]
    if cfg.get("dimensions"):
        kwargs["dimensions"] = cfg["dimensions"]

    model_kwargs = cfg.get("model_kwargs") or {}
    if model_kwargs:
        kwargs["model_kwargs"] = model_kwargs

    return OpenAIEmbeddings(**kwargs)


def _create_azure_openai_embedding(cfg: dict) -> Any:
    from langchain_openai import AzureOpenAIEmbeddings

    kwargs: dict[str, Any] = {
        "model": cfg["model"],
        "azure_deployment": cfg.get("deployment") or cfg["model"],
        "api_version": cfg.get("api_version") or "2024-02-15-preview",
    }
    if cfg.get("api_base"):
        kwargs["azure_endpoint"] = cfg["api_base"]
    if cfg.get("api_key"):
        kwargs["api_key"] = cfg["api_key"]
    if cfg.get("dimensions"):
        kwargs["dimensions"] = cfg["dimensions"]

    return AzureOpenAIEmbeddings(**kwargs)


def _create_ollama_embedding(cfg: dict) -> Any:
    try:
        from langchain_ollama import OllamaEmbeddings
    except ImportError:
        from langchain_community.embeddings import OllamaEmbeddings

    kwargs = {"model": cfg["model"]}
    if cfg.get("api_base"):
        kwargs["base_url"] = cfg["api_base"]

    return OllamaEmbeddings(**kwargs)


def _create_huggingface_embedding(cfg: dict) -> Any:
    from langchain_community.embeddings import HuggingFaceEmbeddings

    kwargs: dict[str, Any] = {"model_name": cfg["model"]}
    model_kwargs = cfg.get("model_kwargs") or {}
    if model_kwargs:
        kwargs["model_kwargs"] = model_kwargs

    return HuggingFaceEmbeddings(**kwargs)


def get_embedding_model() -> Any:
    """根据 embedding.provider 返回对应 Embedding 实例（进程内缓存复用）。"""
    global _embedding_instance
    if _embedding_instance is not None:
        return _embedding_instance

    cfg = get_embedding_config()
    provider = _normalize_provider(cfg.get("provider", "dashscope"))

    if provider in {"dashscope", "tongyi"}:
        _embedding_instance = _create_dashscope_embedding(cfg)
        return _embedding_instance
    if provider in {"openai", "deepseek", "qwen", "openai_compatible"}:
        _embedding_instance = _create_openai_embedding(cfg)
        return _embedding_instance
    if provider in {"azure_openai", "azure"}:
        _embedding_instance = _create_azure_openai_embedding(cfg)
        return _embedding_instance
    if provider in {"ollama", "local"}:
        _embedding_instance = _create_ollama_embedding(cfg)
        return _embedding_instance
    if provider in {"huggingface", "hf", "sentence_transformers"}:
        _embedding_instance = _create_huggingface_embedding(cfg)
        return _embedding_instance

    raise ValueError(
        "Unsupported embedding.provider='{}'. Supported providers: dashscope, openai, "
        "deepseek, qwen, azure_openai, ollama, huggingface".format(provider)
    )


async def aembed_query(text: str) -> list[float]:
    """异步获取单条文本的 embedding 向量。"""
    model = get_embedding_model()
    if hasattr(model, "aembed_query"):
        return await model.aembed_query(text)
    return await asyncio.to_thread(model.embed_query, text)


async def aembed_documents(texts: list[str]) -> list[list[float]]:
    """异步获取多条文本的 embedding 向量。"""
    model = get_embedding_model()
    if hasattr(model, "aembed_documents"):
        return await model.aembed_documents(texts)
    return await asyncio.to_thread(model.embed_documents, texts)
