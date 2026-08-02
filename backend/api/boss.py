"""Boss 直聘自动化 API 路由。

所有端点以 /api/boss 为前缀，与面试助手原有路由隔离。
通过 BossManager 单例管理浏览器自动化实例。

主要功能:
  - 系统控制: 启动/停止浏览器、重新登录、心跳保活
  - 岗位搜索: 搜索 BOSS 直聘岗位、扫描当前页
  - 自动投递: 单个投递、批量投递、扫描+投递
  - 自动沟通: 暂停/恢复监控、会话列表、消息同步、手动发送
  - AI 分析: 岗位匹配度分析、简历优化建议、沟通建议
  - 数据统计: 每日统计、设置管理
"""

import asyncio
import json
import sys
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

# ── 确保 boss 模块在 sys.path ──
_BOSS_DIR = Path(__file__).resolve().parent.parent / "boss"
if str(_BOSS_DIR) not in sys.path:
    sys.path.insert(0, str(_BOSS_DIR))

from boss.manager import BossManager
from boss_state import (
    add_application,
    get_application,
    get_application_by_url,
    update_application_from_job,
    list_applications,
    update_application_status,
    get_today_application_count,
    get_or_create_conversation,
    get_conversation,
    list_active_conversations,
    add_message,
    get_messages,
    replace_conversation_messages,
    update_conversation_last_message,
    update_conversation_status,
    set_auto_reply,
    get_setting,
    set_setting,
    get_all_settings,
    get_daily_stats,
    get_wechat_exchanges,
    get_transfer_requests,
    add_to_shortlist,
    remove_from_shortlist,
    list_shortlists,
    is_in_shortlist,
    list_jobs_by_company,
    list_companies_by_position_count,
    company_already_applied,
    get_db,
    init_db,
)
from boss_replier import generate_greeting, generate_greeting_ai
from boss_company import build_company_preview, rank_companies_by_position_count

router = APIRouter(prefix="/api/boss", tags=["BOSS直聘自动化"])

# ── 初始化数据库 ──
try:
    init_db()
except Exception as e:
    print(f"[Boss API] DB init warning: {e}")

# ── BOSS直聘城市代码 ──
CITY_MAP = {
    "北京": "101010100", "上海": "101020100", "广州": "101280100", "深圳": "101280600",
    "成都": "101270100", "杭州": "101210100", "武汉": "101200100", "南京": "101190100",
    "重庆": "101040100", "西安": "101110100", "长沙": "101250100", "天津": "101030100",
    "苏州": "101190400", "郑州": "101180100", "东莞": "101281600", "沈阳": "101070100",
    "宁波": "101210400", "昆明": "101290100", "合肥": "101220100", "福州": "101230100",
    "厦门": "101230200", "南昌": "101240100", "贵阳": "101260100", "南宁": "101300100",
    "太原": "101100100", "石家庄": "101090100", "哈尔滨": "101050100", "长春": "101060100",
    "兰州": "101160100", "乌鲁木齐": "101130100", "呼和浩特": "101080100",
    "拉萨": "101140100", "西宁": "101150100", "银川": "101170100",
    "海口": "101310100", "三亚": "101310200", "全国": "100010000",
    "济南": "101120100", "青岛": "101120200", "烟台": "101120500", "潍坊": "101120600",
    "临沂": "101120900", "淄博": "101120300", "济宁": "101120700",
}


def _get_manager() -> BossManager:
    return BossManager.get_instance()


def _normalize_job_url(url: str) -> str:
    from urllib.parse import urljoin
    return urljoin("https://www.zhipin.com", (url or "").strip())


def _attach_hr_active_days(job: dict):
    """将 hr_active 字符串转为天数"""
    import re
    text = job.get("hr_active", "") or ""
    if not text:
        job["hr_active_days"] = None
        job["hr_active_label"] = ""
        return
    if "今日" in text or "今天" in text:
        job["hr_active_days"] = 0
    elif "昨日" in text or "昨天" in text:
        job["hr_active_days"] = 1
    else:
        m = re.search(r"(\d+)日", text)
        if m:
            job["hr_active_days"] = int(m.group(1))
        else:
            job["hr_active_days"] = None
    job["hr_active_label"] = text


