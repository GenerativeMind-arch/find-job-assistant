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
from pydantic import BaseModel, field_validator

from log import get_logger

logger = get_logger("boss")

# ── 确保 boss 模块在 sys.path ──
_BOSS_DIR = Path(__file__).resolve().parent.parent / "boss"
if str(_BOSS_DIR) not in sys.path:
    sys.path.insert(0, str(_BOSS_DIR))

from boss.manager import (
    LOCK_WAIT_TIMEOUT_SEC,
    BossManager,
    BrowserBusyError,
    summarize_apply_results,
)
from boss_state import (
    add_application,
    get_application,
    get_application_by_url,
    update_application_from_job,
    list_applications,
    update_application_status,
    get_today_application_count,
    get_apply_allowance,
    get_or_create_conversation,
    get_conversation,
    list_active_conversations,
    add_message,
    get_messages,
    get_message,
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


async def browser_wedged_handler(request, exc):  # noqa: ANN001
    """浏览器线程卡死 → 503 + 一句能直接显示给用户的处置办法。

    定义在这里（而不是直接写在 main.py 的装饰器里）是为了能被测试注册：
    main.py 用 `app.add_exception_handler(BrowserWedgedError, browser_wedged_handler)`
    挂上它。没有这个 handler，异常会被 FastAPI 兜成 500 "Internal Server Error"，
    真正的提示（"请点停止浏览器再重新启动"）就丢了，用户只看到一句无用的 500。
    """
    from fastapi.responses import JSONResponse

    return JSONResponse(status_code=503, content={"detail": str(exc)})

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
    # 新增筛选：值可传中文标签（如"3-5年"/"本科"，后端解析为 BOSS 编码）
    experience: Optional[List[str]] = None
    salary: Optional[List[str]] = None
    degree: Optional[List[str]] = None
    job_type: Optional[List[str]] = None

    @field_validator(
        "districts", "areas", "company_size", "experience", "salary", "degree", "job_type",
        mode="before",
    )
    @classmethod
    def _coerce_scalar_to_list(cls, v):
        """允许这些字段传单个字符串（如 experience="3-5年"）。

        多选语义天然是列表，但调用方（CLI/脚本）很容易顺手传标量；
        这里统一归一为列表，避免"字符串 → 422"这类调用方踩坑。
        """
        if isinstance(v, str):
            return [item.strip() for item in v.split(",") if item.strip()]
        return v

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
    # 自有定制话术（第二条消息）
    custom_greeting_enabled: Optional[str] = None
    custom_greeting_delay_min_sec: Optional[str] = None
    custom_greeting_delay_max_sec: Optional[str] = None
    custom_greeting_max_len: Optional[str] = None
    # 本地相关性重排/过滤
    relevance_rank_enabled: Optional[str] = None
    relevance_filter_enabled: Optional[str] = None
    relevance_min_score: Optional[str] = None
    relevance_llm_enabled: Optional[str] = None
    ai_reply_style: Optional[str] = None
    # 动态模式下 daily_apply_limit 是**起步额度**，会一路涨到 apply_daily_max；
    # 关掉 apply_dynamic_enabled 才退回旧的固定行为。
    daily_apply_limit: Optional[str] = None
    apply_dynamic_enabled: Optional[str] = None
    apply_daily_max: Optional[str] = None
    auto_reply_enabled: Optional[str] = None
    # 已废弃：监控循环改为双节拍后不再读取，保留仅为兼容旧客户端
    min_reply_delay_sec: Optional[str] = None
    max_reply_delay_sec: Optional[str] = None
    scan_interval_sec: Optional[str] = None       # 未读扫描周期（秒），默认 3600
    heartbeat_interval_sec: Optional[str] = None  # 心跳/保活周期（秒），默认 120
    batch_delay_min_sec: Optional[str] = None
    batch_delay_max_sec: Optional[str] = None
    resume_summary: Optional[str] = None
    wechat_id: Optional[str] = None
    search_keywords: Optional[str] = None
    default_city: Optional[str] = None


class ImportJobRequest(BaseModel):
    """导入 Boss 岗位到主工作流。"""
    job_url: Optional[str] = None
    job_id: Optional[int] = None
    session_id: Optional[str] = ""


class AgentRunRequest(BaseModel):
    """运行 Plan-then-Execute Agent（可选高级功能）。"""
    goal: str
    max_steps: Optional[int] = 12
    auto_execute: Optional[bool] = True


class StartRequest(BaseModel):
    """启动浏览器请求。

    headless: 是否无头模式。默认 None（读 config.yaml 的 boss.headless）。
              前端交互流程请显式传 false，确保弹出可见浏览器以便扫码登录。
    """
    headless: Optional[bool] = None


# ══════════════════════════════════════
#  Agent Harness（ToolRegistry + AgentLoop）
# ══════════════════════════════════════

_agent_registry = None


def _get_agent_registry():
    """惰性初始化 ToolRegistry（14 原子工具 + 3 复合技能）。"""
    global _agent_registry
    if _agent_registry is None:
        from boss.agent import ToolRegistry, register_all
        _agent_registry = ToolRegistry()
        register_all(_agent_registry)
    return _agent_registry


def _convert_boss_job_to_copilot_job(record: dict) -> dict:
    """将 boss_state 的岗位记录转换为 CopilotState.Job 结构。

    id 以 boss_ 开头（供 planner 启发式识别），parsed_at 标记为已解析，
    source 填入完整 JD 正文，供下游 gap/content agent 使用。
    """
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    title = record.get("job_title") or record.get("title") or ""
    company = record.get("company") or ""
    description = record.get("description") or ""
    source = description or title
    if company and description:
        source = f"公司：{company}\n职位：{title}\n\n{description}"
    return {
        "id": f"boss_{record.get('id', '')}",
        "source": source,
        "parsed_at": now,
        "version": 1,
        "industry": record.get("industry", ""),
        "title": title,
        "tech_stack": [],
        "keywords": [],
        "hard_skills": [],
        "soft_skills": [],
        "responsibilities": [],
        "education_requirement": record.get("education", ""),
        "experience_requirement": record.get("experience", ""),
        "implicit_preferences": [],
        "bonus_items": [],
    }


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
async def boss_start(req: Optional[StartRequest] = None):
    manager = _get_manager()
    headless = req.headless if (req and req.headless is not None) else None
    result = await manager.start(headless=headless)
    if result.get("status") == "error":
        raise HTTPException(status_code=500, detail=result.get("message", "浏览器启动失败"))
    return result


@router.post("/system/stop")
async def boss_stop():
    return await _get_manager().stop()


@router.post("/system/relogin")
async def boss_relogin():
    manager = _get_manager()
    result = await manager.relogin()
    if result.get("status") == "error":
        raise HTTPException(status_code=500, detail=result.get("message", "重新登录失败"))
    return result


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
async def boss_monitor_pause():
    """⚠️ 遗留端点：只标记"浏览器正忙"，**不会停止自动回复**。

    想真正停回复请用 POST /api/boss/auto-reply {"enabled": false}。
    保留它只是为了兼容旧客户端，新代码不要再接。
    """
    return _get_manager().pause_monitor()


@router.post("/monitor/resume")
async def boss_monitor_resume():
    """⚠️ 遗留端点：只清除"浏览器忙"标记，不会恢复已停止的自动回复。见 /auto-reply。

    必须是 async：resume_monitor 会唤醒监控循环用的 asyncio.Event，
    而 Event.set() 只能在事件循环线程里调（sync 端点会被 FastAPI 丢进线程池）。
    """
    return _get_manager().resume_monitor()


class AutoReplyRequest(BaseModel):
    enabled: bool


@router.post("/auto-reply")
async def boss_set_auto_reply(req: AutoReplyRequest):
    """自动回复总开关：写持久化设置 + 同步运行期监控状态。

    前端那颗「恢复回复 / 停止回复」按钮走这里。之前按钮只调 /monitor/pause|resume
    （仅翻运行期 monitor_paused），而监控循环真正判断的是 auto_reply_enabled
    设置项 —— 开关是关的时候点「恢复回复」看起来毫无反应。
    """
    return await _get_manager().set_auto_reply_enabled(req.enabled)


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
        # 城市留空 = **真的全国**。原先这里 fallback 到 default_city，而库里那个值
        # 是 "淄博"（上游项目留下的默认值），前端却把空选项标成「全国」——
        # 界面写全国、实际在淄博里搜，结果自然和手机端完全对不上（实测搜 "ai"
        # 全国上万条，淄博只有 27 条）。空就是空，不要再静默换成某个城市。
        city_code = CITY_MAP.get(req.city or "", "100010000")
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
                req.experience, req.salary, req.degree, req.job_type,
                req.limit,
            )
        except Exception as e:
            # 必须记日志：这里只抛 HTTPException，不落日志的话真正原因（如 Playwright
            # 超时）就彻底丢了，前端只看到一句 "搜索失败: timeout"
            logger.error("Boss search failed: %s", e, exc_info=True)
            raise HTTPException(status_code=500, detail=f"搜索失败: {type(e).__name__}: {e}")

        if req.welfare:
            welfare_kw = [w.strip() for w in req.welfare.split(",") if w.strip()]
            jobs = manager.automation._filter_by_welfare(jobs, welfare_kw)

        # 本地相关性重排。BOSS 的搜索是服务端分词宽泛匹配（搜 "AI开发" 会混进
        # 只在 JD 里提过 AI 的岗位），而搜索页没有排序控件、服务端也没有可用的排序
        # 参数 —— 只能在本地做。默认只重排不隐藏（见 boss_relevance 模块说明）。
        relevance_stats = None
        if get_setting("relevance_rank_enabled", "true") == "true":
            try:
                from boss_relevance import rank_jobs

                min_score = None
                if get_setting("relevance_filter_enabled", "false") == "true":
                    min_score = float(get_setting("relevance_min_score", "3") or 0)
                jobs, relevance_stats = rank_jobs(jobs, req.keyword, min_score=min_score)
                print(
                    f"  [相关性] tokens={relevance_stats.get('tokens')} "
                    f"保留={len(jobs)} 丢弃={relevance_stats.get('dropped')} "
                    f"标题命中={relevance_stats.get('title_matched')}"
                )
            except Exception as e:
                # 重排失败不能影响搜索本身，退回 BOSS 原始顺序
                logger.warning("relevance ranking failed: %s", e)

        # LLM 复核：上面的 token 命中只能证明"字面上出现过"，判断不了语义等价
        # （搜"Agent开发"时标题写着"大模型应用工程师"的岗位字面不命中却完全相关），
        # 也挡不住"JD 里恰好提过这个词"的无关岗位。整批交给 LLM 复核一遍，
        # 只删**明显不相关**的。失败/解析不出来时一条都不删（宁可放过）。
        llm_dropped: list = []
        if jobs and (req.keyword or "").strip() and get_setting("relevance_llm_enabled", "true") == "true":
            try:
                from boss_relevance import (
                    LLM_FILTER_MAX_ITEMS,
                    build_llm_filter_prompt,
                    drop_by_llm,
                    parse_llm_filter_result,
                )
                from models.llm import acall_llm

                system, user = build_llm_filter_prompt(req.keyword, jobs)
                reviewed = min(len(jobs), LLM_FILTER_MAX_ITEMS)
                raw = await acall_llm(system, user)
                drops = parse_llm_filter_result(raw, reviewed)
                if drops:
                    jobs, llm_dropped = drop_by_llm(jobs, drops)
                print(f"  [相关性-LLM] 复核={reviewed} 删除={len(llm_dropped)}")
            except Exception as e:
                # 复核失败只是退回"不删"，不能影响搜索本身
                logger.warning("LLM relevance filter failed: %s", e)
                llm_dropped = []

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

        # 已投递/已沟通的不再出现在搜索结果里（否则每次搜索都是同一批老岗位）。
        # 记录仍留在 applications 表（会话/统计页照常可见），只是这里不返回。
        _contacted = {"greeting_sent", "applied", "replied"}
        hidden = [j for j in result_jobs if j.get("status") in _contacted]
        if hidden:
            result_jobs = [j for j in result_jobs if j.get("status") not in _contacted]

        meta = dict(getattr(manager.automation, "last_search_meta", {}) or {})
        if relevance_stats or llm_dropped:
            rel_meta = {
                k: (relevance_stats or {}).get(k)
                for k in ("tokens", "dropped", "title_matched", "min_score")
            }
            # 删了哪些、为什么 —— 过滤必须可解释，用户才能判断是否误删
            rel_meta["llm_dropped"] = len(llm_dropped)
            rel_meta["llm_dropped_jobs"] = llm_dropped[:10]
            meta["relevance"] = rel_meta
        if hidden:
            meta["hidden_contacted"] = len(hidden)
        return {
            "jobs_found": len(result_jobs),
            "jobs_hidden": len(hidden),
            "saved": len(saved_ids),
            "jobs": result_jobs,
            "search_meta": meta,
            "hint": _search_hint(meta, len(result_jobs)),
        }


