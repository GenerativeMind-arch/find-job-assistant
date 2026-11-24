"""本地相关性打分测试。

核心不变量：
  1. 标题命中远重于 JD 命中（JD 里出现一个词太容易）
  2. 重排是稳定排序 —— 同分岗位必须保持 BOSS 的原始顺序，不能随机打乱
  3. 关键词拆不出 token 时（空串）必须原样返回，不能假装排过序
  4. 用户实际报过的噪声案例："原料外销业务员"（JD 里提过 AI）不得排在前面
"""

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2]
BOSS_DIR = BACKEND_DIR / "boss"
for p in [str(BACKEND_DIR), str(BOSS_DIR)]:
    if p not in sys.path:
        sys.path.insert(0, p)

from boss_relevance import DESC_WEIGHT, TITLE_WEIGHT, rank_jobs, score_job, tokenize


# ── 分词 ──


def test_tokenize_mixed_ascii_and_cjk():
    assert tokenize("AI开发") == ["ai", "开发"]


def test_tokenize_produces_cjk_bigrams():
    # 2-gram：宁可多召回，也不引入一个可能切错的分词器
    assert tokenize("AI应用开发") == ["ai", "应用", "用开", "开发"]


def test_tokenize_ascii_only():
    assert tokenize("Python") == ["python"]


def test_tokenize_single_cjk_char_still_usable():
    """单字关键词若被丢弃，整个重排会静默失效。"""
    assert tokenize("医") == ["医"]


def test_tokenize_empty_and_noise():
    assert tokenize("") == []
    assert tokenize("   ") == []
    # 单个字母噪声太大，丢掉
    assert tokenize("a") == []


def test_tokenize_dedupes_preserving_order():
    assert tokenize("开发开发") == ["开发", "发开"]


# ── 打分 ──


def test_title_hit_outweighs_desc_hit():
    tokens = tokenize("AI开发")
    title_hit = score_job({"title": "AI开发工程师", "description": ""}, tokens)
    desc_hit = score_job({"title": "外贸业务员", "description": "用 AI 工具获客"}, tokens)
    assert title_hit["total"] > desc_hit["total"]
    assert desc_hit["total"] == DESC_WEIGHT  # 只命中 ai
    assert title_hit["title_hits"] == 2  # ai + 开发


def test_no_hit_scores_zero():
    tokens = tokenize("AI开发")
    s = score_job({"title": "原料外销业务员", "description": "负责海外客户维护"}, tokens)
    assert s["total"] == 0
    assert s["title_hits"] == 0 and s["desc_hits"] == 0


def test_score_reads_job_title_alias():
    """搜索结果里标题字段叫 title，库里叫 job_title，两个都要认。"""
    tokens = tokenize("AI开发")
    assert score_job({"job_title": "AI开发"}, tokens)["total"] == 2 * TITLE_WEIGHT


def test_score_tolerates_missing_fields():
    assert score_job({}, tokenize("AI"))["total"] == 0


# ── 重排 ──


def _job(title, desc=""):
    return {"title": title, "description": desc}


def test_rank_puts_best_first_and_is_stable():
    jobs = [
        _job("外贸业务员", "用 AI 工具做海外获客"),  # 1 分
        _job("AI开发工程师"),                        # 6 分
        _job("后端开发工程师"),                      # 3 分（只命中 开发）
        _job("AI开发实习生"),                        # 6 分，与上面同分
    ]
    ranked, stats = rank_jobs(jobs, "AI开发")
    assert [j["title"] for j in ranked] == [
        "AI开发工程师",
        "AI开发实习生",   # 同分保持原始相对顺序
        "后端开发工程师",
        "外贸业务员",
    ]
    assert stats["ranked"] is True
    assert stats["title_matched"] == 3


def test_rank_does_not_drop_by_default():
    jobs = [_job("AI开发"), _job("原料外销业务员", "用 AI 工具")]
    ranked, stats = rank_jobs(jobs, "AI开发")
    assert len(ranked) == 2
    assert stats["dropped"] == 0


def test_rank_filters_when_min_score_given():
    """用户实际报过的噪声：只在 JD 里提过 AI 的业务员岗位，阈值 3 分应被丢掉。"""
    jobs = [
        _job("AI开发工程师"),
        _job("原料外销业务员", "用 AI 工具做海外获客"),
    ]
    ranked, stats = rank_jobs(jobs, "AI开发", min_score=3)
    assert [j["title"] for j in ranked] == ["AI开发工程师"]
    assert stats["dropped"] == 1


def test_rank_with_no_tokens_returns_input_unchanged():
    jobs = [_job("A"), _job("B")]
    ranked, stats = rank_jobs(jobs, "")
    assert ranked == jobs
    assert stats["ranked"] is False


def test_rank_accepts_keyword_list():
    jobs = [_job("Agent开发"), _job("外贸业务员")]
    ranked, stats = rank_jobs(jobs, ["AI", "Agent开发"])
    assert ranked[0]["title"] == "Agent开发"
    assert "agent" in stats["tokens"]


def test_rank_empty_input():
    ranked, stats = rank_jobs([], "AI开发")
    assert ranked == []
    assert stats["ranked"] is True