def _search_job_payload(job: dict, application: Optional[dict] = None) -> dict:
    """统一搜索结果和数据库记录的字段名"""
    application = application or {}
    company = application.get("company") or job.get("company", "")
    company_id = application.get("company_id") or job.get("company_id", "")
    result = {
        "id": application.get("id"),
        "job_title": application.get("job_title") or job.get("title", ""),
        "company": company,
        "company_id": company_id,
        "company_already_applied": company_already_applied(company=company, company_id=company_id),
        "salary": application.get("salary") or job.get("salary", ""),
        "job_url": application.get("job_url") or _normalize_job_url(job.get("url", "")),
        "city": application.get("city") or job.get("city", ""),
        "experience": application.get("experience") or job.get("experience", ""),
        "education": application.get("education") or job.get("education", ""),
        "company_size": application.get("company_size") or job.get("company_size", ""),
        "industry": application.get("industry") or job.get("industry", ""),
        "legal_rep": application.get("legal_rep") or job.get("legal_rep", ""),
        "is_boss": bool(application.get("is_boss") or job.get("is_boss", False)),
        "hr_name": application.get("hr_name") or job.get("hr_name", ""),
        "hr_title": application.get("hr_title") or job.get("hr_title", ""),
        "hr_active": application.get("hr_active") or job.get("hr_active", ""),
        "description": application.get("description") or job.get("description", ""),
        "status": application.get("status") or ("pending" if job.get("url") else "missing_url"),
    }
    _legal = result.get("legal_rep") or ""
    _hr = result.get("hr_name") or ""
    if _legal and _hr and _hr == _legal:
        result["is_boss"] = True
    _attach_hr_active_days(result)
    return result


# ══════════════════════════════════════
#  请求模型
# ══════════════════════════════════════

class SearchRequest(BaseModel):
    keyword: str = ""
    city: str = ""
    welfare: Optional[str] = None
    limit: int = 60
    district: Optional[str] = ""
    districts: Optional[List[str]] = None
    areas: Optional[List[str]] = None
    company_size: Optional[List[str]] = None
    company_size_str: Optional[str] = None

class ApplyRequest(BaseModel):
    job_url: str
    greeting: Optional[str] = None

class ApplyBatchRequest(BaseModel):
    job_urls: List[str]
    greeting: Optional[str] = None

class ScanAndApplyRequest(BaseModel):
    greeting: Optional[str] = None

class AnalyzeRequest(BaseModel):
    job_url: str
    job_title: Optional[str] = ""
    company: Optional[str] = ""
    description: Optional[str] = ""
    company_id: Optional[str] = ""

class SendMessageRequest(BaseModel):
    content: str

class SettingsUpdate(BaseModel):
    greeting_template: Optional[str] = None
    greeting_enabled: Optional[str] = None
    greeting_mode: Optional[str] = None
    smart_greeting_prompt: Optional[str] = None
    ai_reply_style: Optional[str] = None
    daily_apply_limit: Optional[str] = None
    auto_reply_enabled: Optional[str] = None
    min_reply_delay_sec: Optional[str] = None
    max_reply_delay_sec: Optional[str] = None
    batch_delay_min_sec: Optional[str] = None
    batch_delay_max_sec: Optional[str] = None
    resume_summary: Optional[str] = None
    wechat_id: Optional[str] = None
    search_keywords: Optional[str] = None
    default_city: Optional[str] = None


# ══════════════════════════════════════
#  系统控制
# ══════════════════════════════════════

@router.get("/health")
def boss_health():
    return {"status": "ok", "browser": _get_manager().automation is not None}


@router.get("/status")
def boss_status():
    return _get_manager().get_status()


@router.post("/system/start")
async def boss_start():
    return await _get_manager().start(headless=False)


@router.post("/system/stop")
async def boss_stop():
    return await _get_manager().stop()


@router.post("/system/relogin")
async def boss_relogin():
    return await _get_manager().relogin()


@router.post("/system/heartbeat")
async def boss_heartbeat():
    return await _get_manager().heartbeat()


@router.post("/system/navigate-chat")
async def boss_navigate_chat():
    manager = _get_manager()
    if not manager.automation or manager.automation.page is None:
        raise HTTPException(status_code=503, detail="浏览器未启动")
    success = await manager.run_pw(manager.automation.navigate_to_chat)
    return {"status": "ok" if success else "error"}


# ══════════════════════════════════════
#  监控控制
# ══════════════════════════════════════

@router.post("/monitor/pause")
def boss_monitor_pause():
    return _get_manager().pause_monitor()


@router.post("/monitor/resume")
def boss_monitor_resume():
    return _get_manager().resume_monitor()


# ══════════════════════════════════════
#  岗位搜索 & 投递
# ══════════════════════════════════════

@router.get("/jobs")
def boss_list_jobs(status: Optional[str] = None, limit: int = 100):
    jobs = list_applications(status, limit)
    return {"jobs": jobs, "total": len(jobs)}