def _search_hint(meta: dict, found: int) -> str:
    """「搜到 0 条」到底为什么 —— 给前端一句能直接显示的话。"""
    if found:
        return ""
    if meta.get("hidden_contacted"):
        return (
            f"本轮 BOSS 返回的 {meta['hidden_contacted']} 条岗位都已投递/已沟通，已从列表隐藏。"
            "换关键词、城市或筛选条件才能看到新岗位。"
        )
    err = meta.get("xhr_error") or ""
    if err:
        return f"BOSS 列表接口被拒绝（{err}）。通常是触发风控或登录态过期，请重新登录后再试。"
    if meta.get("source") == "heuristic":
        return "接口和页面结构都没解析出岗位，BOSS 可能改版了页面，请把后端控制台日志发给开发者。"
    return (
        "该筛选组合下 BOSS 没有返回岗位。经验/薪资/学历/公司规模叠加后范围会非常窄，"
        "试着放宽其中一两项，或换更大的城市。"
    )


@router.get("/filters")
async def boss_get_filters():
    """返回可用的筛选项标签（经验/薪资/学历/规模/融资/职位类型 + 常见福利标签）。

    数据来自 BOSS 公开条件接口（免登录），服务端直连并缓存——不依赖浏览器，
    因此未启动浏览器时前端下拉也能正常显示。
    """
    try:
        from boss_filters import list_options

        return await asyncio.to_thread(list_options)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取筛选项失败: {e}")


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
        allowance = get_apply_allowance()
        if allowance["remaining"] <= 0:
            raise HTTPException(status_code=429, detail=allowance["reason"] or "已达到今日投递额度")

        greeting = req.greeting
        # 只有「追加自有定制话术」开着时才需要生成。关着的话这条话术根本不会发出去
        # （平台自己那条已由「立即沟通」发出），白调一次 LLM。
        if not greeting and get_setting("custom_greeting_enabled", "false") == "true":
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

        return await manager.apply_job_async(req.job_url, greeting)


