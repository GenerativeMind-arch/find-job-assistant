"""岗位相关性打分与重排。

为什么需要：BOSS 的搜索是服务端**分词宽泛匹配** —— 关键词 "AI开发" 会被拆成
`ai` + `开发`，只要 JD 里出现 "AI" 就可能命中（实测搜 "AI开发" 会混进"原料外销
业务员"）。而实测（2026-10-10）搜索页上**只有筛选控件、没有排序控件**，服务端
也没有可用的排序参数，所以相关性只能在本地做。

设计原则：
  - **标题命中远比 JD 命中重要** —— JD 里出现一个词太容易了，标题才是岗位定位
  - 标题和 JD 都不含任何 token 的岗位得 0 分，重排后自然沉底
  - 默认只重排**不隐藏**：本地过滤会让"结果少"的观感更强（本来可能就只有十几条），
    所以过滤要另外用开关打开

除本地打分外，本模块还提供 **LLM 复核**（`build_llm_filter_prompt` /
`parse_llm_filter_result`）：token 命中只能证明"字面上出现过"，判断不了语义等价
（搜"Agent开发"时，标题写着"大模型应用工程师"的岗位字面不命中却完全相关）。所以
可以再把整批岗位交给 LLM 复核一遍，只删**明显不相关**的。prompt 与解析都放在这里
（纯函数，好测），真正的模型调用在 api 层。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

# 打分权重
TITLE_WEIGHT = 3.0
DESC_WEIGHT = 1.0
# 英文/数字 token 的最短长度（单个字母噪声太大）
_ASCII_MIN_LEN = 2

_ASCII_RUN = re.compile(r"[a-z0-9+#]+")
_CJK_RUN = re.compile(r"[一-龥]+")


def tokenize(keyword: str) -> List[str]:
    """把关键词拆成用于命中判断的 token。

    英文/数字按整段；中文按 **2-gram** —— 项目里没有中文分词器，2-gram 是足够好的
    近似（"AI应用开发" → ai / 应用 / 用开 / 开发）。宁可多召回，也不要用一个可能
    切错的分词器。单个汉字的关键词（如"医"）直接用它本身，否则会得到空 token、
    整个功能静默失效。
    """
    text = (keyword or "").lower()
    tokens: List[str] = []

    for m in _ASCII_RUN.finditer(text):
        t = m.group(0).strip("+#")
        if len(t) >= _ASCII_MIN_LEN:
            tokens.append(t)

    for m in _CJK_RUN.finditer(text):
        run = m.group(0)
        if len(run) == 1:
            tokens.append(run)
            continue
        for i in range(len(run) - 1):
            tokens.append(run[i : i + 2])

    # 去重但保持顺序
    seen = set()
    out: List[str] = []
    for t in tokens:
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out


def score_job(job: Dict[str, Any], tokens: Sequence[str]) -> Dict[str, Any]:
    """给单个岗位打分。返回各项明细，便于解释和调参。"""
    title = str(job.get("title") or job.get("job_title") or "").lower()
    desc = str(job.get("description") or "").lower()

    title_hits = sum(1 for t in tokens if t in title)
    desc_hits = sum(1 for t in tokens if t in desc)
    return {
        "title_hits": title_hits,
        "desc_hits": desc_hits,
        "title_score": title_hits * TITLE_WEIGHT,
        "desc_score": desc_hits * DESC_WEIGHT,
        "total": title_hits * TITLE_WEIGHT + desc_hits * DESC_WEIGHT,
    }


def rank_jobs(
    jobs: Sequence[Dict[str, Any]],
    keywords,
    min_score: Optional[float] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """按相关性重排（稳定排序，同分保持原顺序）。

    min_score 非空时把总分低于它的岗位过滤掉。
    返回 (重排后的岗位, 统计信息)。
    """
    if isinstance(keywords, str):
        keyword_list = [keywords]
    else:
        keyword_list = [str(k or "") for k in (keywords or [])]

    tokens: List[str] = []
    for kw in keyword_list:
        for t in tokenize(kw):
            if t not in tokens:
                tokens.append(t)

    stats: Dict[str, Any] = {"tokens": tokens, "ranked": False, "dropped": 0}
    if not tokens:
        # 没有可用 token（空关键词等）时原样返回，别假装排过序
        return list(jobs), stats

    scored = [(i, score_job(j, tokens), j) for i, j in enumerate(jobs)]
    scored.sort(key=lambda x: (-x[1]["total"], x[0]))

    kept = [x for x in scored if min_score is None or x[1]["total"] >= min_score]
    stats.update(
        {
            "ranked": True,
            "dropped": len(scored) - len(kept),
            "kept": len(kept),
            "title_matched": sum(1 for x in kept if x[1]["title_hits"]),
            "max_score": scored[0][1]["total"] if scored else 0,
            "min_score": min_score,
        }
    )
    return [x[2] for x in kept], stats


# ══════════════════════════════════════
#  LLM 复核（本地 token 命中的补充）
# ══════════════════════════════════════

# 一次请求最多复核多少条、每条 JD 取多少字。30 条 × 200 字 ≈ 6k 字符，
# 对 deepseek-flash 是很轻松的一次调用；再大就得不偿失（搜索本身只是想看看有没有岗）。
LLM_FILTER_MAX_ITEMS = 40
LLM_FILTER_DESC_CHARS = 200

LLM_FILTER_SYSTEM = (
    "你是招聘岗位筛选助手。用户给出一个搜索关键词和一批岗位，你要挑出**明显不符合**"
    "该关键词的岗位。\n"
    "判断标准：\n"
    "1. 岗位的核心职责/定位与关键词指向的工作**不是一回事** → 判为不符合。"
    "例如搜「AI应用开发」，而岗位是「外贸业务员」（JD 里只提到「用 AI 工具获客」）、"
    "「机修工」「行政专员」，这些都要挑出来。\n"
    "2. 同义、近义、上下位关系都算**符合**。例如搜「Agent开发」，"
    "「大模型应用工程师」「LLM 应用开发」即使标题不含「Agent」也算符合。\n"
    "3. **拿不准的一律算符合**。宁可放过，也不要误删。\n"
    "4. 只根据岗位标题和 JD 判断，不要臆测公司情况。\n"
    "严格只输出 JSON，不要任何解释文字。"
)


def build_llm_filter_prompt(
    keyword: str,
    jobs: Sequence[Dict[str, Any]],
    max_items: int = LLM_FILTER_MAX_ITEMS,
    desc_chars: int = LLM_FILTER_DESC_CHARS,
) -> Tuple[str, str]:
    """构造 LLM 复核的 (system, user)。

    岗位按 1 开始编号 —— 比 0 起始更不容易被模型与"没有"混淆，解析侧也统一按 1-based
    处理。超过 max_items 的岗位直接不送（调用方只送重排后的前 N 条）。
    """
    lines: List[str] = []
    for i, job in enumerate(list(jobs)[:max_items], start=1):
        title = str(job.get("title") or job.get("job_title") or "").strip()
        company = str(job.get("company") or "").strip()
        salary = str(job.get("salary") or "").strip()
        desc = str(job.get("description") or "").replace("\n", " ").strip()
        if len(desc) > desc_chars:
            desc = desc[:desc_chars] + "…"
        head = f"{i}. 岗位：{title}"
        if company:
            head += f" | 公司：{company}"
        if salary:
            head += f" | 薪资：{salary}"
        lines.append(head)
        # JD 为空时不要写一行空的"JD："—— 那会让模型以为信息缺失而倾向于判"符合"
        if desc:
            lines.append(f"   JD：{desc}")

    user = (
        f"搜索关键词：{keyword}\n\n"
        f"待判断岗位共 {len(list(jobs)[:max_items])} 个：\n"
        + "\n".join(lines)
        + "\n\n"
        '请输出 JSON：{"mismatch": [{"index": <岗位编号>, "reason": "<简短理由>"}]}\n'
        "mismatch 只放**明显不符合**的岗位；全符合就返回 {\"mismatch\": []}。"
    )
    return LLM_FILTER_SYSTEM, user


def parse_llm_filter_result(text: str, total: int, max_items: int = LLM_FILTER_MAX_ITEMS) -> List[Dict[str, Any]]:
    """解析 LLM 复核结果，返回 [{"index": <0-based>, "reason": str}]。

    对模型输出保持宽容：``mismatch`` 里既可能是 ``{"index": 3, "reason": "..."}``，
    也可能是裸的 ``3``；编号既可能 1-based 也可能被写成 0-based —— 这里统一按
    **1-based** 解释，落在 ``[1, total]`` 之外的编号一律丢弃（例如模型返回 0 时，
    按 1-based 就是不存在的编号，直接忽略；把它当成 0-based 的第 0 条会误删一个好岗位）。
    解析不出来（非 JSON、total<=0）时返回空列表 —— **失败时一条都不删**。
    """
    if total <= 0:
        return []
    try:
        data = _loads_lax(text)
    except Exception:
        return []
    if isinstance(data, list):  # 模型直接返回了数组，也接受
        data = {"mismatch": data}
    if not isinstance(data, dict):
        return []

    raw_items = data.get("mismatch")
    if raw_items is None:
        raw_items = data.get("mismatch_jobs") or data.get("irrelevant") or []
    if isinstance(raw_items, dict):  # {"3": "理由"} 这种写法
        raw_items = [{"index": k, "reason": v} for k, v in raw_items.items()]
    if not isinstance(raw_items, (list, tuple)):
        return []

    limit = min(total, max_items)
    out: List[Dict[str, Any]] = []
    seen = set()
    for item in raw_items:
        if isinstance(item, dict):
            idx = item.get("index", item.get("id", item.get("编号")))
            reason = str(item.get("reason") or item.get("理由") or "").strip()
        else:
            idx, reason = item, ""
        try:
            n = int(str(idx).strip())
        except (TypeError, ValueError):
            continue
        if n < 1 or n > limit or n in seen:
            continue
        seen.add(n)
        out.append({"index": n - 1, "reason": reason})
    out.sort(key=lambda x: x["index"])
    return out


def _loads_lax(text: str) -> Any:
    """尽量从模型回复里抠出 JSON（兼容 ```json 代码块和前后废话）。"""
    import json

    s = (text or "").strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\s*", "", s)
        s = re.sub(r"\s*```$", "", s)
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass
    # 退而求其次：截取第一个 { 到最后一个 }
    start, end = s.find("{"), s.rfind("}")
    if start != -1 and end > start:
        return json.loads(s[start : end + 1])
    start, end = s.find("["), s.rfind("]")
    if start != -1 and end > start:
        return json.loads(s[start : end + 1])
    raise ValueError("no JSON found")


def drop_by_llm(jobs: Sequence[Dict[str, Any]], drops: Sequence[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """按 LLM 判定丢弃岗位。返回 (保留的岗位, 被丢弃岗位的说明)。

    说明里带上标题/公司/理由，供前端展示"删了什么、为什么"，让过滤可解释、可质疑。
    """
    drop_map = {d["index"]: d.get("reason", "") for d in drops if 0 <= d.get("index", -1) < len(jobs)}
    kept: List[Dict[str, Any]] = []
    dropped: List[Dict[str, Any]] = []
    for i, job in enumerate(jobs):
        if i in drop_map:
            dropped.append(
                {
                    "title": job.get("title") or job.get("job_title") or "",
                    "company": job.get("company") or "",
                    "reason": drop_map[i],
                }
            )
        else:
            kept.append(job)
    return kept, dropped