@router.post("/jobs/search")
async def boss_search_jobs(req: SearchRequest):
    manager = _get_manager()
    if not manager.automation or manager.automation.page is None:
        raise HTTPException(status_code=503, detail="浏览器未启动，请先点击「启动浏览器」")

    async with manager.lock_browser():
        city_code = CITY_MAP.get(req.city or get_setting("default_city", "全国"), "100010000")
        try:
            cs_list: list = []
            if req.company_size:
                cs_list = [str(s).strip() for s in req.company_size if s]
            if req.company_size_str and req.company_size_str not in cs_list:
                cs_list.append(req.company_size_str)

            ds_list: list = []
            if req.districts:
                ds_list = [str(d).strip() for d in req.districts if d]
            if req.district and req.district not in ds_list:
                ds_list.append(req.district)

            ar_list: list = []
            if req.areas:
                ar_list = [str(a).strip() for a in req.areas if a]

            jobs = await manager.run_pw(
                manager.automation.search,
                req.keyword, city_code,
                ds_list or None, "",
                cs_list if cs_list else None,
                ar_list if ar_list else None,
            )
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"搜索失败: {e}")

        if req.welfare:
            welfare_kw = [w.strip() for w in req.welfare.split(",") if w.strip()]
            jobs = manager.automation._filter_by_welfare(jobs, welfare_kw)

        saved_ids = []
        result_jobs = []
        for j in jobs:
            j["url"] = _normalize_job_url(j.get("url", ""))
            if j.get("url"):
                existing = get_application_by_url(j["url"])
                if existing:
                    updated = update_application_from_job(existing["id"], j) or existing
                    saved_ids.append(updated["id"])
                    result_jobs.append(_search_job_payload(j, updated))
                else:
                    aid = add_application(j)
                    if aid:
                        saved_ids.append(aid)
                        result_jobs.append(_search_job_payload(j, get_application(aid)))
                    else:
                        result_jobs.append(_search_job_payload(j))
            else:
                result_jobs.append(_search_job_payload(j))

        return {"jobs_found": len(jobs), "saved": len(saved_ids), "jobs": result_jobs}


@router.get("/jobs/{job_id}")
def boss_get_job(job_id: int):
    job = get_application(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="岗位不存在")
    return {"job": job}


@router.post("/jobs/{job_id}/skip")
def boss_skip_job(job_id: int):
    update_application_status(job_id, "skipped")
    return {"status": "ok"}


@router.post("/jobs/apply")
async def boss_apply(req: ApplyRequest):
    manager = _get_manager()
    if not manager.automation:
        raise HTTPException(status_code=503, detail="浏览器未启动")

    async with manager.lock_browser():
        daily_limit = int(get_setting("daily_apply_limit", "15"))
        if get_today_application_count() >= daily_limit:
            raise HTTPException(status_code=429, detail="已达到今日投递上限")

        greeting = req.greeting
        if not greeting:
            job = get_application_by_url(req.job_url)
            title = job["job_title"] if job else "相关岗位"
            company = job["company"] if job else "贵公司"
            job_desc = (job or {}).get("description", "") if job else ""
            is_boss = bool((job or {}).get("is_boss")) if job else False
            style = get_setting("ai_reply_style", "professional")
            resume = get_setting("resume_summary", "")
            opt_hints = ""
            if job:
                try:
                    _opt = job.get("optimize_result") or ""
                    if _opt:
                        _opt_data = json.loads(_opt) if isinstance(_opt, str) else _opt
                        parts = []
                        if _opt_data.get("one_line"):
                            parts.append("核心定位: " + _opt_data["one_line"])
                        if _opt_data.get("match_gaps"):
                            parts.append("匹配差距: " + ", ".join(_opt_data["match_gaps"][:3]))
                        if _opt_data.get("optimize_tips"):
                            for t in _opt_data.get("optimize_tips", [])[:2]:
                                parts.append(f"{t.get('area', '')}: {t.get('suggestion', '')}")
                        opt_hints = "\n".join(parts)
                except Exception:
                    pass
            greeting = await asyncio.to_thread(
                generate_greeting_ai, title, company, "", job_desc, is_boss, style, resume, opt_hints
            )

        result = await manager.run_pw(manager.automation.apply_to_job, req.job_url, greeting)
        return result