@router.post("/jobs/apply-batch")
async def boss_apply_batch(req: ApplyBatchRequest):
    manager = _get_manager()
    if not manager.automation:
        raise HTTPException(status_code=503, detail="浏览器未启动")

    async with manager.lock_browser():
        allowance = get_apply_allowance()
        if allowance["remaining"] <= 0:
            raise HTTPException(status_code=429, detail=allowance["reason"] or "已达到今日投递额度")
        urls = req.job_urls[: allowance["remaining"]]
        results = await manager.apply_batch_async(urls, req.greeting)
        return {"results": results, "summary": summarize_apply_results(results, len(req.job_urls))}


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
    """一键投递：把**岗位库里 status=pending 的岗位**批量投出去。

    早先实现是 `automation.scan_and_apply_current_page` —— 扫描**浏览器当前页**的
    DOM 取岗位。但 `apply_to_job` 会 goto 到岗位详情页且不返回，所以投过任意一个
    岗位之后「当前页」就不再是搜索结果页，扫描恒为 0（浏览器刚启动、停在首页时同理）。
    现在改为从岗位库取待投递岗位，与浏览器停在哪一页无关 —— 与前端「一键投递」
    （直接调 /jobs/apply-batch）同语义，CLI 的 `scan-apply` 也因此能用了。
    """
    manager = _get_manager()
    if not manager.automation:
        raise HTTPException(status_code=503, detail="浏览器未启动")

    async with manager.lock_browser():
        allowance = get_apply_allowance()
        remaining = allowance["remaining"]
        if remaining <= 0:
            raise HTTPException(status_code=429, detail=allowance["reason"] or "已达到今日投递额度")

        # 库里按 updated_at DESC 排 → 取到的就是最近一次搜索的那批
        pending = list_applications(status="pending", limit=200)
        urls = [j["job_url"] for j in pending if j.get("job_url")][:remaining]
        if not urls:
            return {
                "success": False,
                "message": "岗位库里没有待投递的岗位（先搜索一次，或列表里的岗位都已在沟通中）",
                "scanned": 0,
                "applied": 0,
                "results": [],
            }

        results = await manager.apply_batch_async(urls, req.greeting)
        summary = summarize_apply_results(results, len(urls))
        success_count = summary["applied"]
        return {
            "success": success_count > 0,
            "message": f"待投递 {len(urls)} 个，成功 {success_count} 个",
            "scanned": len(urls),
            "applied": success_count,
            "results": results,
            "summary": summary,
        }


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

    # 搜索列表页只返回公司/薪资，JD 正文在详情页 → 缺失时按需补抓并回写数据库
    if len(desc.strip()) < 20 and req.job_url:
        manager = _get_manager()
        if manager.automation and manager.automation.page is not None:
            try:
                async with manager.lock_browser():
                    fetched = await manager.run_pw(
                        manager.automation.fetch_job_description, req.job_url
                    )
                if fetched and len(fetched.strip()) > len(desc.strip()):
                    desc = fetched
                    _rec = get_application_by_url(_normalize_job_url(req.job_url))
                    if _rec:
                        update_application_from_job(_rec["id"], {"description": desc})
            except Exception as e:
                print(f"  [JD] 按需补抓失败: {e}")

    # 补抓后仍没有 JD → 明确告知，而不是把空 JD 丢给 LLM 换回一段含糊回复
    if len(desc.strip()) < 20:
        return {
            "match_score": 0,
            "decision": "无法分析",
            "key_skills": [], "reasons": [], "risks": [], "suggested_questions": [],
            "gap": "", "advice": "",
            "summary": (
                "没有拿到这个岗位的 JD 正文（搜索列表页只返回公司/薪资）。"
                "请先启动 BOSS 浏览器，我会自动打开详情页抓取 JD；或手动粘贴 JD 再分析。"
            ),
        }

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

    try:
        # 限时等锁：后台扫描整轮持锁（开会话 + LLM + 拟人停顿，动辄几分钟），
        # 不设上限的话这里会无声地卡住，前端只有一个转不完的"发送中..."。
        # 超时回 409 让用户稍后重试，比挂着强。
        async with manager.lock_browser(timeout=LOCK_WAIT_TIMEOUT_SEC):
            try:
                opened = await asyncio.wait_for(
                    manager.run_pw(manager.automation.open_conversation_by_name, hr_name),
                    timeout=10,
                )
                if not opened:
                    raise HTTPException(status_code=500, detail=f"无法打开 {hr_name} 的会话")
                confirmed = await manager.run_pw(manager.automation.send_message, req.content)
                if not confirmed:
                    # send_message 的判断依据是"消息区里出现了这段文字"，页面重渲染慢
                    # 或选择器失配时会误报失败 —— 此时消息很可能其实已经发出去了。
                    # 所以两种极端都不对：报错会让用户重发、可能发成两条；静默成功
                    # 又让用户以为发过了。如实说"没确认上，自己去核对"。
                    #
                    # 不落库：本地一旦记下这条，会话会被标成 has_unreplied=0，自动回复
                    # 就认为 HR 的消息已经回过了，而这条错账**不会自愈**。反过来漏记
                    # 一条真发出去的消息是能自愈的 —— 下一轮扫描的
                    # replace_conversation_messages 会按 DOM 整段重写消息历史。
                    return {
                        "status": "ok",
                        "message": "发送未确认",
                        "warning": "未能确认消息已发出，请到浏览器里核对",
                    }
                msg_id = add_message(conv_id, "me", req.content, ai_generated=False)
                update_conversation_last_message(conv_id, req.content, "me")
                # 回带刚落库的那一行（含真实 id/created_at）：前端据它本地追加，
                # 省掉一次"整段重新拉取"。让前端自己拼时间戳的话，显示的时间会和库里对不上。
                return {"status": "ok", "message": "发送成功", "sent_message": get_message(msg_id)}
            except asyncio.TimeoutError:
                raise HTTPException(status_code=504, detail="操作超时")
            except HTTPException:
                raise
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"发送失败: {e}")
    except BrowserBusyError:
        raise HTTPException(
            status_code=409,
            detail="后台正在扫描会话，浏览器暂时被占用，请稍后重试",
        )


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


