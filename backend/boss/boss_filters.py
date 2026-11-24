"""BOSS 直聘搜索筛选项（经验/薪资/学历/规模/融资/职位类型）编码解析。

数据来源：BOSS 公开接口（参考 boss_geo.py 的做法）

    https://www.zhipin.com/wapi/zpgeek/pc/all/filter/conditions.json

该接口**免登录**即可访问，因此服务端直接 httpx 拉取、进程内缓存 6 小时——
不需要浏览器，前端下拉在「未启动浏览器」时也能正常显示。

返回结构（实测）：
    zpData.experienceList / salaryList / degreeList / scaleList / stageList /
    jobTypeList / payTypeList / partTimeList，每项为 [{code, name}, ...]。

接口不可用时回退 `STATIC_CONDITIONS`（下表编码取自实测响应，非猜测）。
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

_CONDITION_URL = "https://www.zhipin.com/wapi/zpgeek/pc/all/filter/conditions.json"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.zhipin.com/web/geek/job",
}

# 接口字段名 → 统一维度名（搜索 XHR 里用的字段名）
_KEY_MAP: Dict[str, str] = {
    "experienceList": "experience",
    "salaryList": "salary",
    "degreeList": "degree",
    "scaleList": "scale",
    "stageList": "stage",
    "jobTypeList": "jobType",
    "payTypeList": "payType",
}

# 兜底表：编码取自实测 conditions.json 响应
STATIC_CONDITIONS: Dict[str, List[Dict[str, str]]] = {
    "experience": [
        {"name": "在校生", "code": "108"}, {"name": "应届生", "code": "102"},
        {"name": "经验不限", "code": "101"}, {"name": "1年以内", "code": "103"},
        {"name": "1-3年", "code": "104"}, {"name": "3-5年", "code": "105"},
        {"name": "5-10年", "code": "106"}, {"name": "10年以上", "code": "107"},
    ],
    "salary": [
        {"name": "3K以下", "code": "402"}, {"name": "3-5K", "code": "403"},
        {"name": "5-10K", "code": "404"}, {"name": "10-20K", "code": "405"},
        {"name": "20-50K", "code": "406"}, {"name": "50K以上", "code": "407"},
    ],
    "degree": [
        {"name": "初中及以下", "code": "209"}, {"name": "中专/中技", "code": "208"},
        {"name": "高中", "code": "206"}, {"name": "大专", "code": "202"},
        {"name": "本科", "code": "203"}, {"name": "硕士", "code": "204"},
        {"name": "博士", "code": "205"},
    ],
    "scale": [
        {"name": "0-20人", "code": "301"}, {"name": "20-99人", "code": "302"},
        {"name": "100-499人", "code": "303"}, {"name": "500-999人", "code": "304"},
        {"name": "1000-9999人", "code": "305"}, {"name": "10000人以上", "code": "306"},
    ],
    "stage": [
        {"name": "未融资", "code": "801"}, {"name": "天使轮", "code": "802"},
        {"name": "A轮", "code": "803"}, {"name": "B轮", "code": "804"},
        {"name": "C轮", "code": "805"}, {"name": "D轮及以上", "code": "806"},
        {"name": "已上市", "code": "807"}, {"name": "不需要融资", "code": "808"},
    ],
    "jobType": [{"name": "全职", "code": "1901"}, {"name": "兼职", "code": "1903"}],
}

_cache: Dict[str, Any] = {"ts": 0.0, "dims": {}, "ttl_sec": 6 * 3600}

def _fetch_remote() -> Dict[str, List[Dict[str, str]]]:
    """服务端直连 conditions.json，按实测字段名解析。"""
    try:
        import httpx

        resp = httpx.get(_CONDITION_URL, headers=_HEADERS, timeout=15)
        resp.raise_for_status()
        payload = resp.json()
    except Exception as exc:  # noqa: BLE001 — 网络/解析失败一律回退静态表
        print(f"  [筛选] 条件接口不可用，使用内置兜底表: {exc}")
        return {}

    if not isinstance(payload, dict) or payload.get("code") != 0:
        return {}

    zp = payload.get("zpData") or {}
    dims: Dict[str, List[Dict[str, str]]] = {}
    for key, dim in _KEY_MAP.items():
        rows = zp.get(key)
        if not isinstance(rows, list):
            continue
        items = [
            {"name": str(r["name"]), "code": str(r["code"])}
            for r in rows
            if isinstance(r, dict) and r.get("name") is not None and r.get("code") is not None
        ]
        # code 0 / 名称"不限" 由调用方统一跳过，这里保留原始数据
        if items:
            dims[dim] = items
    return dims

def get_conditions(force: bool = False) -> Dict[str, List[Dict[str, str]]]:
    """返回 {dimension: [{name, code}]}，线上优先，失败回退静态表，进程内缓存。"""
    now = time.time()
    if not force and _cache["dims"] and now - _cache["ts"] < _cache["ttl_sec"]:
        return _cache["dims"]

    dims = _fetch_remote() or {k: list(v) for k, v in STATIC_CONDITIONS.items()}
    _cache["dims"] = dims
    _cache["ts"] = now
    return dims

# 表示"不限"的标签 → 视为不传该筛选
_ANY_LABELS = {"不限", "经验不限", "学历不限", "薪资不限", "全部", "any", "all"}

def resolve_filters(requested: Dict[str, Any]) -> Dict[str, str]:
    """把 {dimension: 标签或编码(可多个)} 解析为 {dimension: "code,code"}。

    解析不出的维度直接省略（视为未筛选）并打印告警——不会发送猜测的编码。
    """
    dims = get_conditions()
    resolved: Dict[str, str] = {}

    for dim, values in (requested or {}).items():
        if not values:
            continue
        items = values if isinstance(values, (list, tuple, set)) else str(values).split(",")
        items = [str(v).strip() for v in items if str(v).strip()]
        if not items or all(v in _ANY_LABELS for v in items):
            continue

        by_name = {row["name"]: row["code"] for row in dims.get(dim) or []}
        codes: List[str] = []
        for v in items:
            if v.isdigit():
                if v not in codes:
                    codes.append(v)
                continue
            code = by_name.get(v)
            if code and code not in codes:
                codes.append(code)

        if codes:
            resolved[dim] = ",".join(codes)
        else:
            print(f"  [筛选] 无法解析 {dim}={items}，已跳过该筛选")

    return resolved

def list_options() -> Dict[str, List[str]]:
    """返回各维度可选项的名称列表（去掉"不限"），供前端渲染下拉。"""
    dims = get_conditions()
    return {
        dim: [r["name"] for r in rows if r["name"] not in _ANY_LABELS]
        for dim, rows in dims.items()
    }