@router.post("/jobs/apply-batch")
async def boss_apply_batch(req: ApplyBatchRequest):
    manager = _get_manager()
    if not manager.automation:
        raise HTTPException(status_code=503, detail="浏览器未启动")

    async with manager.lock_browser():
        daily_limit = int(get_setting("daily_apply_limit", "15"))
        remaining = daily_limit - get_today_application_count()
        urls = req.job_urls[: max(1, remaining)]
        results = await manager.run_pw(manager.automation.apply_batch, urls, req.greeting)
        return {"results": results}


@router.post("/jobs/scan")
async def boss_scan_current_page():
    manager = _get_manager()
    if not manager.automation or manager.automation.page is None:
        raise HTTPException(status_code=503, detail="浏览器未启动")

    async with manager.lock_browser():
        try:
            jobs = await manager.run_pw(manager.automation.scan_current_page)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"扫描失败: {e}")

        saved_ids = []
        result_jobs = []
        for j in jobs:
            j["url"] = _normalize_job_url(j.get("url", ""))
            if j.get("url"):
                existing = get_application_by_url(j["url"])
                if existing:
                    updated = update_application_from_job(existing["id"], j) or existing
                    saved_ids.append(updated["id"])
                    result_jobs.append(_search_job_payload(j, updated))
                else:
                    aid = add_application(j)
                    if aid:
                        saved_ids.append(aid)
                        result_jobs.append(_search_job_payload(j, get_application(aid)))
                    else:
                        result_jobs.append(_search_job_payload(j))
            else:
                result_jobs.append(_search_job_payload(j))

        return {"jobs_found": len(jobs), "saved": len(saved_ids), "jobs": result_jobs}


@router.post("/jobs/scan-and-apply")
async def boss_scan_and_apply(req: ScanAndApplyRequest = ScanAndApplyRequest()):
    manager = _get_manager()
    if not manager.automation:
        raise HTTPException(status_code=503, detail="浏览器未启动")

    async with manager.lock_browser():
        daily_limit = int(get_setting("daily_apply_limit", "15"))
        if get_today_application_count() >= daily_limit:
            raise HTTPException(status_code=429, detail="已达到今日投递上限")
        result = await manager.run_pw(manager.automation.scan_and_apply_current_page, req.greeting)
        return result


# ══════════════════════════════════════
#  AI 分析
# ══════════════════════════════════════

@router.post("/jobs/analyze")
async def boss_analyze_jd(req: AnalyzeRequest):
    """AI分析岗位JD，返回匹配度、关键技能、差距、建议。"""
    from boss.manager import _BOSS_DIR
    sys.path.insert(0, str(_BOSS_DIR / "interview"))
    from llm_client import llm_chat_deepseek

    resume = get_setting("resume_summary", "")
    desc = req.description or ""
    title = req.job_title or ""
    company = req.company or ""

    if resume and len(resume.strip()) > 5:
        prompt = f"""你是求职辅导专家。分析以下岗位JD，对比求职者简历，输出JSON。

## 求职者简历
{resume}

## 岗位信息
- 公司: {company}
- 职位: {title}
- JD: {desc[:2000]}

## 输出格式（严格JSON）
{{
  "match_score": 85,
  "decision": "建议投递",
  "key_skills": ["Python", "LangChain", "RAG"],
  "gap": "缺少K8s部署经验",
  "advice": "建议强调Agent开发经验",
  "summary": "整体匹配度较高",
  "reasons": ["匹配理由1"],
  "risks": ["风险点1"],
  "suggested_questions": ["建议追问1"]
}}"""
    else:
        prompt = f"""你是求职辅导专家。分析以下岗位JD，提取关键信息，输出JSON。

## 岗位信息
- 公司: {company}
- 职位: {title}
- JD: {desc[:2000]}

## 输出格式（严格JSON）
{{
  "match_score": 70,
  "decision": "可以尝试",
  "key_skills": ["Python"],
  "gap": "",
  "advice": "建议了解岗位详情",
  "summary": "岗位分析",
  "reasons": [],
  "risks": [],
  "suggested_questions": []
}}"""

    try:
        raw = await asyncio.to_thread(llm_chat_deepseek, [{"role": "user", "content": prompt}], None, 0.3)
        import re
        json_match = re.search(r"\{.*\}", raw, re.DOTALL)
        if json_match:
            return json.loads(json_match.group())
        return {"raw": raw}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"AI分析失败: {e}")


# ══════════════════════════════════════
#  会话 & 聊天
# ══════════════════════════════════════

@router.get("/conversations")
def boss_list_conversations():
    convs = list_active_conversations()
    return {"conversations": convs}


@router.get("/conversations/{conv_id}")
def boss_get_conversation(conv_id: int):
    conv = get_conversation(conv_id)
    if not conv:
        raise HTTPException(status_code=404, detail="会话不存在")
    messages = get_messages(conv_id, 100)
    return {"conversation": conv, "messages": messages}