# 数值型设置项。它们的读取端一律是裸的 int()/float()（daily_apply_limit 甚至
# 没有 `or 默认值` 兜底），写进空串或非数字会让 /api/boss/status 之类的接口
# 直接 500。所以在这里挡住，把问题当场告诉调用方，而不是留给下一次轮询去炸。
_NUMERIC_SETTINGS = frozenset({
    "daily_apply_limit",
    "apply_daily_max",
    "scan_interval_sec",
    "heartbeat_interval_sec",
    "custom_greeting_delay_min_sec",
    "custom_greeting_delay_max_sec",
    "custom_greeting_max_len",
    "min_reply_delay_sec",
    "max_reply_delay_sec",
    "batch_delay_min_sec",
    "batch_delay_max_sec",
    "batch_rest_every",
})


@router.put("/settings")
async def boss_update_settings(req: SettingsUpdate):
    _key_alias = {"greeting_enabled": "ai_greeting_enabled"}
    updates = {}
    # 只跳过「没传」（None）的字段。空串是合法值 —— 以前把 "" 也当成"没传"，
    # 于是清空简历摘要、清空微信号这类操作会被静默丢弃，用户以为改了却没生效。
    # 前端现在只提交真正改动过的字段，不会再把未触碰的字段当成空串发过来。
    for k, v in req.model_dump().items():
        actual_key = _key_alias.get(k, k)
        if v is None:
            continue
        if actual_key in _NUMERIC_SETTINGS:
            try:
                float(v)
            except (TypeError, ValueError):
                raise HTTPException(
                    status_code=400,
                    detail=f"{actual_key} 需要是数字，收到 {v!r}（空值会写坏读取端，已拒绝）",
                )
        if actual_key == "auto_reply_enabled":
            # 走 set_auto_reply_enabled：它会一并唤醒监控循环、重置扫描到期时刻，
            # 与 Boss 页那颗「停止/恢复回复」按钮行为完全一致。
            await _get_manager().set_auto_reply_enabled(str(v) == "true")
        else:
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


