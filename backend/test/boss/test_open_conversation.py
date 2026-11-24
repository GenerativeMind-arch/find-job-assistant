"""会话定位逻辑测试（投递后追加定制话术的 P1a）。

核心不变量：
  1. 三级匹配的优先级不能被绕过（HR 名 > 公司+岗位 > 仅公司）
  2. 匹配不到时必须是"放弃"，绝不能退化成"随便挑一个"（旧的盲点第一个会话就是这样发错人的）
  3. 身份校验读不到头部时一律判失败

全部是纯逻辑，不启动浏览器。
"""

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2]
BOSS_DIR = BACKEND_DIR / "boss"
for p in [str(BACKEND_DIR), str(BOSS_DIR)]:
    if p not in sys.path:
        sys.path.insert(0, p)

import boss_automation
from boss_automation import BossAutomation


def _bare_automation() -> BossAutomation:
    """不跑 __init__ 的实例：本文件测的方法都不需要浏览器/数据库。

    page 故意不设 —— 若被测代码试图访问页面，会立刻 AttributeError 暴露出来。
    """
    return BossAutomation.__new__(BossAutomation)


def _card(text: str, hr_name: str = ""):
    # _pick_conversation_card 只读 text；locator 留空即可
    return {"text": text, "hr_name": hr_name, "locator": None}


JOB = {"hr_name": "张小美", "company": "智谱科技", "job_title": "Agent应用开发工程师"}


# ── _norm_text ──


def test_norm_text_strips_all_whitespace():
    assert BossAutomation._norm_text(" 智谱\n科技 \t") == "智谱科技"
    assert BossAutomation._norm_text("") == ""
    assert BossAutomation._norm_text(None) == ""


# ── _pick_conversation_card：优先级 ──


def test_picks_by_hr_name_first():
    a = _bare_automation()
    cards = [
        _card("李大壮\n某公司\nPython开发"),
        _card("张小美\n智谱科技\nAgent应用开发工程师"),
    ]
    picked, matched_by = a._pick_conversation_card(
        cards, JOB["hr_name"], JOB["company"], JOB["job_title"]
    )
    assert matched_by == "hr_name"
    assert picked is cards[1]


def test_hr_name_wins_over_company_match():
    """公司名命中的卡片排在前面时，HR 名仍必须胜出。"""
    a = _bare_automation()
    cards = [
        _card("王五\n智谱科技\n后端开发"),
        _card("张小美\n别的公司\n别的岗位"),
    ]
    picked, matched_by = a._pick_conversation_card(
        cards, JOB["hr_name"], JOB["company"], JOB["job_title"]
    )
    assert matched_by == "hr_name"
    assert picked is cards[1]


def test_falls_back_to_company_plus_title():
    a = _bare_automation()
    cards = [
        _card("王五\n智谱科技\n销售代表"),
        _card("王五\n智谱科技\nAgent应用开发工程师"),
    ]
    picked, matched_by = a._pick_conversation_card(
        cards, JOB["hr_name"], JOB["company"], JOB["job_title"]
    )
    assert matched_by == "company+job_title"
    assert picked is cards[1]


def test_falls_back_to_company_only_when_title_absent():
    """卡片里岗位名被截断时，只靠公司名也能命中，但必须标记为低置信度。"""
    a = _bare_automation()
    cards = [_card("王五\n智谱科技\nAgent应用…")]
    picked, matched_by = a._pick_conversation_card(
        cards, JOB["hr_name"], JOB["company"], JOB["job_title"]
    )
    assert matched_by == "company_only"
    assert picked is cards[0]


def test_no_match_returns_none_never_guesses():
    """关键不变量：都不命中时必须返回 None，不能退化成"挑第一个"。"""
    a = _bare_automation()
    cards = [_card("王五\n完全无关公司\n销售代表"), _card("赵六\n另一家公司\n会计")]
    picked, matched_by = a._pick_conversation_card(
        cards, JOB["hr_name"], JOB["company"], JOB["job_title"]
    )
    assert picked is None
    assert matched_by == ""


def test_single_char_hr_name_is_not_used_for_matching():
    """单字名字匹配噪声太大，应被忽略并降级到公司名匹配。"""
    a = _bare_automation()
    cards = [_card("张\n智谱科技\nAgent应用开发工程师")]
    picked, matched_by = a._pick_conversation_card(cards, "张", JOB["company"], JOB["job_title"])
    assert matched_by == "company+job_title"
    assert picked is cards[0]


