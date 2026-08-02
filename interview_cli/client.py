"""HTTP client for Interview Assistant FastAPI backend.

所有 CLI 命令通过 httpx 调用后端 REST API，架构四层分离：
  cli.py (命令定义) → client.py (HTTP 客户端) → output.py (JSON 信封) → schema.json (Agent 描述)
"""

import os
from typing import Optional
import httpx

BASE_URL = os.environ.get("INTERVIEW_API", "http://127.0.0.1:8000")


def _post(path: str, json=None, timeout=120):
    """发送 POST 请求。连接失败返回 503 伪响应。"""
    try:
        resp = httpx.post(f"{BASE_URL}{path}", json=json, timeout=timeout)
    except httpx.ConnectError:
        resp = httpx.Response(
            503,
            text="Cannot connect to Interview Assistant server. "
                 "Run `interview server --start` first.",
        )
    return resp


def _get(path: str, params=None, timeout=30):
    """发送 GET 请求。连接失败返回 503 伪响应。"""
    try:
        resp = httpx.get(f"{BASE_URL}{path}", params=params, timeout=timeout)
    except httpx.ConnectError:
        resp = httpx.Response(
            503,
            text="Cannot connect to Interview Assistant server. "
                 "Run `interview server --start` first.",
        )
    return resp


def _put(path: str, json=None, timeout=30):
    """发送 PUT 请求。连接失败返回 503 伪响应。"""
    try:
        resp = httpx.put(f"{BASE_URL}{path}", json=json, timeout=timeout)
    except httpx.ConnectError:
        resp = httpx.Response(
            503,
            text="Cannot connect to Interview Assistant server. "
                 "Run `interview server --start` first.",
        )
    return resp


# ── 系统管理 ──

def health():
    """GET /health — 健康检查。"""
    return _get("/health")


# ── 核心对话 ──

def chat(message: str, session_id: str = "", attachments: Optional[list] = None):
    """POST /api/chat — 主对话接口。"""
    payload = {"message": message, "session_id": session_id}
    if attachments:
        payload["attachments"] = attachments
    return _post("/api/chat", json=payload, timeout=180)


# ── 简历 ──

def resume_content(session_id: str):
    """GET /api/resume/content — 获取简历内容 JSON。"""
    return _get("/api/resume/content", params={"session_id": session_id})


def resume_html(session_id: str):
    """GET /api/resume/html — 获取简历 HTML。"""
    return _get("/api/resume/html", params={"session_id": session_id})


def resume_preview(session_id: str):
    """GET /api/resume/preview — 获取简历 HTML 预览。"""
    return _get("/api/resume/preview", params={"session_id": session_id})


def resume_render(session_id: str, render_instruction: str):
    """POST /api/resume/render — 渲染指令接口。"""
    payload = {"session_id": session_id, "render_instruction": render_instruction}
    return _post("/api/resume/render", json=payload, timeout=180)


# ── 导出 ──

def export(session_id: str, target: str = "resume", format: str = "html"):
    """POST /api/export — 导出简历/岗位/缺口/面试问答。"""
    payload = {"session_id": session_id, "target": target, "format": format}
    return _post("/api/export", json=payload, timeout=60)


# ── 知识库 ──

def knowledge_stats():
    """GET /api/knowledge/stats — 知识库统计（如后端支持）。"""
    return _get("/api/knowledge/stats")


# ── 岗位搜索 ──

def search_jobs(keyword: str, city: str = "", limit: int = 10):
    """POST /api/jobs/search — 岗位搜索（如后端支持）。"""
    payload = {"keyword": keyword, "city": city, "limit": limit}
    return _post("/api/jobs/search", json=payload, timeout=60)


# ── BOSS 直聘自动化 ──

def boss_health():
    """GET /api/boss/health — Boss 模块健康检查。"""
    return _get("/api/boss/health")


def boss_status():
    """GET /api/boss/status — Boss 自动化状态。"""
    return _get("/api/boss/status")