# ══════════════════════════════════════
#  Agent Harness（可选高级功能）
# ══════════════════════════════════════

@router.get("/agent/tools")
def boss_agent_tools():
    """返回 Agent 可用的工具列表（文本描述 + OpenAI function schema）。

    定位：这是"AI 自动求职"高级能力的工具清单，手动操作为默认。
    """
    registry = _get_agent_registry()
    return {
        "count": len(registry.get_names()),
        "names": registry.get_names(),
        "tools": registry.get_text_description(),
        "openai_schema": registry.get_openai_schema(),
    }


@router.post("/agent/run")
async def boss_agent_run(req: AgentRunRequest):
    """运行 Plan-then-Execute Agent，自主执行求职任务（可选高级功能）。

    四阶段：规划 → 执行 → 重规划(仅失败时) → 总结。
    通过 WebSocket 事件广播 plan / step / complete 进展。
    """
    from boss.agent import AgentLoop
    from boss.manager import _BOSS_DIR

    if not req.goal or not req.goal.strip():
        raise HTTPException(status_code=400, detail="缺少 goal 参数")

    registry = _get_agent_registry()
    manager = _get_manager()

    # LLM 函数（复用面试助手 config_loader 配置）
    sys.path.insert(0, str(_BOSS_DIR / "interview"))
    from llm_client import llm_chat_deepseek

    # WebSocket 事件回调
    async def _on_plan(plan: dict):
        await manager._broadcast({"type": "agent_plan", "plan": plan})

    async def _on_step(step: dict):
        await manager._broadcast({"type": "agent_step", "step": step})

    loop = AgentLoop(
        registry=registry,
        llm_chat=llm_chat_deepseek,
        goal=req.goal,
        max_steps=req.max_steps or 12,
        on_plan=_on_plan,
        on_step=_on_step,
    )

    try:
        result = await loop.run()
    except Exception as e:
        import logging
        logging.getLogger("api").error("Agent run failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Agent 执行失败: {e}")

    # 广播完成事件（detail 较大，只广播摘要）
    summary_event = {k: v for k, v in result.items() if k != "detail"}
    await manager._broadcast({"type": "agent_complete", "result": summary_event})

    return result


