"""LLM 相关性复核的纯逻辑测试（prompt 构造 + 结果解析 + 删除）。

核心不变量：
  1. 解析失败 / 返回垃圾时**一条都不删** —— 宁可放过，不可误删
  2. 编号一律按 1-based 解释，越界编号（含 0）直接丢弃，不能当成 0-based 的第 0 条
  3. 编号只允许落在本次**实际送审**的范围内（超过 max_items 尾部不送）
  4. drop_by_llm 保留原顺序，并带出"删了什么、为什么"
"""

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2]
BOSS_DIR = BACKEND_DIR / "boss"
for p in [str(BACKEND_DIR), str(BOSS_DIR)]:
    if p not in sys.path:
        sys.path.insert(0, p)

from boss_relevance import (
    build_llm_filter_prompt,
    drop_by_llm,
    parse_llm_filter_result,
)


def _jobs(n: int):
    return [{"title": f"岗位{i+1}", "company": f"公司{i+1}", "description": "JD内容"} for i in range(n)]


# ── prompt 构造 ──


def test_prompt_numbers_jobs_from_one():
    system, user = build_llm_filter_prompt("AI应用开发", _jobs(3))
    assert "1. 岗位：岗位1" in user
    assert "3. 岗位：岗位3" in user
    assert "搜索关键词：AI应用开发" in user
    assert "JSON" in user


def test_prompt_truncates_long_description():
    job = [{"title": "T", "description": "x" * 500}]
    _, user = build_llm_filter_prompt("AI", job, desc_chars=20)
    assert "x" * 21 not in user  # 截断后不该出现超长片段
    assert "…" in user


def test_prompt_omits_empty_description_line():
    """JD 为空时不要写一行空的“JD：”，否则模型会误以为信息缺失。"""
    _, user = build_llm_filter_prompt("AI", [{"title": "T"}] * 2)
    assert "JD：\n" not in user and not user.rstrip().endswith("JD：")


def test_prompt_caps_items():
    _, user = build_llm_filter_prompt("AI", _jobs(10), max_items=3)
    assert "6. 岗位：岗位6" not in user
    assert "3. 岗位：岗位3" in user


# ── 结果解析 ──


def test_parse_accepts_object_items():
    text = '{"mismatch": [{"index": 2, "reason": "外贸业务员"}]}'
    assert parse_llm_filter_result(text, 5) == [{"index": 1, "reason": "外贸业务员"}]


def test_parse_accepts_bare_int_items():
    assert parse_llm_filter_result('{"mismatch": [3]}', 5) == [{"index": 2, "reason": ""}]


def test_parse_accepts_dict_form():
    assert parse_llm_filter_result('{"mismatch": {"4": "机修工"}}', 5) == [
        {"index": 3, "reason": "机修工"}
    ]


def test_parse_tolerates_code_fences_and_prose():
    text = '```json\n{"mismatch": [1]}\n```'
    assert parse_llm_filter_result(text, 3) == [{"index": 0, "reason": ""}]
    text2 = '好的，结果如下：{"mismatch": [2]} 完毕'
    assert parse_llm_filter_result(text2, 3) == [{"index": 1, "reason": ""}]


def test_parse_rejects_index_zero():
    """0 按 1-based 是不存在的编号。若当成 0-based 就会误删第一条。"""
    assert parse_llm_filter_result('{"mismatch": [0]}', 5) == []


def test_parse_rejects_out_of_range():
    assert parse_llm_filter_result('{"mismatch": [99, -1]}', 5) == []


def test_parse_clamps_to_reviewed_count():
    """只送了前 3 条时，第 5 条不在审范围内，不能删（否则删错岗位）。"""
    assert parse_llm_filter_result('{"mismatch": [5]}', 3, max_items=3) == []


def test_parse_dedupes_and_sorts():
    text = '{"mismatch": [3, 1, 3]}'
    assert [d["index"] for d in parse_llm_filter_result(text, 5)] == [0, 2]


def test_parse_garbage_returns_empty():
    for bad in ["", "我不知道", "null", "{不是 json}", '{"mismatch": "全部删除"}']:
        assert parse_llm_filter_result(bad, 5) == []


def test_parse_zero_total_returns_empty():
    assert parse_llm_filter_result('{"mismatch": [1]}', 0) == []


# ── 删除 ──


def test_drop_keeps_order_and_reports_reason():
    jobs = _jobs(4)
    kept, dropped = drop_by_llm(jobs, [{"index": 1, "reason": "外贸业务员"}])
    assert [j["title"] for j in kept] == ["岗位1", "岗位3", "岗位4"]
    assert dropped == [{"title": "岗位2", "company": "公司2", "reason": "外贸业务员"}]


def test_drop_nothing_when_no_drops():
    jobs = _jobs(3)
    kept, dropped = drop_by_llm(jobs, [])
    assert kept == jobs and dropped == []
