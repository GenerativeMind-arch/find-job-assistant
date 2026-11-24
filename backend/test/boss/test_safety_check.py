"""页面风控判定的误报/漏报。

真实事故（2026-10-12 实测）：投递第 2 个岗位时被判成 captcha 并冷却 600s，
证据是 JD 正文里的这几个字 ——

    参与 rag 相关开发 …… 检索链路优化及效果验证。

「验证」原先被列在 captcha 关键词里，而它是个极其常见的词。安全判定应该盯
**页面框架**（URL 跳转、页面级提示），而不是招聘方自由撰写的 JD 正文。
"""

import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[2]
BOSS_DIR = BACKEND_DIR / "boss"
for p in [str(BACKEND_DIR), str(BOSS_DIR)]:
    if p not in sys.path:
        sys.path.insert(0, p)

from boss_automation import BossAutomation


@pytest.fixture(autouse=True)
def _isolated_db(temp_db):
    """本文件的用例走的是真实 `inspect_page_safety`，而它命中风控时会调用
    `_trigger_cooldown` → `report_apply_risk` → **写今日预算状态**。

    不隔离的话，跑一次测试就会把真实库当天那条 apply_budget_state 打成
    `{"budget": 0, "strikes": 10}` —— 用户当天直接投不出去了（真发生过一次）。
    """
    return temp_db

REAL_JD = (
    "岗位职责：\n"
    "参与 rag 相关开发，包括数据清洗、文档切分、向量化、向量数据库索引构建、\n"
    "检索链路优化及效果验证。协助接入主流大模型 api，完成接口联调、上下文管理、\n"
    "工具调用、agent 流程编排及业务场景落地。"
)
DISCLAIMER = (
    "BOSS直聘严禁用人单位和招聘者用户做出任何损害求职者合法权益的违法违规行为，"
    "包括但不限于扣押求职者证件、收取求职者财物、向求职者集资、让求职者入股等，"
    "求职者若发现此类行为，请立即举报。"
)


class FakePage:
    def __init__(self, body: str, url: str = "https://www.zhipin.com/job_detail/x/", jd: str = ""):
        self._body = body
        self.url = url
        self._jd = jd

    def inner_text(self, _sel):
        return self._body

    def evaluate(self, _js):
        return self._jd


def _automation(body: str, url: str = "https://www.zhipin.com/job_detail/x/", jd: str = ""):
    """不启动浏览器、不碰数据库的 BossAutomation。

    用 __new__ 绕开 __init__（它会 init_db 并准备 Playwright），只装安全判定用到
    的那几个属性。
    """
    a = BossAutomation.__new__(BossAutomation)
    a.page = FakePage(body, url, jd)
    a._cooldown_until = 0.0
    a._risk_strikes = 0
    a._login_prompt_visible = lambda: False
    return a


# ── 误报 ──


def test_jd_containing_yanzheng_is_not_captcha():
    """JD 里出现「验证」不能判成验证码 —— 这是实测踩到的原故障。"""
    body = f"职位描述\n{REAL_JD}\n公司介绍\n某科技公司"
    a = _automation(body, jd=REAL_JD)

    result = a.inspect_page_safety()

    assert result["ok"] is True, f"正常岗位被误判: {result}"
    assert a.in_cooldown() is False, "误判会白白冷却 10 分钟，把后续投递全锁死"


def test_jd_containing_xianzhi_and_weigui_is_not_banned():
    """JD 里出现「限制」「违规」同理 —— 模板里就爱写「不限制学历」。"""
    jd = "任职要求：\n学历不限制，经验不限。\n严禁任何违规操作。"
    a = _automation(f"职位描述\n{jd}", jd=jd)

    assert a.inspect_page_safety()["ok"] is True


def test_boss_disclaimer_is_still_stripped():
    """回归：BOSS 自己的免责声明里有「违规」，必须仍然被摘掉。"""
    body = f"{DISCLAIMER}\n职位描述\n{REAL_JD}"
    a = _automation(body, jd=REAL_JD)

    assert a.inspect_page_safety()["ok"] is True


# ── 真风控仍要抓住 ──


def test_security_redirect_url_is_captcha():
    """被风控拦截的真实形态是整页跳到 security.html —— URL 是最可靠的信号。"""
    a = _automation(
        "请完成安全验证",
        url="https://www.zhipin.com/web/passport/zp/security.html?code=37&seed=abc",
    )

    result = a.inspect_page_safety()

    assert result["ok"] is False
    assert result["category"] == "captcha"
    assert result["keyword"] == "url"
    assert a.in_cooldown() is True


def test_captcha_widget_text_is_still_detected():
    """页面框架里真的出现验证码提示时仍要命中（只是不再用裸的「验证」）。"""
    a = _automation("为保证账号安全，请拖动滑块完成验证")

    result = a.inspect_page_safety()

    assert result["ok"] is False
    assert result["category"] == "captcha"


def test_banned_and_rate_limit_still_detected():
    assert _automation("您的账号异常，已被限制使用").inspect_page_safety()["category"] == "banned"
    assert _automation("操作太频繁，请稍后再试").inspect_page_safety()["category"] == "rate_limit"


def test_evidence_is_reported():
    """判定必须带证据（关键词/片段/URL），否则误报和真封号分不出来。"""
    result = _automation("请拖动滑块完成验证").inspect_page_safety()

    assert result["keyword"] == "拖动滑块"
    assert result["snippet"]
    assert result["url"]


def test_strip_job_description_keeps_page_frame_text():
    """只摘 JD，页面框架里的风控提示不能一起被摘掉。"""
    jd = "负责效果验证与数据核验"
    a = _automation(f"账号异常\n{jd}", jd=jd)

    stripped = a._strip_job_description(f"账号异常\n{jd}")
    assert "账号异常" in stripped
    assert "效果验证" not in stripped