def test_empty_cards_returns_none():
    a = _bare_automation()
    assert a._pick_conversation_card([], JOB["hr_name"], JOB["company"], JOB["job_title"]) == (
        None,
        "",
    )


# ── _header_matches：身份校验 ──


def test_header_matches_on_hr_name_or_company():
    a = _bare_automation()
    assert a._header_matches("张小美 智谱科技", "张小美", "智谱科技") is True
    assert a._header_matches("张小美", "张小美", "智谱科技") is True
    assert a._header_matches("智谱科技", "张小美", "智谱科技") is True


def test_header_matches_rejects_empty_and_unrelated():
    a = _bare_automation()
    # 读不到头部 → 一律判失败（宁可不发）
    assert a._header_matches("", "张小美", "智谱科技") is False
    assert a._header_matches("   \n ", "张小美", "智谱科技") is False
    assert a._header_matches("完全无关公司", "张小美", "智谱科技") is False


# ── _open_conversation_for_job：缺信息必须早退，且不碰页面 ──


def test_missing_identity_info_aborts_without_touching_page():
    """hr_name/company 都没有时必须直接返回，不能去点任何东西。

    这里 page 根本没设 —— 只要代码走到页面操作就会 AttributeError。
    """
    a = _bare_automation()
    result = a._open_conversation_for_job({"hr_name": "", "company": "", "job_title": "某岗位"})
    assert result["opened"] is False
    assert result["matched_by"] == ""
    assert "无法定位会话" in result["reason"]


def test_result_shape_is_stable():
    a = _bare_automation()
    result = a._open_conversation_for_job({"hr_name": "", "company": ""})
    assert set(result) == {"opened", "matched_by", "header", "expected", "reason"}
    assert set(result["expected"]) == {"hr_name", "company", "job_title"}


# ── 第二条（定制）话术 ──


def _fake_settings(monkeypatch, **overrides):
    """把 get_setting 换成内存表，测试不碰数据库。"""
    table = {
        "greeting_template": "您好，我对贵公司的{job_title}岗位很感兴趣。",
        "greeting_mode": "template",
        "custom_greeting_enabled": "false",
        "custom_greeting_max_len": "120",
        "custom_greeting_delay_min_sec": "4",
        "custom_greeting_delay_max_sec": "8",
    }
    table.update(overrides)
    monkeypatch.setattr(boss_automation, "get_setting", lambda k, d=None: table.get(k, d))
    return table


JOB_RECORD = {"hr_name": "张三", "company": "智谱科技", "job_title": "Agent应用开发工程师"}


def test_custom_greeting_disabled_never_touches_page(monkeypatch):
    """开关关着时必须直接返回：这里 page 根本没设，一碰页面就 AttributeError。"""
    _fake_settings(monkeypatch, custom_greeting_enabled="false")
    a = _bare_automation()
    result = a._send_custom_greeting_for_job("https://x", "你好", "岗位", "", JOB_RECORD)
    assert result["sent"] is False
    assert result["need_manual"] is False
    # skipped 让上层能把"开关关着"和"发了但失败"区分开（前者不该提示"未发出"）
    assert result["skipped"] is True
    assert "custom_greeting_enabled" in result["reason"]


def test_build_custom_greeting_keeps_caller_text(monkeypatch):
    """调用方传了话术（如 /jobs/apply 带优化建议的版本）就原样用，不套模板、不截断。"""
    _fake_settings(monkeypatch, greeting_template="模板不该被用到")
    a = _bare_automation()
    text = a._build_custom_greeting("这是调用方给的话术", "岗位名", "JD", JOB_RECORD)
    assert text == "这是调用方给的话术"


def test_build_custom_greeting_fills_template_placeholders(monkeypatch):
    _fake_settings(monkeypatch)
    a = _bare_automation()
    text = a._build_custom_greeting("", "", "", JOB_RECORD)
    assert "Agent应用开发工程师" in text
    assert "{job_title}" not in text


def test_build_custom_greeting_caps_length(monkeypatch):
    _fake_settings(monkeypatch, custom_greeting_max_len="10")
    a = _bare_automation()
    text = a._build_custom_greeting("一二三四五六七八九十十一十二十三", "", "", JOB_RECORD)
    assert len(text) == 10


def test_build_custom_greeting_zero_max_len_means_no_cap(monkeypatch):
    _fake_settings(monkeypatch, custom_greeting_max_len="0")
    a = _bare_automation()
    raw = "一二三四五六七八九十十一十二十三"
    assert a._build_custom_greeting(raw, "", "", JOB_RECORD) == raw


