"""语音转文字 — 调用阿里云 DashScope qwen-audio-turbo。

候选人在模拟面试中通过语音作答，前端录音后 base64 编码上传，
后端调用 qwen-audio-turbo 将音频转为文字，作为回答文本交给 Judge Agent。

注意：qwen-audio-3.0-realtime-plus 走实时流式 API，不支持 HTTP 调用
（实测报 "current user api does not support http call"），故使用 qwen-audio-turbo。
"""

from __future__ import annotations

import base64
import binascii

import httpx

from config_loader import get_embedding_config
from log import get_logger

logger = get_logger("agent")

# qwen-audio-turbo 多模态生成端点
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
    # 音频以 URL 形式传给 qwen-audio-turbo，因此需先转 data URI
    data_uri = "data:audio/wav;base64," + base64.b64encode(audio_bytes).decode()

    payload = {
        "model": "qwen-audio-turbo",
        "input": {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"audio": data_uri},
                        {"text": "请直接转写这段语音中人物实际说出的原话。只输出原话本身，"
                                  "不要添加任何引导语、冒号、引号、书名号、解释或标点装饰。"},
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
        logger.error("qwen-audio-turbo HTTP %s: %s", exc.response.status_code, exc.response.text[:300])
        raise ValueError(f"语音转写失败（HTTP {exc.response.status_code}）")
    except httpx.HTTPError as exc:
        logger.error("qwen-audio-turbo request failed: %s", exc)
        raise ValueError(f"语音转写请求失败: {exc}")

    # 解析返回：output.choices[0].message.content 可能是文本或含图片的列表
    try:
        output = data.get("output", {})
        choices = output.get("choices", [])
        if not choices:
            # 兼容非流式直出文本
            return _clean_transcription(str(output.get("text", "")))

        content = choices[0].get("message", {}).get("content", [])
        if isinstance(content, str):
            return _clean_transcription(content)
        # 内容为 list，提取所有 text 片段
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("text"):
                parts.append(item["text"])
        text = "".join(parts).strip()
        if not text:
            raise ValueError("语音转写结果为空")
        return _clean_transcription(text)
    except Exception as exc:
        logger.error("qwen-audio-turbo response parse failed: %s", exc)
        raise ValueError(f"语音转写结果解析失败: {exc}")


def _clean_transcription(text: str) -> str:
    """清洗转写结果：只保留语音中人物实际说出的原话。

    qwen-audio-turbo 有时会输出引导语，如「这段语音的内容是是：'你好'」，
    这里提取引号内的内容并去掉引号；若无引号，则剥离常见前缀/后缀说明。
    """
    if not text:
        return ""
    t = text.strip()

    # 1) 优先提取引号（中文/英文）内的原话
    import re
    m = re.search(r"['\"“”‘’]([^'\"“”‘’]+)['\"“”‘’]", t)
    if m:
        return m.group(1).strip()

    # 2) 无引号：剥离常见引导前缀（依次尝试多种模式，直到前缀被去除）
    import re as _re
    prefix_patterns = [
        # "这段语音的内容是：" / "该段音频的转写结果是：" / "语音内容为："
        # 长模式（转写结果/识别结果）必须排在短模式（转写/识别）之前
        r"^(这段|该段|此段|这个)?(语音|音频|录音|说话)?的?(内容|话|原话|转写结果|识别结果|转写|识别)?(是|为|是是|就是|大意是|说的是|转写为|转写成|识别为)?\s*[:：]?\s*",
        # "我转写为：" / "我把它转写成了：" / "模型转写为："
        r"^(我|模型|AI)(把)?(这段)?(语音|音频)?(转写|识别)(为|成|的结果是)?\s*[:：]?\s*",
        # 兜底："就是：" / "结果为：" / "结果是：" / "意思是："
        r"^(就是|结果为|结果是|意思是|总结为|概括为)\s*[:：]?\s*",
    ]
    for pat in prefix_patterns:
        t = _re.sub(pat, "", t)
    # 3) 去掉尾部多余说明（"以上就是全部""这是转写结果"等）
    t = _re.sub(r"(以上就是|这是|本段)?(语音|音频)?(的)?(全部)?(内容|转写结果|原话)(。|\.)?$", "", t)
    return t.strip()
