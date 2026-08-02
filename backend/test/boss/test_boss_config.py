"""Boss 模块配置集成测试。

验证 config_loader 的 get_boss_config() 正确读取 config.yaml 中的 boss 配置段，
以及 llm_client 适配器正确桥接到面试助手的 LLM 配置。
"""

import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[2]
BOSS_DIR = BACKEND_DIR / "boss"
for p in [str(BACKEND_DIR), str(BOSS_DIR), str(BOSS_DIR / "interview")]:
    if p not in sys.path:
        sys.path.insert(0, p)

from config_loader import get_boss_config, get_llm_config, get_embedding_config


class TestBossConfig:
    """测试 config_loader 的 boss 配置读取。"""

    def test_get_boss_config_returns_dict(self):
        """get_boss_config 应返回字典。"""
        cfg = get_boss_config()
        assert isinstance(cfg, dict)

    def test_boss_config_has_required_keys(self):
        """boss 配置应包含所有必需的 key。"""
        cfg = get_boss_config()
        required_keys = [
            "enabled",
            "headless",
            "daily_apply_limit",
            "auto_reply_enabled",
            "min_reply_delay_sec",
            "max_reply_delay_sec",
            "ai_reply_style",
            "greeting_mode",
            "default_city",
        ]
        for key in required_keys:
            assert key in cfg, f"Missing required boss config key: {key}"

    def test_boss_config_types(self):
        """boss 配置值的类型应正确。"""
        cfg = get_boss_config()
        assert isinstance(cfg.get("enabled"), bool)
        assert isinstance(cfg.get("headless"), bool)
        assert isinstance(cfg.get("daily_apply_limit"), int)
        assert isinstance(cfg.get("auto_reply_enabled"), bool)
        assert isinstance(cfg.get("min_reply_delay_sec"), int)
        assert isinstance(cfg.get("max_reply_delay_sec"), int)
        assert isinstance(cfg.get("ai_reply_style"), str)
        assert isinstance(cfg.get("greeting_mode"), str)
        assert isinstance(cfg.get("default_city"), str)

    def test_boss_config_values_reasonable(self):
        """boss 配置值应在合理范围内。"""
        cfg = get_boss_config()
        assert cfg["daily_apply_limit"] > 0
        assert cfg["daily_apply_limit"] <= 200
        assert cfg["min_reply_delay_sec"] >= 5
        assert cfg["max_reply_delay_sec"] >= cfg["min_reply_delay_sec"]
        assert cfg["ai_reply_style"] in ("professional", "casual", "enthusiastic")
        assert cfg["greeting_mode"] in ("template", "smart")


class TestLLMClientAdapter:
    """测试 LLM 适配器正确桥接到面试助手配置。"""

    def test_llm_client_imports(self):
        """llm_client 模块应能正常导入。"""
        from llm_client import _load_ai_config, llm_chat_deepseek, get_embedding, cosine_similarity
        assert callable(_load_ai_config)
        assert callable(llm_chat_deepseek)
        assert callable(get_embedding)
        assert callable(cosine_similarity)

    def test_load_ai_config_returns_dict(self):
        """_load_ai_config 应返回包含必需 key 的字典。"""
        from llm_client import _load_ai_config
        cfg = _load_ai_config()
        assert isinstance(cfg, dict)
        assert "api_key" in cfg
        assert "base_url" in cfg
        assert "model" in cfg

    def test_load_ai_config_uses_config_loader(self):
        """_load_ai_config 应从 config_loader 读取配置，而非 SQLite。"""
        from llm_client import _load_ai_config
        cfg = _load_ai_config()
        llm_cfg = get_llm_config()
        # model 应与 config_loader 一致
        assert cfg["model"] == llm_cfg.get("model", "deepseek-chat")
        # base_url 应与 config_loader 一致
        assert cfg["base_url"] == llm_cfg.get("api_base", "https://api.deepseek.com")

    def test_cosine_similarity(self):
        """cosine_similarity 应正确计算余弦相似度。"""
        from llm_client import cosine_similarity
        # 相同向量应相似度为 1
        vec = [1.0, 0.0, 0.0]
        sim = cosine_similarity(vec, vec)
        assert abs(sim - 1.0) < 0.001
        # 正交向量应相似度为 0
        sim2 = cosine_similarity([1.0, 0.0], [0.0, 1.0])
        assert abs(sim2) < 0.001

    def test_parse_json_from_llm(self):
        """parse_json_from_llm 应从文本中提取 JSON。"""
        from llm_client import parse_json_from_llm
        # 正常 JSON
        result = parse_json_from_llm('some text {"key": "value"} more text')
        assert result == {"key": "value"}
        # 无 JSON
        assert parse_json_from_llm("no json here") is None
        # 多层嵌套
        result2 = parse_json_from_llm('{"a": {"b": 1}}')
        assert result2 == {"a": {"b": 1}}
