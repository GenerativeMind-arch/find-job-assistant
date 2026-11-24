"""boss_replier 的 AI 身份标记测试。

核心不变量：**每一条非空自动回复都必须以 [AI助手回复] 结尾**，
包括绕过 LLM 的硬编码分支；而招呼语与手动发送不得带标记。
LLM 调用一律 monkeypatch，测试不触网。
"""

import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[2]
BOSS_DIR = BACKEND_DIR / "boss"
for p in [str(BACKEND_DIR), str(BOSS_DIR)]:
    if p not in sys.path:
        sys.path.insert(0, p)

import boss_replier
from boss_replier import (
    AI_REPLY_MARKER,
    _MAX_REPLY_LEN,
    _append_ai_marker,
    generate_greeting,
    generate_reply,
)

JOB = {
    "title": "Agent 应用开发工程师",
    "company": "某公司",
    "description": "负责 RAG 检索与 Agent 工具调用链路",
    "city": "北京",
}


class TestAppendAiMarker:
    """标记追加函数的纯逻辑。"""

    def test_appends_marker_on_new_line(self):
        assert _append_ai_marker("你好") == f"你好\n{AI_REPLY_MARKER}"

    def test_is_idempotent(self):
        once = _append_ai_marker("你好")
        assert _append_ai_marker(once) == once

    def test_empty_reply_untouched(self):
        assert _append_ai_marker("") == ""

    def test_trailing_whitespace_collapsed(self):
        """正文尾部空白要清掉，标记才紧跟在最后一行。"""
        assert _append_ai_marker("你好   \n\n") == f"你好\n{AI_REPLY_MARKER}"

    def test_total_length_respects_cap(self):
        out = _append_ai_marker("好" * 1000)
        assert len(out) <= _MAX_REPLY_LEN
        assert out.endswith(AI_REPLY_MARKER)


class TestGenerateReplyHasMarker:
    """所有自动回复路径都必须带标记。"""

    def test_trivial_greeting_branch(self):
        """「你好」走硬编码分支，不调 LLM，也必须带标记。"""
        result = generate_reply(1, "你好", JOB)
        assert result["reply"]
        assert result["reply"].endswith(AI_REPLY_MARKER)

    def test_human_transfer_branch(self, monkeypatch):
        """转人工分支同样绕过 LLM，也必须带标记。"""
        monkeypatch.setattr(boss_replier, "_needs_human_transfer", lambda *a, **k: True)
        result = generate_reply(1, "我想跟真人聊", JOB)
        assert result["transfer"] is True
        assert result["reply"].endswith(AI_REPLY_MARKER)

    def test_llm_path(self, monkeypatch):
        """正常 LLM 路径要带标记，且正文完整保留。"""
        monkeypatch.setattr(boss_replier, "_needs_human_transfer", lambda *a, **k: False)
        monkeypatch.setattr(boss_replier, "build_reply_context", lambda *a, **k: "上下文")
        monkeypatch.setattr(
            boss_replier,
            "llm_chat_deepseek",
            lambda *a, **k: '{"reply": "您好，我做过 RAG 检索链路。", "interest": "medium"}',
        )
        result = generate_reply(1, "你们做过 RAG 吗", JOB)
        assert result["interest"] == "medium"
        assert "RAG 检索链路" in result["reply"]
        assert result["reply"].endswith(AI_REPLY_MARKER)

    def test_marker_not_duplicated_when_model_emits_it(self, monkeypatch):
        """模型自己带了标记时不能重复追加。"""
        monkeypatch.setattr(boss_replier, "_needs_human_transfer", lambda *a, **k: False)
        monkeypatch.setattr(boss_replier, "build_reply_context", lambda *a, **k: "上下文")
        monkeypatch.setattr(
            boss_replier,
            "llm_chat_deepseek",
            lambda *a, **k: '{"reply": "正文\\n[AI助手回复]", "interest": "low"}',
        )
        result = generate_reply(1, "在吗", JOB)
        assert result["reply"].count(AI_REPLY_MARKER) == 1

    def test_empty_reply_stays_empty(self, monkeypatch):
        """拒绝回答 / 空回复分支不得凭空造出标记。"""
        monkeypatch.setattr(boss_replier, "_needs_human_transfer", lambda *a, **k: False)
        monkeypatch.setattr(boss_replier, "build_reply_context", lambda *a, **k: "上下文")
        monkeypatch.setattr(
            boss_replier,
            "llm_chat_deepseek",
            lambda *a, **k: '{"reply": "无法回答这个问题", "interest": ""}',
        )
        result = generate_reply(1, "你期望薪资多少", JOB)
        assert result["reply"] == ""


class TestPromptAndGreeting:
    """提示词与招呼语两端的边界。"""

    def test_system_prompt_mentions_marker(self):
        """提示词必须强制要求标记，这是双保险的前一半。"""
        assert AI_REPLY_MARKER in boss_replier.SYSTEM_PROMPT

    def test_greeting_has_no_marker(self):
        """招呼语走独立路径，不该被标记污染。"""
        out = generate_greeting("Agent 应用开发", "某公司")
        assert out
        assert AI_REPLY_MARKER not in out