@router.get("/conversations/{conv_id}/messages")
def boss_get_messages(conv_id: int, limit: int = 50):
    return {"messages": get_messages(conv_id, limit)}


@router.post("/conversations/{conv_id}/send")
async def boss_send_message(conv_id: int, req: SendMessageRequest):
    """手动在指定会话中发送消息。"""
    manager = _get_manager()
    conv = get_conversation(conv_id)
    if not conv:
        raise HTTPException(status_code=404, detail="会话不存在")
    if not manager.automation or manager.automation.page is None:
        raise HTTPException(status_code=503, detail="浏览器未启动")

    hr_name = conv.get("hr_name", "")
    if not hr_name:
        raise HTTPException(status_code=400, detail="会话缺少HR姓名")

    async with manager.lock_browser():
        try:
            opened = await asyncio.wait_for(
                manager.run_pw(manager.automation.open_conversation_by_name, hr_name),
                timeout=10,
            )
            if not opened:
                raise HTTPException(status_code=500, detail=f"无法打开 {hr_name} 的会话")
            await manager.run_pw(manager.automation.send_message, req.content)
            add_message(conv_id, "me", req.content, ai_generated=False)
            update_conversation_last_message(conv_id, req.content, "me")
            return {"status": "ok", "message": "发送成功"}
        except asyncio.TimeoutError:
            raise HTTPException(status_code=504, detail="操作超时")
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"发送失败: {e}")


@router.post("/conversations/{conv_id}/pause")
def boss_pause_conversation(conv_id: int):
    set_auto_reply(conv_id, False)
    return {"status": "ok"}


@router.post("/conversations/{conv_id}/resume")
def boss_resume_conversation(conv_id: int):
    set_auto_reply(conv_id, True)
    return {"status": "ok"}


# ══════════════════════════════════════
#  统计 & 设置
# ══════════════════════════════════════

@router.get("/stats")
def boss_stats():
    from boss_state import get_today_application_count, get_today_auto_reply_count, get_today_pending_count
    return {
        "today_applications": get_today_application_count(),
        "today_auto_replies": get_today_auto_reply_count(),
        "today_pending": get_today_pending_count(),
        "daily_stats": get_daily_stats(),
    }


@router.get("/settings")
def boss_get_settings():
    settings = get_all_settings()
    ai_key = settings.get("ai_api_key", "")
    settings["ai_key_configured"] = "true" if ai_key and len(ai_key) > 10 else "false"
    # 不返回完整 API Key
    if "ai_api_key" in settings and settings["ai_api_key"]:
        settings["ai_api_key"] = settings["ai_api_key"][:8] + "***"
    return {"settings": settings}


@router.put("/settings")
async def boss_update_settings(req: SettingsUpdate):
    _key_alias = {"greeting_enabled": "ai_greeting_enabled"}
    updates = {}
    for k, v in req.model_dump().items():
        actual_key = _key_alias.get(k, k)
        if v is not None and v != "":
            set_setting(actual_key, str(v))
            updates[actual_key] = str(v)
    return {"status": "ok", "updated": updates}


# ══════════════════════════════════════
#  候选池 & 其他
# ══════════════════════════════════════

@router.get("/shortlists")
def boss_get_shortlists():
    return {"shortlists": list_shortlists()}


@router.post("/shortlists")
def boss_add_shortlist(req: dict):
    url = req.get("job_url", "")
    if not url:
        raise HTTPException(status_code=400, detail="缺少 job_url")
    if is_in_shortlist(url):
        return {"status": "already_exists"}
    sid = add_to_shortlist(
        url, req.get("title", ""), req.get("company", ""),
        req.get("salary", ""), req.get("city", ""), req.get("note", ""),
    )
    return {"status": "ok", "id": sid} if sid else {"status": "duplicate"}


@router.delete("/shortlists/{sid}")
def boss_remove_shortlist(sid: int):
    remove_from_shortlist(sid)
    return {"status": "ok"}


@router.get("/wechat-exchanges")
def boss_wechat_exchanges():
    return {"exchanges": get_wechat_exchanges()}


@router.get("/transfer-requests")
def boss_transfer_requests():
    return {"requests": get_transfer_requests()}


# ══════════════════════════════════════
#  城市数据
# ══════════════════════════════════════

@router.get("/geo/cities")
def boss_geo_cities():
    return {"cities": [{"name": k, "code": v} for k, v in CITY_MAP.items()]}
