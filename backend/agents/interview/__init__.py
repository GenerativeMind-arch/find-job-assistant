"""模拟面试模块 — 交互式一问一答 + Judge 评分。

核心组件:
  - engine.py:    InterviewEngine（start → chat 多轮 → end）
  - judge.py:     JudgeAgent（四维度评分）
  - audio.py:     语音转文字（DashScope qwen-audio-turbo）
  - contracts.py: Pydantic 数据模型
  - prompts.py:   出题/面试官/Judge/总结 Prompt
"""

from .engine import InterviewEngine
from .judge import JudgeAgent
from .audio import transcribe_audio

__all__ = ["InterviewEngine", "JudgeAgent", "transcribe_audio"]