# ══════════════════════════════════════
#  工作流桥接：Boss 岗位 → 主流水线
# ══════════════════════════════════════

@router.post("/import-job")
async def boss_import_job(req: ImportJobRequest):
    """将 Boss 直聘搜索到的岗位导入主工作流（CopilotState.job）。

    导入后，岗位已预置解析（parsed_at 已设置、id 以 boss_ 开头），
    用户在对话区输入"分析这个岗位"即可触发 jd→差距→简历→面试全链路
    （planner 启发式识别 boss_ 前缀，跳过 jd_agent 重新解析）。
    """
    import uuid as _uuid

    from workflow.state import CopilotState, Job
    from storage.redis_client import get_redis_client, RedisSessionStore

    # 1. 从 boss SQLite 获取岗位记录
    record = None
    if req.job_url:
        record = get_application_by_url(_normalize_job_url(req.job_url))
    elif req.job_id is not None:
        record = get_application(req.job_id)
    if not record:
        raise HTTPException(status_code=404, detail="岗位不存在，请先在 Boss 面板搜索")

    # 1.5 JD 补抓：列表页只有公司/薪资，缺 JD 正文则按需从详情页补抓并回写
    if not (record.get("description") or "").strip() and record.get("job_url"):
        manager = _get_manager()
        if manager.automation and manager.automation.page is not None:
            try:
                async with manager.lock_browser():
                    fetched = await manager.run_pw(
                        manager.automation.fetch_job_description, record["job_url"]
                    )
                if fetched and fetched.strip():
                    update_application_from_job(record["id"], {"description": fetched})
                    record = get_application(record["id"]) or record
            except Exception as e:
                print(f"  [JD] 导入前补抓失败: {e}")

    # 2. 转换为 CopilotState.Job（标记为已解析）
    job = Job(**_convert_boss_job_to_copilot_job(record))

    # 3. 加载或创建会话状态并写入 Redis
    session_id = req.session_id or f"sess_{_uuid.uuid4().hex[:16]}"
    redis_client = await get_redis_client()
    store = RedisSessionStore(session_id, redis_client)
    saved_state = await store.load_state()
    if saved_state:
        state = CopilotState.model_validate(saved_state)
        state.job = job
    else:
        state = CopilotState(session_id=session_id, job=job)

    await store.save_state(state.to_session_snapshot())

    return {
        "session_id": session_id,
        "job": job.model_dump(),
        "status": "imported",
        "message": f"已导入岗位「{job.title}」到工作流会话 {session_id}。请在对话区输入「分析这个岗位」开始分析。",
    }


# ══════════════════════════════════════════════════════════════
# ══════════════════════════════════════════════════════════════
#  风控冷却解除（运维）
# ══════════════════════════════════════════════════════════════


@router.post("/cooldown/clear")
async def boss_clear_cooldown():
    """清掉内存里的风控冷却计时，让投递立即恢复。

    为什么保留这个口子：`banned` 档冷却 3600s 起，而安全检查是"在页面开头扫关键词"
    的启发式 —— 一旦误判，用户就得白等一小时（或重启后端）才能继续。实测记录：
    BOSS 详情页常驻的免责声明含"违规"，曾让每次投递都被判成封号。

    也可以在界面上看到「风控冷却中」时调用它，而不是去改代码或重启。
    """
    manager = _get_manager()
    if not manager.automation:
        raise HTTPException(status_code=503, detail="浏览器未启动")
    before = manager.automation._cooldown_remaining()
    manager.automation._cooldown_until = 0.0
    manager.automation._risk_strikes = 0
    return {"status": "ok", "cleared_seconds": round(before, 1)}
