"""HTTP client for Interview Assistant FastAPI backend (BOSS 直聘 API)."""

import os
from typing import Optional
import httpx

BASE_URL = os.environ.get("INTERVIEW_BOSS_API", "http://127.0.0.1:8000")


def _post(path: str, json=None, timeout=120):
    try:
        resp = httpx.post(f"{BASE_URL}{path}", json=json, timeout=timeout)
    except httpx.ConnectError:
        resp = httpx.Response(503, text="Cannot connect to server. Start backend first (uvicorn main:app --port 8000).")
    return resp


def _get(path: str, timeout=30):
    try:
        resp = httpx.get(f"{BASE_URL}{path}", timeout=timeout)
    except httpx.ConnectError:
        resp = httpx.Response(503, text="Cannot connect to server. Start backend first (uvicorn main:app --port 8000).")
    return resp


def _delete(path: str, timeout=30):
    try:
        resp = httpx.delete(f"{BASE_URL}{path}", timeout=timeout)
    except httpx.ConnectError:
        resp = httpx.Response(503, text="Cannot connect to server.")
    return resp


# ── 系统控制 ──
def status():
    return _get("/api/boss/status")


def start():
    return _post("/api/boss/system/start", timeout=300)


def stop():
    return _post("/api/boss/system/stop", timeout=60)


def relogin():
    return _post("/api/boss/system/relogin", timeout=300)


def heartbeat():
    return _post("/api/boss/system/heartbeat")


def monitor_pause():
    return _post("/api/boss/monitor/pause")


def monitor_resume():
    return _post("/api/boss/monitor/resume")


# ── 搜索 & 投递 ──
def search(keyword: str, city: str = "", welfare: str = "", limit: int = 60):
    payload = {"keyword": keyword, "city": city or "", "limit": limit}
    if welfare:
        payload["welfare"] = welfare
    return _post("/api/boss/jobs/search", payload, timeout=180)


def jobs(status_filter=None, limit=100):
    q = f"?limit={limit}"
    if status_filter:
        q += f"&status={status_filter}"
    return _get(f"/api/boss/jobs{q}")


def apply_one(job_url: str):
    return _post("/api/boss/jobs/apply", {"job_url": job_url}, timeout=180)


def apply_batch(job_urls: list):
    return _post("/api/boss/jobs/apply-batch", {"job_urls": job_urls}, timeout=300)


def scan():
    return _post("/api/boss/jobs/scan", timeout=180)


def scan_and_apply():
    return _post("/api/boss/jobs/scan-and-apply", timeout=300)


def analyze(job_url: str, title: str = "", company: str = "", desc: str = ""):
    return _post("/api/boss/jobs/analyze", {"job_url": job_url, "job_title": title, "company": company, "description": desc})


# ── 会话 & 沟通 ──
def conversations():
    return _get("/api/boss/conversations")


def chat_messages(conv_id: int):
    return _get(f"/api/boss/conversations/{conv_id}/messages")


def send_message(conv_id: int, content: str):
    return _post(f"/api/boss/conversations/{conv_id}/send", {"content": content})


# ── 统计 & 设置 ──
def stats():
    return _get("/api/boss/stats")


def get_settings():
    return _get("/api/boss/settings")


def update_settings(updates: dict):
    return _put("/api/boss/settings", updates)


def _put(path: str, json=None, timeout=30):
    try:
        resp = httpx.put(f"{BASE_URL}{path}", json=json, timeout=timeout)
    except httpx.ConnectError:
        resp = httpx.Response(503, text="Cannot connect to server.")
    return resp


# ── 候选池 ──
def get_shortlists():
    return _get("/api/boss/shortlists")


def add_shortlist(job_url: str, title: str = "", company: str = "", salary: str = "", city: str = ""):
    return _post("/api/boss/shortlists", {"job_url": job_url, "title": title, "company": company, "salary": salary, "city": city})


def remove_shortlist(sid: int):
    return _delete(f"/api/boss/shortlists/{sid}")


# ── Agent Harness（可选高级功能）──
def agent_tools():
    return _get("/api/boss/agent/tools")


def agent_run(goal: str, max_steps: int = 12):
    return _post("/api/boss/agent/run", {"goal": goal, "max_steps": max_steps}, timeout=600)


# ── 工作流桥接 ──
def import_job(job_url: str = "", job_id: Optional[int] = None, session_id: str = ""):
    payload: dict = {"session_id": session_id}
    if job_url:
        payload["job_url"] = job_url
    if job_id is not None:
        payload["job_id"] = job_id
    return _post("/api/boss/import-job", payload)