def boss_start():
    """POST /api/boss/system/start — 启动浏览器自动化。"""
    return _post("/api/boss/system/start", timeout=120)


def boss_stop():
    """POST /api/boss/system/stop — 停止浏览器自动化。"""
    return _post("/api/boss/system/stop", timeout=60)


def boss_relogin():
    """POST /api/boss/system/relogin — 重新扫码登录。"""
    return _post("/api/boss/system/relogin", timeout=600)


def boss_heartbeat():
    """POST /api/boss/system/heartbeat — 心跳保活。"""
    return _post("/api/boss/system/heartbeat")


def boss_pause_monitor():
    """POST /api/boss/monitor/pause — 暂停自动回复。"""
    return _post("/api/boss/monitor/pause")


def boss_resume_monitor():
    """POST /api/boss/monitor/resume — 恢复自动回复。"""
    return _post("/api/boss/monitor/resume")


def boss_search_jobs(keyword: str, city: str = "", welfare: str = "", limit: int = 60):
    """POST /api/boss/jobs/search — 搜索 BOSS 直聘岗位。"""
    payload = {"keyword": keyword, "city": city, "welfare": welfare, "limit": limit}
    return _post("/api/boss/jobs/search", json=payload, timeout=120)


def boss_list_jobs(status: str = "", limit: int = 100):
    """GET /api/boss/jobs — 列出已保存的岗位。"""
    return _get("/api/boss/jobs", params={"status": status, "limit": limit})


def boss_apply(job_url: str, greeting: str = ""):
    """POST /api/boss/jobs/apply — 投递单个岗位。"""
    payload = {"job_url": job_url}
    if greeting:
        payload["greeting"] = greeting
    return _post("/api/boss/jobs/apply", json=payload, timeout=180)


def boss_apply_batch(job_urls: list, greeting: str = ""):
    """POST /api/boss/jobs/apply-batch — 批量投递。"""
    payload = {"job_urls": job_urls}
    if greeting:
        payload["greeting"] = greeting
    return _post("/api/boss/jobs/apply-batch", json=payload, timeout=600)


def boss_scan_and_apply(greeting: str = ""):
    """POST /api/boss/jobs/scan-and-apply — 扫描当前页并批量投递。"""
    payload = {}
    if greeting:
        payload["greeting"] = greeting
    return _post("/api/boss/jobs/scan-and-apply", json=payload, timeout=600)


def boss_scan_page():
    """POST /api/boss/jobs/scan — 仅扫描当前页岗位。"""
    return _post("/api/boss/jobs/scan", timeout=120)


def boss_analyze(job_url: str, job_title: str = "", company: str = "", description: str = ""):
    """POST /api/boss/jobs/analyze — AI 分析岗位匹配度。"""
    payload = {"job_url": job_url, "job_title": job_title, "company": company, "description": description}
    return _post("/api/boss/jobs/analyze", json=payload, timeout=120)


def boss_conversations():
    """GET /api/boss/conversations — 列出活跃会话。"""
    return _get("/api/boss/conversations")


def boss_conversation_messages(conv_id: int, limit: int = 50):
    """GET /api/boss/conversations/{conv_id}/messages — 获取会话消息。"""
    return _get(f"/api/boss/conversations/{conv_id}/messages", params={"limit": limit})


def boss_send_message(conv_id: int, content: str):
    """POST /api/boss/conversations/{conv_id}/send — 手动发送消息。"""
    return _post(f"/api/boss/conversations/{conv_id}/send", json={"content": content}, timeout=60)


def boss_stats():
    """GET /api/boss/stats — 获取统计数据。"""
    return _get("/api/boss/stats")


def boss_get_settings():
    """GET /api/boss/settings — 获取 Boss 设置。"""
    return _get("/api/boss/settings")


def boss_update_settings(**kwargs):
    """PUT /api/boss/settings — 更新 Boss 设置。"""
    return _put("/api/boss/settings", json=kwargs)


def boss_shortlists():
    """GET /api/boss/shortlists — 获取候选池。"""
    return _get("/api/boss/shortlists")
