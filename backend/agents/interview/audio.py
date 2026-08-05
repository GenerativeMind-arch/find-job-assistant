"""语音转文字 — 调用阿里云 DashScope qwen-audio-3.0-realtime-plus。

候选人在模拟面试中通过语音作答，前端录音后 base64 编码上传，
后端调用 qwen-audio-3.0-realtime-plus 将音频转为文字，作为回答文本交给 Judge Agent。
"""

from __future__ import annotations

import base64
import binascii

import httpx

from config_loader import get_embedding_config
from log import get_logger

logger = get_logger("agent")

# qwen-audio-3.0-realtime-plus 多模态生成端点
_AUDIO_API_URL = "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"


def _get_dashscope_api_key() -> str:
    """从配置读取 DashScope API Key。"""
    try:
        cfg = get_embedding_config()
        key = cfg.get("api_key") or ""
        if key:
            return key
    except Exception:
        pass
    import os
    return os.environ.get("DASHSCOPE_API_KEY", "")


async def transcribe_audio(audio_base64: str, timeout: float = 30.0) -> str:
    """将 base64 编码的音频转为文字。

    Args:
        audio_base64: base64 编码的音频数据（WAV/PCM/MP3 等）
        timeout: 请求超时（秒）

    Returns:
        转写后的文本

    Raises:
        ValueError: 音频数据无效或转写失败
    """
    if not audio_base64 or not audio_base64.strip():
        raise ValueError("audio_base64 为空")

    api_key = _get_dashscope_api_key()
    if not api_key:
        raise ValueError("DashScope API Key 未配置，无法进行语音转文字")

    # 清理 data URL 前缀（如 "data:audio/wav;base64,...."）
    if "," in audio_base64 and audio_base64.strip().startswith("data:"):
        audio_base64 = audio_base64.split(",", 1)[1]

    # 解码校验
    try:
        audio_bytes = base64.b64decode(audio_base64)
    except (binascii.Error, ValueError) as exc:
        raise ValueError(f"音频 base64 解码失败: {exc}")

    if not audio_bytes:
        raise ValueError("音频数据为空")

    # 构造多模态请求
    # 音频以 URL 形式传给 qwen-audio-3.0-realtime-plus，因此需先转 data URI
    data_uri = "data:audio/wav;base64," + base64.b64encode(audio_bytes).decode()

    payload = {
        "model": "qwen-audio-3.0-realtime-plus",
        "input": {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"audio": data_uri},
                        {"text": "请将这段语音内容完整转写为文字，只输出转写文本，不要添加任何解释。"},
                    ],
                }
            ]
        },
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "X-DashScope-DataInspection": "enable",
    }

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(_AUDIO_API_URL, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPStatusError as exc:
        logger.error("qwen-audio-3.0-realtime-plus HTTP %s: %s", exc.response.status_code, exc.response.text[:300])
        raise ValueError(f"语音转写失败（HTTP {exc.response.status_code}）")
    except httpx.HTTPError as exc:
        logger.error("qwen-audio-3.0-realtime-plus request failed: %s", exc)
        raise ValueError(f"语音转写请求失败: {exc}")

    # 解析返回：output.choices[0].message.content 可能是文本或含图片的列表
    try:
        output = data.get("output", {})
        choices = output.get("choices", [])
        if not choices:
            # 兼容非流式直出文本
            return str(output.get("text", "")).strip()

        content = choices[0].get("message", {}).get("content", [])
        if isinstance(content, str):
            return content.strip()
        # 内容为 list，提取所有 text 片段
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("text"):
                parts.append(item["text"])
        text = "".join(parts).strip()
        if not text:
            raise ValueError("语音转写结果为空")
        return text
    except Exception as exc:
        logger.error("qwen-audio-3.0-realtime-plus response parse failed: %s", exc)
        raise ValueError(f"语音转写结果解析失败: {exc}")