def test_build_custom_greeting_ignores_bad_max_len(monkeypatch):
    """配置被写坏（非数字）时按默认值处理，不能抛异常打断投递。"""
    _fake_settings(monkeypatch, custom_greeting_max_len="abc")
    a = _bare_automation()
    text = a._build_custom_greeting("短话术", "", "", JOB_RECORD)
    assert text == "短话术"


# ── 风控关键词诊断 ──


def test_safety_snippet_returns_context_around_keyword():
    """命中关键词时要能拿到上下文 —— 判"真风控"还是"误报"全靠这段原文。"""
    body = "a" * 80 + "账号异常" + "b" * 80
    snippet = BossAutomation._safety_snippet(body, "账号异常", radius=10)
    assert "账号异常" in snippet
    assert snippet == "a" * 10 + "账号异常" + "b" * 10


def test_safety_snippet_clamps_at_page_start():
    snippet = BossAutomation._safety_snippet("违规内容在此", "违规", radius=60)
    assert snippet.startswith("违规")


def test_safety_snippet_empty_when_keyword_absent():
    assert BossAutomation._safety_snippet("页面正文", "冻结") == ""


def test_safety_snippet_collapses_newlines():
    """片段要能单行打印进日志，换行会打乱日志结构。"""
    snippet = BossAutomation._safety_snippet("上文\n违规\n下文", "违规", radius=20)
    assert "\n" not in snippet


# 实测抓到的岗位详情页尾部原文（2026-10-10，英科医疗科技 ai算法工程师）。
# 这段免责声明每个详情页都有，里面的"违规"曾让每次投递都被误判成账号封禁。
REAL_JOB_PAGE_TAIL = """一般
良好
优秀
极好
boss 安全提示

boss直聘严禁用人单位和招聘者用户做出任何损害求职者合法权益的违法违规行为，包括但不限于扣押求职者证件、收取求职者财物、向求职者集资、让求职者入股、诱导求职者异地入职、异地参加培训、违法违规"""


def test_boilerplate_disclaimer_is_stripped():
    """回归：真实的岗位详情页免责声明必须被摘掉，否则"违规"永远命中。"""
    a = _bare_automation()
    cleaned = a._strip_safety_boilerplate(REAL_JOB_PAGE_TAIL.lower())
    assert "违规" not in cleaned
    assert "boss安全提示" not in cleaned.replace(" ", "")


def test_boilerplate_matching_ignores_inner_whitespace():
    """页面上的写法可能是空格或换行，直接子串匹配会漏。"""
    a = _bare_automation()
    for variant in ("BOSS 安全提示\n正文", "boss\n安全提示\n正文", "boss　安全提示\n正文"):
        cleaned = a._strip_safety_boilerplate(variant.lower())
        assert "安全提示" not in cleaned


def test_real_ban_notice_survives_stripping():
    """真正的封号提示不能被误摘 —— 否则安全检查就形同虚设。"""
    a = _bare_automation()
    for body in ("您的账号存在违规行为", "账号异常，已被冻结", "该账号被限制使用"):
        cleaned = a._strip_safety_boilerplate(body.lower())
        assert cleaned == body.lower()


def test_stripping_keeps_unrelated_paragraphs():
    a = _bare_automation()
    body = "岗位职责\n负责模型训练\n任职要求\n本科以上"
    assert a._strip_safety_boilerplate(body) == body


# ── 已投递判定（详情页按钮） ──


class _FakeEvalPage:
    def __init__(self, ret=None, boom=False):
        self._ret = ret
        self._boom = boom

    def evaluate(self, _js, _arg=None):
        if self._boom:
            raise RuntimeError("页面炸了")
        return self._ret


def test_already_applied_reads_button_state():
    a = _bare_automation()
    a.page = _FakeEvalPage(ret=True)
    assert a._is_already_applied_on_detail_page() is True
    a.page = _FakeEvalPage(ret=False)
    assert a._is_already_applied_on_detail_page() is False


def test_already_applied_never_raises():
    """判定失败必须退化成"未投递"，不能把异常抛进投递流程。

    实测：详情页上「已沟通」从不出现，判据是 .btn-startchat 按钮的文本
    （已投递=继续沟通 / 未投递=立即沟通）。原先扫整个 body 会误判。
    """
    a = _bare_automation()
    a.page = _FakeEvalPage(boom=True)
    assert a._is_already_applied_on_detail_page() is False
