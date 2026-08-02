"""JSON envelope formatter — stdout always structured JSON.

所有 CLI 命令统一输出 JSON 信封格式，对 AI Agent 友好：
- stdout 仅输出 JSON 数据
- stderr 输出日志和进度
- ok/fail 通过 envelope 的 ok 字段区分
"""

import json
import sys

# Windows 默认 GBK 控制台可能无法编码部分 Unicode 字符。
# 强制 stdout/stderr 切换到 UTF-8 + backslashreplace 回退。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    except Exception:
        pass


def ok(command: str, data=None, **kwargs) -> dict:
    """构建成功信封。"""
    envelope = {
        "ok": True,
        "command": command,
        "data": data,
        "error": None,
    }
    envelope.update(kwargs)
    return envelope


def fail(command: str, error: str) -> dict:
    """构建失败信封。"""
    return {
        "ok": False,
        "command": command,
        "data": None,
        "error": error,
    }


def emit(result: dict):
    """将信封 JSON 输出到 stdout。"""
    try:
        json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
    except UnicodeEncodeError:
        json.dump(result, sys.stdout, ensure_ascii=True, indent=2)
    sys.stdout.write("\n")


def ok_or_fail(resp, command: str) -> dict:
    """将 httpx 响应转换为信封。处理 HTTP 错误和 JSON 解析异常。"""
    if resp.is_error:
        return fail(command, f"HTTP {resp.status_code}: {resp.text[:200]}")
    try:
        body = resp.json()
    except Exception:
        return fail(command, f"invalid JSON: {resp.text[:200]}")
    if isinstance(body, dict) and body.get("detail"):
        return fail(command, str(body["detail"]))
    return ok(command, data=body)
