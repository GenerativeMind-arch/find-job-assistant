#!/usr/bin/env python3
"""
BossAutomation — 继承 BossScraper，增加点击/输入/聊天等交互能力。
"""

import json
import random
import re
import time
from concurrent.futures import Future
from typing import Optional, List, Dict, Any, Callable
from urllib.parse import urljoin, urlsplit

from playwright.sync_api import Locator

from boss_firefox import BossScraper, pause, decode_salary
from boss_state import (
    init_db,
    add_application,
    get_application_by_url,
    update_application_status,
    get_setting,
    get_today_application_count,
    get_apply_allowance,
    report_apply_success,
    report_apply_risk,
    get_or_create_conversation,
    get_conversation,
    add_message,
    get_messages,
    get_recent_messages,
    replace_conversation_messages,
    message_exists,
    update_conversation_last_message,
    update_conversation_status,
    update_conversation_interest,
    update_conversation_wechat,
    update_conversation_transfer_requested,
    increment_daily_stat,
    get_today_auto_reply_count,
    find_conversation_by_hr_name,
    get_daily_stats,
)


def auto_reply_allowed() -> bool:
    """「自动回复」总开关的最后一道闸 —— 每次都实时读持久化的 auto_reply_enabled。

    自动回复链路上**任何"即将真的发出一条消息"的位置**都必须过这道闸。
    只在周期开头查一次是不够的：LLM 生成回复要数秒，之后还有 _human_pace
    的 5~15 秒拟人停顿，用户完全可能在这段窗口里点下「停止回复」——
    那样开关读的是旧值，消息照样发出去，表现就是"设了停止却没真停"。

    读设置失败时返回 False（宁可不发，也不要在关掉之后漏发）。
    """
    try:
        return get_setting("auto_reply_enabled", "false") == "true"
    except Exception:
        return False


def scan_should_yield(automation) -> bool:
    """扫描该不该让路给前台操作（手动发送/搜索/投递）—— 见 BossAutomation.should_yield。

    扫描整轮握着浏览器锁，前台操作得等它跑完；一轮最多 3 个会话外加 DB 兜底，
    每个会话都含 LLM 生成和 5~15 秒拟人停顿，动辄几分钟。所以每处理完一个会话
    就查一次，有人在等就提前收尾，把前台等待压到一个会话以内。

    钩子由 BossManager 在启动浏览器时挂上；没挂（CLI 直建实例、测试桩）时
    恒为 False，行为跟以前一致。钩子自己抛异常也当"没人等"，绝不能因为
    一个诊断钩子把扫描搞崩。
    """
    hook = getattr(automation, "should_yield", None)
    if hook is None:
        return False
    try:
        return bool(hook())
    except Exception:
        return False


def sent_text_visible(page, needle: str) -> bool:
    """刚发出的内容是否已经出现在消息区（page 为 Playwright Page 或等价桩）。

    先只看右栏最后一条消息气泡，没命中再退回整页文本。

    为什么要分两步：`page.inner_text("body")` 会把左侧会话列表 + 全部历史消息
    一起序列化，是发送路径上最贵的一次 DOM 调用（几百 ms 起），而每次发送都要
    校验一次。但**不能**只信窄选择器 —— 万一 BOSS 改版导致气泡选择器失配，
    发送明明成功却会被判成失败，进而触发"清空输入框"，把已经发出去的消息
    当成没发。所以窄查询只当快路径，兜底保持原样。
    """
    if not needle:
        return False
    try:
        loc = page.locator(
            ".message-content li.message-item, "
            ".message-content li[class*='message-item']"
        ).last
        if loc.count() > 0 and needle in (loc.inner_text() or ""):
            return True
    except Exception:
        pass
    return needle in page.inner_text("body")


# ── 选择器配置（BOSS UI 改版时只改这里，也可通过设置表覆盖）──
SELECTORS = {
    "apply_button": [
        'button:has-text("立即沟通")',
        'a:has-text("立即沟通")',
        '[class*="btn-chat"]',
        '[class*="start-chat"]',
        'span:has-text("立即沟通")',
        'div:has-text("立即沟通")',
    ],
    "chat_input": [
        "#chat-input",
        'div[contenteditable="true"]',
        '[class*="chat-input"]',
        '[placeholder*="请输入"]',
    ],
    "chat_send_button": [
        'button[type="send"]',
        ".btn-send",
        'button:has-text("发送")',
        'button[class*="send"]',
    ],
    "conversation_items": [
        'li[role="listitem"]',
        ".friend-content",
        '[class*="chat-item"]',
    ],
    "message_items_in_chat": [
        "li.message-item",
        'li[class*="message-item"]',
        '[class*="message-item"]',
    ],
    "unread_badge": [
        '[class*="unread"]',
        '[class*="badge"]',
        ".red-dot",
    ],
    "greeting_dialog_close": [
        'button[class*="close"]',
        '[class*="dialog-close"]',
        'span:has-text("×")',
        '[class*="modal-close"]',
        'svg[class*="close"]',
    ],
    "resume_attach_btn": [
        'div.toolbar-btn:has-text("发简历")',
        'div:has-text("发简历")',
        'button:has-text("发简历")',
        'span:has-text("发简历")',
    ],
    "resume_confirm_btn": [
        ".btn-sure-v2.btn-confirm",
        ".choose-resume-dialog .btn-confirm",
        'button:has-text("发送")',
        '.boss-popup__content button:has-text("发送")',
    ],
    "wechat_share_btn": [
        ".btn-weixin",
        'div:has-text("换微信")',
        'span:has-text("换微信")',
        '[class*="btn-weixin"]',
    ],
    "phone_share_btn": [
        ".btn-contact",
        'div:has-text("换电话")',
        'span:has-text("换电话")',
        '[class*="btn-contact"]',
    ],
    "back_to_list": [
        '[class*="back"]',
        'span:has-text("返回")',
        'button:has-text("返回")',
        'a[href*="/chat"]',
    ],
    "company_similar_jobs_link": [
        'a[href*="/gongsi/job/"]',
        'a:has-text("查看该公司其他职位")',
        'a:has-text("查看该公司")',
        'a:has-text("全部职位")',
        '.company-info a[href*="gongsi"]',
    ],
    "company_job_cards": [
        ".job-card",
        ".job-primary",
        "li.job-primary",
        '[class*="job-card"]',
        '[class*="job-primary"]',
    ],
}


def _merge_selectors():
    """合并 settings 表中的选择器覆盖。"""
    try:
        from boss_state import get_setting
        import json as _json

        raw = get_setting("selector_overrides", "")
        if raw:
            overrides = _json.loads(raw)
            for k, v in overrides.items():
                if k in SELECTORS and isinstance(v, list) and len(v) > 0:
                    SELECTORS[k] = v
    except Exception:
        pass


_merge_selectors()

# ── 绝对上限 ──
# 投递上限已改为自适应的每日预算（见 boss_apply_budget / get_apply_allowance）。
# 这里不再留固定值：原来的 MAX_APPLY_PER_DAY=30 只在 apply_to_job 里夹逼过一次，
# 而 api/agent 那几处算 remaining 时根本没用它 —— 两套上限并存只会互相打架。
MAX_AUTO_REPLY_PER_DAY = 200   # （自动回复侧仍用固定上限，未改）

# ── HR 头衔优先级（数字越大越"高"，用于 pick_top_hr）──
HR_TITLE_PRIORITY = [
    ("招聘经理", 100),
    ("HR经理", 95),
    ("HRBP", 90),
    ("HR负责人", 85),
    ("招聘主管", 80),
    ("招聘负责人", 75),
    ("HR主管", 70),
    ("招聘官", 60),
    ("HR", 50),
    ("人事", 40),
    ("招聘专员", 30),
    ("HR助理", 20),
    ("猎头", 10),
]


def _hr_title_score(title: str) -> int:
    """根据 HR 头衔文本返回优先级分；命中多条取最高。"""
    if not title:
        return 0
    t = title.strip()
    for kw, score in HR_TITLE_PRIORITY:
        if kw in t:
            return score
    return 0


# ── 法人 / 老板识别 ──
# 中国最常见姓氏：同姓巧合概率高，作为弱信号降权。
COMMON_SURNAMES = set(
    "王李张刘陈杨黄赵周吴徐孙朱马胡郭林何高梁郑罗宋谢唐韩曹许邓萧冯曾程蔡彭潘"
    "袁于董余苏叶吕魏蒋田杜丁沈姜范江傅钟卢汪戴崔任陆廖姚方金邱夏谭韦贾邹石熊"
    "孟秦阎薛侯雷白龙段郝孔邵史毛常万顾赖武康贺严尹钱施牛洪龚汤陶黎温莫易樊乔"
)

# 复姓：同姓基本可锁定，作为强信号。
COMPOUND_SURNAMES = [
    "欧阳",
    "太史",
    "端木",
    "上官",
    "司马",
    "东方",
    "独孤",
    "南宫",
    "万俟",
    "闻人",
    "夏侯",
    "诸葛",
    "尉迟",
    "公羊",
    "赫连",
    "澹台",
    "皇甫",
    "宗政",
    "濮阳",
    "公冶",
    "太叔",
    "申屠",
    "公孙",
    "慕容",
    "仲孙",
    "钟离",
    "长孙",
    "宇文",
    "司徒",
    "鲜于",
    "司空",
    "闾丘",
    "子车",
    "亓官",
    "司寇",
    "巫马",
    "公西",
    "颛孙",
    "乐正",
    "宰父",
    "谷梁",
    "拓跋",
    "夹谷",
    "轩辕",
    "令狐",
    "段干",
    "百里",
    "呼延",
    "东郭",
    "南门",
    "羊舌",
    "微生",
    "梁丘",
    "左丘",
    "西门",
    "东里",
    "仲长",
    "即墨",
    "达奚",
]


_LEGAL_REP_NOISE = ("变更", "信息", "登记", "为", "是", "持股", "对外", "及")


def _extract_legal_rep(body_text: str, lines: Optional[List[str]] = None) -> str:
    """从公司页纯文本里抽取「法定代表人」姓名。

    常见格式：
        法定代表人：张三 / 法定代表人  张三 / 法定代表人\\n张三
    抽不到返回空字符串。
    """
    if not body_text:
        return ""
    # 同行 / 紧邻：法定代表人[: ：空白换行]姓名
    m = re.search(r"法定代表人[\s：:]*([\u4e00-\u9fa5·•・]{2,8})", body_text)
    if m:
        name = m.group(1).strip("·•・ ")
        if name and not any(n in name for n in _LEGAL_REP_NOISE):
            return name
    # 兜底：按行找「法定代表人」标签，取下一行非空文本
    rows = lines or []
    for i, ln in enumerate(rows):
        if "法定代表人" in ln and i + 1 < len(rows):
            cand = rows[i + 1].strip("·•・ ")
            if re.fullmatch(r"[\u4e00-\u9fa5·•・]{2,8}", cand) and not any(n in cand for n in _LEGAL_REP_NOISE):
                return cand
    return ""


def _extract_surname(name: str) -> str:
    """提取姓氏：先匹配复姓，否则取首字。空字符串返回空。"""
    name = (name or "").strip()
    if not name:
        return ""
    for cs in COMPOUND_SURNAMES:
        if name.startswith(cs):
            return cs
    return name[0]


def detect_boss(hr_name: str, legal_rep: str) -> Dict[str, Any]:
    """根据招聘者姓名与公司法人姓名，判断招聘者是否疑似老板/法人本人。

    返回 {is_boss, confidence, reason, score_bonus}：
      - 全名完全一致      → high   （几乎肯定本人）
      - 同姓 + 复姓/罕见姓 → medium （巧合概率低）
      - 同姓 + 常见姓      → low    （仅供参考）
      - 缺数据 / 不同姓    → none
    score_bonus 用于在 pick_top_hr 里加权（数值越大越优先）。
    """
    hr_name = (hr_name or "").strip()
    legal_rep = (legal_rep or "").strip()
    none = {"is_boss": False, "confidence": "none", "reason": "", "score_bonus": 0}
    if not hr_name or not legal_rep:
        return none

    if hr_name == legal_rep:
        return {
            "is_boss": True,
            "confidence": "high",
            "reason": f"招聘者姓名与法人「{legal_rep}」完全一致",
            "score_bonus": 1000,
        }

    hr_surname = _extract_surname(hr_name)
    rep_surname = _extract_surname(legal_rep)
    if not hr_surname or hr_surname != rep_surname:
        return none

    if hr_surname in COMPOUND_SURNAMES or hr_surname not in COMMON_SURNAMES:
        return {
            "is_boss": True,
            "confidence": "medium",
            "reason": f"与法人同为「{hr_surname}」姓（复姓/罕见姓，巧合概率低）",
            "score_bonus": 300,
        }

    return {
        "is_boss": True,
        "confidence": "low",
        "reason": f"与法人同为「{hr_surname}」姓（常见姓，仅供参考）",
        "score_bonus": 50,
    }


def pick_top_hr(hrs: List[Dict[str, Any]], legal_rep: str = "") -> Optional[Dict[str, Any]]:
    """从 HR 列表中挑出职务最高的一个。
    同分时按输入顺序（保持稳定）。输入为空返回 None。
    每条 hr 至少包含 name / title 字段。

    传入 legal_rep（公司法人姓名）时，疑似老板的 HR 会被加权优先选出，
    并在返回的 hr dict 上附加 is_boss / boss_confidence / boss_reason 字段。
    """
    if not hrs:
        return None
    best = None
    best_score = -1
    for hr in hrs:
        title_score = _hr_title_score(hr.get("title") or "")
        boss = detect_boss(hr.get("name") or "", legal_rep)
        if legal_rep:
            hr["is_boss"] = boss["is_boss"]
            hr["boss_confidence"] = boss["confidence"]
            hr["boss_reason"] = boss["reason"]
        total = title_score + boss["score_bonus"]
        if total > best_score:
            best_score = total
            best = hr
    return best


# BOSS 自己的系统卡片 / 提示。它们混在 HR 消息流里，但**不是人说的话** ——
# 对它们自动回复既没意义，又会让账号显得像机器人。
#
# 这份名单来自真实会话（2026-10-12 把 messages 表里 sender='hr' 的去重全捞了一遍）：
# 微信交换卡片、附件简历请求卡片、安全提示、微信号卡片都在其中。
SYSTEM_NOTICE_PREFIXES = (
    "你与该职位竞争者PK情况",
    "竞争力分析",
    "BOSS安全提示",
    "系统消息",
    "沟通分析",
    "今日推荐",
    "该Boss已查看了你的简历",
    "对方已查看了您的附件简历",
    "对方已同意",                       # 附件简历已发送
    # ── 实测补充（从真实会话里捞出来的）──
    "我想要和您交换微信",              # 微信交换卡片
    "我想要一份您的附件简历",          # 简历请求卡片
    "您的附件简历",
    "附件简历请求",
)

# 不能靠前缀识别的卡片：微信号卡片以**电话号码**开头，没法枚举。改用特征词。
SYSTEM_NOTICE_MARKERS = (
    "复制微信号",
    "的微信号",
    "点击预览附件简历",
    "为保障您的安全建议在平台内沟通",  # 安全提示（也可能出现在交换微信卡片里）
)


def is_system_notification(content: str) -> bool:
    """判断一条 HR 侧消息是不是 BOSS 的系统卡片，而不是真人在说话。

    两道判据：前缀（大多数卡片）+ 特征词（微信号卡片这种以电话号码开头的）。

    另外**不做长度过滤**：原实现有 `if len(content) > 80: return False`，本意是
    "长消息更可能是真人写的"，但系统卡片完全可能很长（微信交换卡片会把安全提示和
    「拒绝/同意」按钮拼在同一条里），先按长度筛等于给长卡片开后门。这里的特征词都
    足够独特，不会误伤真人发言（见 test_system_notification.py 的两组用例）。
    """
    text = (content or "").strip()
    if not text:
        return False
    if text.startswith(SYSTEM_NOTICE_PREFIXES):
        return True
    return any(m in text for m in SYSTEM_NOTICE_MARKERS)


class BossAutomation(BossScraper):
    """在 BossScraper 基础上增加交互能力"""


    def __init__(self, headless=False):
        super().__init__(headless)
        init_db()
        # 风控冷却状态：检测到频率限制时设置 _cooldown_until，期间暂停高风险操作
        self._cooldown_until = 0.0
        self._risk_strikes = 0  # 连续命中风控信号次数，越高退避越久
        self._last_action_ts = 0.0  # 上一次高风险动作（投递/发消息）时间戳
        # 扫描"该不该让路"的钩子，由 BossManager 在启动浏览器时挂上
        # （见 _should_yield）。单独 new 出来的实例没有它，扫描行为不变。
        self.should_yield: Optional[Callable[[], bool]] = None

    # ══════════════════════════════════════
    #  底层交互 helpers
    # ══════════════════════════════════════

    def _find_element(self, selector_list: List[str], timeout_ms: int = 5000) -> Optional[Locator]:
        """逐个尝试选择器，返回第一个可见匹配。"""
        deadline = time.time() + timeout_ms / 1000
        while time.time() < deadline:
            for sel in selector_list:
                try:
                    loc = self.page.locator(sel).first
                    if loc.is_visible():
                        return loc
                except Exception:
                    continue
            time.sleep(0.3)
        return None

    def _find_all_elements(self, selector_list: List[str]) -> List[Locator]:
        """返回所有匹配的可见元素。"""
        for sel in selector_list:
            try:
                locs = self.page.locator(sel)
                count = locs.count()
                if count > 0:
                    return [locs.nth(i) for i in range(count)]
            except Exception:
                continue
        return []

    def _human_type(self, locator: Locator, text: str):
        """逐字输入，模拟真人打字。"""
        try:
            locator.click()
            time.sleep(random.uniform(0.1, 0.3))
        except Exception:
            pass
        for ch in text:
            self.page.keyboard.type(ch, delay=random.randint(50, 150))
        time.sleep(random.uniform(0.3, 0.8))

    def _safe_click(self, locator: Locator):
        """带随机延迟的点击。"""
        time.sleep(random.uniform(0.2, 0.6))
        try:
            locator.hover()
            time.sleep(random.uniform(0.1, 0.3))
        except Exception:
            pass
        locator.click()

    def _has_text(self, *texts: str) -> bool:
        """检查页面是否包含任意关键词。"""
        try:
            body = self.page.inner_text("body").lower()
            return any(t.lower() in body for t in texts)
        except Exception:
            return False

    # ══════════════════════════════════════
    #  安全检查
    # ══════════════════════════════════════

    # 风控关键词 → 判定类别。顺序有意义：先验证码、再封号、最后限频。
    # 注意这是**启发式**：只看页面开头若干字里有没有这些词，正常页面的正文里
    # 恰好出现这些词就会误判，所以判定前必须先摘掉 _SAFETY_BOILERPLATE。
    # captcha 关键词必须**具体到验证界面本身**。
    # 原先把裸的「验证」也当成信号，而岗位 JD 里「效果验证」「验证方案」「数据验证」
    # 极其常见 —— 实测一个正常的 RAG 岗位，就因为 JD 写了「检索链路优化及效果验证」
    # 被判成 captcha，投递直接放弃并冷却 600s。泛词换成了成句的说法。
    _SAFETY_KEYWORDS = (
        ("captcha", ("验证码", "滑块验证", "拖动滑块", "完成拼图", "请完成验证", "安全验证", "captcha")),
        ("banned", ("账号异常", "违规", "限制使用", "冻结")),
        ("rate_limit", ("操作太频繁", "稍后再试", "休息一下", "访问过于频繁", "请求过于频繁")),
    )

    # 真正的风控拦截是**整页跳转**，URL 比正文关键词可靠得多（实测形态：
    # https://www.zhipin.com/web/passport/zp/security.html?...&code=37）。
    _SAFETY_URL_MARKERS = ("/security.html",)

    # 正常页面里必然出现、且含风控关键词的样板文字，判定前先摘掉。
    # 实测：岗位详情页常驻 BOSS 自己的免责声明
    #   "BOSS直聘严禁用人单位和招聘者用户做出任何损害求职者合法权益的违法违规行为……"
    # 里面有"违规"，不排掉的话每一个岗位详情页都会被判成账号封禁、投递直接瘫掉。
    _SAFETY_BOILERPLATE = ("boss安全提示", "boss直聘严禁用人单位")

    def _strip_safety_boilerplate(self, body_lower: str) -> str:
        """按段落摘掉样板文字。

        比对时把段落内空白全部去掉 —— 页面上的写法可能是"boss 安全提示"（有空格）
        或"boss\\n安全提示"（换行），直接子串匹配会漏。换行的情况标记会被拆到两个
        段落里，所以除了本段，还要把"上一段 + 本段"拼起来再比一次。
        """
        kept = []
        prev_compact = ""
        for para in re.split(r"\n+", body_lower):
            compact = re.sub(r"[\s　]+", "", para)
            if any(
                marker in compact or marker in prev_compact + compact
                for marker in self._SAFETY_BOILERPLATE
            ):
                prev_compact = compact
                continue
            kept.append(para)
            prev_compact = compact
        return "\n".join(kept)

    def _strip_job_description(self, scan_text: str) -> str:
        """从待扫文本里摘掉岗位 JD 正文。

        JD 是招聘方自由撰写的，命中 _SAFETY_KEYWORDS 里的词纯属正常（实测：
        "检索链路优化及效果验证" 命中了 captcha）。而安全判定的目标是**页面框架**
        ——风控提示出现在页面级的横幅/弹窗里，不会长在 JD 里。

        按行剔除：JD 元素与 body 的 inner_text 共享同一批文本行，去掉空白后逐行
        比对即可。取不到 JD 元素（非详情页）时原样返回。
        """
        try:
            jd = self.page.evaluate(
                """() => {
                    const el = document.querySelector(
                        '.job-detail, .job-sec-text, .detail-content, .job_desc, .job-detail-section'
                    );
                    return el ? (el.innerText || '') : '';
                }"""
            )
        except Exception:
            return scan_text
        if not jd:
            return scan_text
        norm = lambda s: re.sub(r"[\s　]+", "", s)  # noqa: E731
        jd_lines = {norm(ln) for ln in jd.split("\n") if ln.strip()}
        if not jd_lines:
            return scan_text
        return "\n".join(ln for ln in scan_text.split("\n") if norm(ln) not in jd_lines)

    def inspect_page_safety(self) -> Dict[str, Any]:
        """检查页面安全状态，返回结构化结果。

        返回 {ok, reason, category, backoff}；命中时额外带
        {keyword, snippet, url} —— 这三点是判断"真风控"还是"误报"的唯一依据。
          - category: ok / login / captcha / banned / rate_limit / error
          - backoff: 建议退避秒数（rate_limit 时随 _risk_strikes 递增）
        同时维护 self._risk_strikes / self._cooldown_until。
        """
        try:
            body = self.page.inner_text("body")
            body_lower = body.lower()
        except Exception:
            return {"ok": True, "reason": "", "category": "error", "backoff": 0}

        if self._login_prompt_visible():
            return {"ok": False, "reason": "需要重新登录", "category": "login", "backoff": 0}

        # URL 先判：真的被风控拦截是整页跳到 security.html，这个信号比正文关键词准得多，
        # 命中就不用再去看正文了（正文里"验证"之类的泛词太容易误伤岗位 JD）。
        try:
            cur_url = (self.page.url or "").lower()
        except Exception:
            cur_url = ""
        if any(m in cur_url for m in self._SAFETY_URL_MARKERS):
            print(f"  ⚠️ 安全检查命中 captcha（URL 被跳转到风控页）: {cur_url[:120]}")
            self._trigger_cooldown(category="captcha")
            return {
                "ok": False,
                "reason": "检测到验证码",
                "category": "captcha",
                "keyword": "url",
                "snippet": cur_url[:160],
                "url": cur_url,
                "backoff": self._cooldown_remaining(),
            }

        # 先摘掉样板文字再扫 —— 否则岗位详情页上 BOSS 自己的免责声明会让"违规"
        # 每次都命中，把所有投递都误判成封号
        scan_text = self._strip_safety_boilerplate(body_lower)
        # 再摘掉岗位 JD 正文 —— 它是招聘方写的自由文本，出现"验证/限制"这类词完全
        # 正常，但它们在 _SAFETY_KEYWORDS 里是风控信号（实测因此误判过 captcha）
        scan_text = self._strip_job_description(scan_text)
        head = scan_text[:600]
        reasons = {
            "captcha": "检测到验证码",
            "banned": "账号异常",
            "rate_limit": "操作频率限制",
        }
        for category, keywords in self._SAFETY_KEYWORDS:
            hit = next((kw for kw in keywords if kw in head), None)
            if hit is None:
                continue

            # 必须把证据打出来：这套判定只是"在页面开头 600 字里扫关键词"的启发式，
            # 判断不了是 BOSS 真的风控还是页面正文里恰好出现了这些词。没有关键词、
            # 页面 URL 和上下文，"误判"和"真封号"就分不出来（曾因此白等 1 小时冷却）。
            # 在摘除样板后的文本里取片段，保证片段就是真正触发判定的那段
            snippet = self._safety_snippet(scan_text, hit)
            try:
                url = self.page.url
            except Exception:
                url = ""
            print(
                f"  ⚠️ 安全检查命中 {category} 关键词 {hit!r}\n"
                f"      页面: {url}\n"
                f"      原文片段: ...{snippet}..."
            )
            self._trigger_cooldown(category=category)
            return {
                "ok": False,
                "reason": reasons[category],
                "category": category,
                "keyword": hit,
                "snippet": snippet,
                "url": url,
                "backoff": self._cooldown_remaining(),
            }

        return {"ok": True, "reason": "", "category": "ok", "backoff": 0}

    @staticmethod
    def _safety_snippet(body_lower: str, keyword: str, radius: int = 60) -> str:
        """截取命中关键词附近的原文，用于人工判断是真风控还是误判。"""
        idx = body_lower.find(keyword)
        if idx < 0:
            return ""
        start = max(0, idx - radius)
        end = min(len(body_lower), idx + len(keyword) + radius)
        return body_lower[start:end].replace("\n", " ⏎ ").strip()

    def check_page_safety(self) -> bool:
        """所有自动化操作前检查页面安全状态。布尔版（向后兼容）。"""
        status = self.inspect_page_safety()
        if not status["ok"]:
            print(f"  ⚠️ 安全检查: {status['reason']}")
        return status["ok"]

    # ── 风控冷却退避 ──
    def _trigger_cooldown(self, category: str = "rate_limit") -> None:
        """指数退避：连续命中风控时冷却时间递增，避免账号被限制。

        如果当前不在冷却期，说明是新一轮风控触发，重置计数器从基础档开始；
        如果在冷却期内再次命中，说明风控在升级，递增计数器延长冷却。
        """
        if not self.in_cooldown():
            self._risk_strikes = 0  # 新一轮触发，重置计数
        self._risk_strikes += 1
        base = {
            "rate_limit": 120,    # 2 分钟起
            "captcha": 600,       # 10 分钟起
            "banned": 3600,       # 1 小时起
            "daily_quota": 1800,  # 平台说今天投完了 —— 真正的"当天收手"由预算逻辑负责
        }.get(category, 120)
        seconds = min(base * (2 ** (self._risk_strikes - 1)), 7200)
        self._cooldown_until = time.time() + seconds
        print(f"  ⚠️ 触发风控冷却 category={category} strikes={self._risk_strikes} 冷却 {seconds:.0f}s")
        # 自适应预算：所有风控（URL 跳转 / 关键词命中 / JS 回报）都汇到这一个函数，
        # 所以这里挂一次就够了 —— 撞风控 → 当日预算收缩 + 间隔拉长。
        report_apply_risk(category)

    def _cooldown_remaining(self) -> float:
        return max(0.0, self._cooldown_until - time.time())

    def in_cooldown(self) -> bool:
        return self._cooldown_remaining() > 0

    def _respect_cooldown(self) -> bool:
        if not self.in_cooldown():
            return True
        remaining = self._cooldown_remaining()
        print(f"  🧊 风控冷却中，剩余 {remaining:.0f}s，跳过本次操作")
        return False

    def _is_already_applied_on_detail_page(self) -> bool:
        """岗位详情页是否已经投递过。

        实测（2026-10-10，已投递/未投递两个页面各抓一次）：
          - 已投递 → `.btn-startchat` 按钮文本是「继续沟通」
          - 未投递 → 同一位置的按钮文本是「立即沟通」
          - **「已沟通」两个字在详情页从不出现**（两次抓取都是 0 次），
            而「继续沟通」的最小容器就是那个按钮本身

        原先用 `_has_text("已沟通", "继续沟通")` 扫**整个页面 body**：页面别处
        （推荐位、相似职位卡片、正文）出现这两个词就会被误判成"已投递过"，
        把一次真实投递吞掉。改成只认这个按钮。
        """
        try:
            return bool(
                self.page.evaluate(
                    """() => {
                    const btn = document.querySelector('.btn-startchat, .btn-startchat-wrap');
                    if (!btn) return false;   // 按钮还没渲染出来 → 当作未投递，交给点击环节判定
                    const t = (btn.innerText || '').trim();
                    return t.includes('继续沟通') || t.includes('已沟通');
                }"""
                )
            )
        except Exception:
            return False

    def _human_pace(self, min_gap: float = 8.0, max_gap: float = 20.0) -> None:
        """两次高风险动作之间保证最小人性化间隔，避免节奏过于机械。"""
        now = time.time()
        elapsed = now - self._last_action_ts
        target = random.uniform(min_gap, max_gap)
        if elapsed < target:
            time.sleep(target - elapsed)
        self._last_action_ts = time.time()

    def _human_scroll(self) -> None:
        """模拟人类浏览：随机滚动页面，停留阅读。"""
        try:
            for _ in range(random.randint(1, 3)):
                delta = random.randint(200, 600)
                self.page.mouse.wheel(0, delta)
                time.sleep(random.uniform(0.8, 2.5))
        except Exception:
            pass

    # ══════════════════════════════════════
    #  Session 保活 & 心跳
    # ══════════════════════════════════════

    def check_logged_in(self) -> bool:
        """快速检查当前是否已登录；未知空白页不直接当作过期。"""
        try:
            return self.is_logged_in_page()
        except Exception:
            return False

    def heartbeat(self) -> bool:
        """心跳: 只检查当前页面登录状态，不主动跳转。"""
        try:
            return self.check_logged_in()
        except Exception:
            return False

    def keep_alive(self):
        """主动保活: 在聊天页保持 BOSS session 活跃。已登录时用轻量操作代替完整刷新。"""
        try:
            current_url = self.page.url
            need_navigate = "/web/geek/chat" not in current_url
            try:
                if need_navigate:
                    self.page.goto("https://www.zhipin.com/web/geek/chat", wait_until="load", timeout=30000)
                    pause(2, 4)
                else:
                    # 已在聊天页，轻量滚动模拟用户活动，避免频繁 reload 被检测
                    try:
                        self.page.mouse.move(random.randint(200, 600), random.randint(300, 500))
                        pause(0.5, 1.0)
                        self.page.evaluate("window.scrollBy(0, %d)" % random.randint(-100, 100))
                    except Exception:
                        pass
            except Exception:
                pass
            return self.check_logged_in()
        except Exception:
            return False

    def _save_state(self):
        """保存当前浏览器状态到文件。"""
        try:
            from boss_firefox import STATE_FILE

            state = self._ctx.storage_state()
            STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False)
        except Exception:
            pass

    # ══════════════════════════════════════
    #  JD 补抓（列表页只有公司/薪资，JD 正文在详情页）
    # ══════════════════════════════════════

    def fetch_job_description(self, job_url: str) -> str:
        """打开岗位详情页抓取 JD 正文；失败返回空串。

        用于「列表页没拿到 JD」时按需补抓（AI 分析 / 导入工作流前）。
        """
        if not job_url:
            return ""
        try:
            self.page.goto(job_url, wait_until="load", timeout=45000)
        except Exception:
            pass  # BOSS 可能先跳安全页再回来导致 interrupted，继续尝试读取
        pause(1, 2)
        try:
            desc = self.page.evaluate("""() => {
                const el = document.querySelector('.job-detail, .job-sec-text, .detail-content, .job_desc, .job-detail-section');
                return el ? el.innerText.trim() : '';
            }""")
        except Exception:
            return ""
        return desc or ""

    # ══════════════════════════════════════
    #  自动投递
    # ══════════════════════════════════════

    def apply_to_job(
        self, job_url: str, greeting: Optional[str] = None, dispatch: Optional[Future] = None,
    ) -> dict:
        """
        对单个岗位执行投递流程:
        1. 打开详情页，抓 HR 名 / 岗位名 / JD（必须在导航去聊天页之前做完）
        2. 点击"立即沟通" —— 平台会自动发出它在 BOSS 后台设置的招呼语，
           「投递」到此即成立
        3. （可选，custom_greeting_enabled）到聊天页精确定位该 HR 的会话，
           追加一条自有定制话术

        返回 {success, message, application_id, greeting_sent}
          success 表示"「立即沟通」确实被点下去了"，与第 3 步成败无关；
          greeting_sent 才是自有定制话术有没有发出去。
        """
        if not job_url:
            return {"success": False, "message": "缺少岗位链接"}

        # 日限检查。唯一的执行闸门 —— 预算由 boss_apply_budget 自适应算出来
        # （顺利加码、撞风控收缩），这里只负责问"还能投几个"。
        allowance = get_apply_allowance()
        if allowance["remaining"] <= 0:
            return {
                "success": False,
                "outcome": "not_attempted",
                "message": f"已达今日投递额度({allowance['budget']}条)",
                "stop_batch": True,
                "stop_reason": "daily_budget",
            }

        try:
            target = urlsplit(job_url)
            valid_job = (
                target.scheme == "https" and target.netloc in {"www.zhipin.com", "zhipin.com"}
                and re.fullmatch(r"/job_detail/[^/]+/?", target.path)
            )
        except ValueError:
            valid_job = False
        if not valid_job:
            return {"success": False, "outcome": "not_attempted", "message": "缺少有效的 BOSS 岗位详情链接"}

        # 风控冷却：处于退避期直接跳过，让上层稍后重试
        if not self._respect_cooldown():
            return {"success": False, "outcome": "not_attempted", "message": "风控冷却中", "cooldown": True, "stop_batch": True, "stop_reason": "cooldown"}

        print(f"  🚀 投递: {job_url[:60]}...")

        try:
            # 人类化节奏：两次投递间保持随机间隔
            self._human_pace(min_gap=10.0, max_gap=25.0)

            self.page.goto(job_url, wait_until="load", timeout=45000)
            pause(2, 4)

            # 模拟人类浏览：滚动阅读JD，让BOSS觉得是真人在看
            self._human_scroll()
            # 滚回顶部，避免 Playwright auto-scroll 触发 BOSS header 重排动画导致 click 超时
            try:
                self.page.evaluate("window.scrollTo({top: 0, behavior: 'instant'})")
            except Exception:
                pass
            pause(1, 2)

            safety = self.inspect_page_safety()
            if not safety["ok"]:
                # 原先这里直接 return、一行日志都没有，日志从"🚀 投递"直接跳到
                # 风控冷却，完全看不出是哪一步、因为什么被拦下的
                print(
                    f"  ❌ 安全检查未通过，放弃投递: {safety['reason']}"
                    f"（命中关键词={safety.get('keyword')!r} 页面={safety.get('url', '')}）"
                )
                return {
                    "success": False, "outcome": "not_attempted",
                    "message": f"安全检查未通过: {safety['reason']}", "safety": safety,
                    "stop_batch": True, "stop_reason": safety["category"],
                }

            # 从详情页提取岗位名和JD（数据库可能没有JD）
            page_title = ""
            page_desc = ""
            try:
                page_title = self.page.evaluate("""() => {
                    const el = document.querySelector('.name h1, .job-name h1, .post-name h1, h1.name, .job-title, .title');
                    return el ? el.innerText.trim() : '';
                }""")
                page_desc = self.page.evaluate("""() => {
                    const el = document.querySelector('.job-detail, .job-sec-text, .detail-content, .job_desc, .job-detail-section');
                    return el ? el.innerText.trim() : '';
                }""")
            except Exception:
                pass

            # 从详情页提取 HR 真实姓名。必须趁页面还停在详情页时做 —— 后面追加定制
            # 话术会导航到聊天页，那时再抓就抓到聊天页的内容了。
            hr_name = ""
            try:
                hr_info = self.page.evaluate("""() => {
                    const body = (document.body || {}).innerText || '';
                    const lines = body.split('\\n').map(l => l.trim()).filter(Boolean);
                    let hrName = '', hrTitle = '';
                    for (let i = 0; i < lines.length; i++) {
                        const l = lines[i];
                        if (l.includes('HR') || l.includes('招聘者') || l.includes('招聘经理') ||
                            l.includes('人事') || l.includes('HRBP') || l.includes('猎头')) {
                            if (i > 0 && lines[i-1].length <= 6 && !/\\d|省|市|区|路|号|招聘|公司|BOSS/.test(lines[i-1])) {
                                hrName = lines[i-1];
                            }
                            hrTitle = l;
                            break;
                        }
                    }
                    return {hrName, hrTitle};
                }""")
                hr_name = (hr_info.get("hrName") or "").strip()
            except Exception:
                pass

            # 岗位库内记录：定位会话、生成定制话术都要用它
            app_record = get_application_by_url(job_url) or {}

            # 检查是否已投递
            if self._is_already_applied_on_detail_page():
                existing = get_application_by_url(job_url)
                if existing and existing["status"] == "pending":
                    update_application_status(existing["id"], "applied")
                print(f"  ✅ 已投递过，跳过")
                return {"success": True, "message": "已投递过", "already_applied": True}

            # ── 点击「立即沟通」 ──
            # 平台会在这一步自动发出它在 BOSS 后台设置的招呼语 —— 所以"投递"到这里
            # 就成立了。我们自己那句（定制话术）是随后追加的第二条消息，见下方。
            # 早先的实现会在这里死等聊天弹窗再逐字输入（10s 超时），而实测那个弹窗
            # 根本不会出现，那段代码从未生效、只白拖慢每次投递。
            if dispatch is not None and not dispatch.set_running_or_notify_cancel():
                return {"success": False, "outcome": "not_attempted", "message": "操作已超时，未发起点击"}
            print(f"  🖱️ 调用 window.__bossStartChat() 原生点击「立即沟通」...")
            js_result = None
            try:
                js_result = self.page.evaluate("async () => { return await window.__bossStartChat(); }")
                print(
                    f"  📡 JS返回: success={js_result.get('success')} clicked={js_result.get('clicked')} "
                    f"message={js_result.get('message', '')[:60]}"
                )
                if js_result.get("already_applied"):
                    existing = get_application_by_url(job_url)
                    if existing and existing["status"] == "pending":
                        update_application_status(existing["id"], "applied")
                    print(f"  ✅ 已投递过")
                    return {"success": True, "message": "已投递过", "already_applied": True}
                if js_result.get("cooldown"):
                    # JS 侧已经把两种拦截分开了：`daily_quota` = 平台明确说今天投完了，
                    # `rate_limit` = 只是操作太快。原先一律当 rate_limit 处理 ——
                    # 于是"平台说没额度了"只冷却 120s，而且因为消息里没有"上限"二字，
                    # apply_batch 的字符串嗅探也认不出来，整批会继续往下硬撞。
                    category = js_result.get("reason") or "rate_limit"
                    self._trigger_cooldown(category=category)
                    print(f"  ⚠️ 触发风控冷却（{category}）")
                    return {
                        "success": False,
                        "message": js_result.get("message", "触发风控"),
                        "cooldown": True,
                        "stop_batch": True,
                        "stop_reason": category,
                    }
            except Exception as e:
                print(f"  ❌ window.__bossStartChat() 异常: {e}")
                return {
                    "success": False, "outcome": "unknown",
                    "message": "与浏览器的交互中断，请先在 BOSS 会话核对结果。",
                    "stop_batch": True, "stop_reason": "unconfirmed",
                }

            # ── 判定这次投递到底有没有发生 ──
            # 唯一可信信号是"「立即沟通」按钮有没有被点下去"（平台会自己发招呼语）。
            # 连按钮都没点上就绝不写 applied —— 旧实现无论成败都写 applied 并返回
            # success=True，造成"后台显示已投递、BOSS 上其实什么都没发生"。
            js_clicked = bool((js_result or {}).get("success") or (js_result or {}).get("clicked"))
            if not js_clicked:
                msg = (js_result or {}).get("message") or "未能点击「立即沟通」"
                print(f"  ❌ 投递未发生（{msg}），状态保持不变")
                return {"success": False, "message": msg}

            # greeting_sent=True：平台招呼语已经随「立即沟通」发出去了 —— 这一刻
            # 就是"今天投了几个"的计数依据，不写的话每日上限根本不会生效。
            try:
                existing = get_application_by_url(job_url)
                app_id = existing["id"] if existing else add_application({
                    "title": page_title, "company": "", "url": job_url,
                })
                update_application_status(app_id, "applied", greeting_sent=True)
            except Exception as exc:
                print(f"  ❌ 平台点击后写入投递记录失败: {exc}")
                return {
                    "success": False, "outcome": "unknown",
                    "message": "本地记录写入失败，请先在 BOSS 会话核对结果。",
                    "stop_batch": True, "stop_reason": "unconfirmed",
                }

            # 详情页抓到的 HR 名补进内存记录，供 _open_conversation_for_job 定位会话
            # （库里可能没有 HR 名，搜索阶段不一定抓得到）
            if hr_name and not (app_record.get("hr_name") or "").strip():
                app_record = {**app_record, "hr_name": hr_name}

            # ── 追加自有定制话术（第二条消息，由 custom_greeting_enabled 控制）──
            try:
                custom = self._send_custom_greeting_for_job(job_url, greeting, page_title, page_desc, app_record)
            except Exception as exc:
                print(f"  ⚠️ 已记录投递，后续定制话术异常: {exc}")
                custom = {"need_manual": True, "reason": "定制话术异常"}
            greeting_sent = bool(custom.get("sent"))
            if greeting_sent:
                # 把定制话术的文本记上（greeting_sent_at 上面那次已经写过了，
                # 这里只是补文本；COALESCE 语义保证不会把已记的时间清掉）
                try:
                    update_application_status(app_id, "applied", custom.get("text") or "", greeting_sent=True)
                except Exception as exc:
                    print(f"  ⚠️ 已记录投递，补写定制话术失败: {exc}")
            elif not custom.get("skipped"):
                print(f"  ⚠️ 自有招呼语未发出: {custom.get('reason', '')}")

            # HR 名在详情页阶段已抓过；这里只补库里可能有的字段
            try:
                app_record = get_application_by_url(job_url) or app_record
                hr_name = hr_name or app_record.get("hr_name", "")
                if hr_name and len(hr_name) >= 2:
                    get_or_create_conversation(
                        app_id, hr_name, app_record.get("company", ""), app_record.get("job_title", ""),
                    )
                increment_daily_stat("applications_sent")
                self._save_state()
            except Exception as exc:
                print(f"  ⚠️ 已记录投递，辅助信息更新失败: {exc}")
            self._risk_strikes = 0  # 投递成功说明账号正常，清零风控计数
            # 自适应预算：攒干净成功，够了就加码
            report_apply_success()
            if greeting_sent:
                print("  ✅ 投递成功（定制话术已发出）")
                message = "投递成功"
            elif custom.get("skipped"):
                # 追加功能是关着的，这是正常状态，不该给出"没发出去"的暗示
                print("  ✅ 投递成功（平台招呼语已发）")
                message = "投递成功"
            else:
                # 平台那条招呼语已随"立即沟通"发出，只是我们这句没跟上 —— 如实标注，
                # 别让上层以为自有招呼语也发出去了
                print("  ✅ 投递成功，但定制话术未发出（需人工确认）")
                message = f"投递成功（平台招呼语已发，定制话术未发出：{custom.get('reason', '')}）"
            return {
                "success": True,
                "message": message,
                "application_id": app_id,
                "greeting_sent": greeting_sent,
                "custom_greeting_need_manual": bool(custom.get("need_manual")),
            }

        except Exception as e:
            print(f"  ❌ 投递失败: {e}")
            return {"success": False, "message": str(e)}

    # 批量投递的循环已移到 BossManager.apply_batch_async —— 那里每条岗位一次 run_pw，
    # 拟人停顿在异步层。留在 PW 线程里跑整批会把"单次操作超时"放大成"整批超时"，
    # 一条卡住就占死唯一那条线程（见 manager.apply_batch_async 的说明）。

    # ══════════════════════════════════════
    #  聊天监控
    # ══════════════════════════════════════

    def navigate_to_chat(self) -> bool:
        """导航到 BOSS 聊天页，切到「未读」标签，只显示有未读消息的会话。"""
        try:
            self.page.goto("https://www.zhipin.com/web/geek/chat", wait_until="load", timeout=45000)
            pause(2, 3)
            # 点击「未读」标签，只显示有未读的会话
            for sel in ['span.label-name:has-text("未读")', 'li:has-text("未读")', '.label-name:has-text("未读")']:
                try:
                    unread_tab = self.page.locator(sel).first
                    if unread_tab.is_visible():
                        unread_tab.click()
                        pause(1, 2)
                        break
                except Exception:
                    pass
            return self.check_page_safety()
        except Exception:
            return False

    def poll_conversation_list(self) -> List[dict]:
        """从 BOSS 聊天页 DOM 获取会话列表。DOM 失败用 body text 正则兜底。"""
        conversations = []

        # 方式1: DOM 选择器
        conv_els = self._find_all_elements(SELECTORS["conversation_items"])
        if conv_els:
            for el in conv_els:
                try:
                    text = el.inner_text().strip()
                    if not text or len(text) < 3:
                        continue
                    # 从 BOSS 真实结构提取 HR 名字: .name-text
                    try:
                        hr_name = el.locator(".name-text").first.inner_text().strip()
                    except Exception:
                        hr_name = ""
                    if not hr_name:
                        # 兜底：从 body_text 行中提取
                        hr_name = (
                            el.evaluate("""(el) => {
                            const lines = (el.innerText||'').split('\\n').map(l=>l.trim()).filter(Boolean);
                            for (const l of lines) {
                                if (/^\\d{1,2}:\\d{2}$/.test(l)) continue;
                                if (/^\\[.+\\]$/.test(l)) continue;
                                const ch = l.replace(/[^\\u4e00-\\u9fff]/g,'');
                                if (ch.length>=2 && ch.length<=5) return l.split(/[\\s|·]/)[0].trim();
                            }
                            return '';
                        }""")
                            or ""
                        )
                    has_unread = False
                    try:
                        badge = el.locator('.red-dot, [class*="unread"]').first
                        has_unread = badge.is_visible()
                    except Exception:
                        pass
                    conversations.append(
                        {
                            "text": text,
                            "has_unread": has_unread,
                            "element": el,
                            "hr_name": hr_name,
                        }
                    )
                except Exception:
                    continue

        # 方式2: body text 正则兜底
        if not conversations:
            try:
                body = self.page.inner_text("body") or ""
                pattern = r"(\d{1,2}:\d{2})\s+([\u4e00-\u9fff\w·]+?)\s+(\[\s*\S+\s*\])\s+(.+?)(?=\s*\d{1,2}:\d{2}\s+|没有更多了|\Z)"
                for m in re.findall(pattern, body):
                    time_str, name_block, status, msg = m
                    # 提取纯名字：从 name_block 中去掉公司后缀
                    hr_name = re.sub(
                        r"[\u4e00-\u9fff]{2,}(?:有限|集团|科技|网络|信息|文化|教育|医疗|能源|贸易|实业|发展|控股|投资).*|经理.*|主管.*|专员.*|总监.*|[\[\]].*",
                        "",
                        name_block,
                    ).strip()
                    if not hr_name or len(hr_name) < 2:
                        m2 = re.match(r"^[\u4e00-\u9fff]{2,4}", name_block)
                        hr_name = m2.group(0) if m2 else name_block[:6]
                    hr_name = hr_name.strip()
                    if not hr_name or len(hr_name) < 2:
                        continue
                    conversations.append(
                        {
                            "text": f"{time_str}\n{name_block}\n{status}\n{msg}".strip(),
                            "has_unread": "未读" in status,
                            "element": None,
                            "hr_name": hr_name,
                        }
                    )
            except Exception:
                pass

        return conversations

    def read_visible_messages(self) -> List[dict]:
        """读取当前右侧聊天窗口中的可见消息，避免把左侧会话列表误当聊天内容。"""
        try:
            raw = self.page.evaluate("""() => {
                const result = [];
                const vw = window.innerWidth || 1200;
                const visible = el => {
                    const r = el.getBoundingClientRect();
                    const style = getComputedStyle(el);
                    return r.width > 0 && r.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
                };
                const clean = text => (text || '')
                    .replace(/^(已读|未读|送达|发送失败|已发送)\\s*/g, '')
                    .replace(/\\n?(已读|未读|送达|发送失败|已发送)$/g, '')
                    .trim();
                const pickStatus = text => {
                    const m = (text || '').match(/(^|\\n)\\s*(已读|未读|送达|发送失败|已发送)\\s*(\\n|$)/);
                    return m ? m[2] : '';
                };
                const push = (el, contentEl) => {
                    if (!visible(el)) return;
                    const r = el.getBoundingClientRect();
                    if (r.left + r.width / 2 < vw * 0.35) return;
                    const textNode = contentEl || el.querySelector('.text p, .text span:last-child, .text, [class*="bubble"], [class*="content"]');
                    const fullText = el.innerText || '';
                    const content = clean(textNode ? textNode.innerText : el.innerText);
                    if (!content || /^(已读|未读|送达|发送失败|已发送)$/.test(content)) return;
                    if (content.length > 1000) return;
                    const cls = el.className || '';
                    const sender = cls.includes('item-myself') || cls.includes('myself') || cls.includes('self') || r.left > vw * 0.52 ? 'me' : 'hr';
                    const status = sender === 'me' ? pickStatus(fullText) : '';
                    result.push({sender: sender, content: content, status: status});
                };

                document.querySelectorAll('li.message-item, li[class*="message-item"]').forEach(el => push(el));
                if (result.length === 0) {
                    document.querySelectorAll('[class*="message"] [class*="bubble"], [class*="msg"] [class*="bubble"], [class*="chat"] [class*="text"]').forEach(el => push(el, el));
                }
                return result;
            }""")
            return raw or []
        except Exception:
            return []

    def open_conversation_by_name(self, hr_name: str) -> bool:
        """在聊天页中按 HR 名字定位并打开对应会话。"""
        try:
            current_url = self.page.url
            if "/web/geek/chat" not in current_url:
                self.page.goto("https://www.zhipin.com/web/geek/chat", wait_until="load", timeout=45000)
                pause(2, 3)

            # 右栏已经开着目标会话就直接返回。手动发送时用户多半正看着这个会话，
            # 而下面那套定位是"逐个选择器试 → 坐标点击 → 轮询等渲染"，白花 0.5~1s。
            # 校验用头部文本，和 _open_conversation_for_job 同一套判断 —— 宁可不省
            # 这一步，也不能因为名字碰巧对不上就点错人。
            if self._conversation_is_open():
                header = self._current_conversation_header()
                if self._header_starts_with(header, hr_name):
                    print(f"  [会话] 目标会话「{hr_name}」已打开，跳过重新定位")
                    return True

            # 用文本选择器定位列表项，再走**坐标点击**。
            # 不能用 loc.click()：实测在 BOSS 聊天页上它会卡到超时（force=True 也一样），
            # 而下面的 JS 派发兜底对 BOSS 的 React 处理器无效（点了没反应）。
            for sel in [
                f'li[role="listitem"]:has-text("{hr_name}")',
                f'.user-list li:has-text("{hr_name}")',
                f'[class*="friend"]:has-text("{hr_name}")',
                f'text="{hr_name}"',
            ]:
                try:
                    loc = self.page.locator(sel).first
                    if loc.count() > 0 and loc.is_visible():
                        res = self._click_by_coordinates(loc)
                        if not res["ok"]:
                            continue
                        if self._wait_conversation_open():
                            return True
                        print(f"  ⚠️ 点击「{hr_name}」后右栏未打开会话")
                except Exception:
                    pass

            # 原来的"DOM 里找最小容器并派发 MouseEvent"兜底已删除：
            # 实测 BOSS 的 React 处理器不认非可信事件，派发出去没有任何反应 ——
            # 留着只会让调用方以为点成功了。
            print(f"  ⚠️ 未能打开会话: {hr_name}")
            return False
        except Exception as e:
            print(f"  ⚠️ 打开会话失败 ({hr_name}): {e}")
            return False

    # ══════════════════════════════════════
    #  会话定位（投递后追加话术用：只打开、只校验，不发送任何内容）
    # ══════════════════════════════════════

    # 会话头部（HR 名/公司名）的候选选择器。
    # 前两项是 2026-10-10 实测确认的结构：会话打开后 .chat-conversation 的一级子节点
    # 依次是 .top-info-content（头部）/ .message-content（消息）/ .message-controls（输入区），
    # 其中 .top-info-content 形如「马先生 上海马帮 招聘专员 更多 python开发 15-20K·13薪 上海 查看职位」。
    _HEADER_SELECTORS = [
        ".chat-conversation .top-info-content",
        ".chat-conversation .user-info",
        ".top-info-content",
        ".chat-friend-info",
        "[class*='conversation-header']",
        "[class*='dialog-header']",
    ]

    @staticmethod
    def _norm_text(s: str) -> str:
        """去掉所有空白，便于做包含匹配（DOM innerText 换行位置不稳定）。"""
        return re.sub(r"[\s　]+", "", s or "")

    # 聊天输入框的候选选择器。把"限定在会话区内"的放前面 —— 页面上别处也可能有
    # contenteditable（搜索框等），不加限定会点错元素。
    _CHAT_INPUT_SELECTORS = (
        ".chat-conversation [contenteditable='true']",
        ".editor-container [contenteditable='true']",
        ".chat-editor [contenteditable='true']",
        ".chat-conversation textarea",
        "#chat-input",
    )

    def _focus_chat_editor(self) -> bool:
        """把焦点放到聊天输入框上。

        同样不能用 locator.click()（在 BOSS 聊天页会卡到超时），改用坐标点击。
        输入框是 contenteditable，必须先点中它，Playwright 的 keyboard 才会打进去。
        """
        inp = self._chat_input_locator()
        if inp is None:
            print("  ⚠️ 未找到可见的聊天输入框")
            return False
        res = self._click_by_coordinates(inp)
        if not res["ok"]:
            print(f"  ⚠️ 聚焦聊天输入框失败: {res['reason']}")
            return False
        time.sleep(0.2)
        return True

    def _chat_input_locator(self):
        """返回当前可见的聊天输入框 Locator；找不到返回 None。"""
        for sel in self._CHAT_INPUT_SELECTORS:
            try:
                loc = self.page.locator(sel).first
                if loc.count() > 0 and loc.is_visible():
                    return loc
            except Exception:
                continue
        return None

    def _in_chat_context(self) -> bool:
        """判断当前是否处于聊天上下文（聊天页或页内聊天弹窗）。

        用于给"清空输入框"加保险：避免在岗位详情页误清掉无关的输入框
        （页面上可能有搜索框等同样匹配 contenteditable 的元素）。
        """
        try:
            if "/web/geek/chat" in (self.page.url or ""):
                return True
        except Exception:
            pass
        try:
            return bool(
                self.page.evaluate(
                    """() => document.querySelectorAll(
                        'li.message-item, li[class*="message-item"]'
                    ).length > 0"""
                )
            )
        except Exception:
            return False

    def _clear_chat_input(self) -> None:
        """清空聊天输入框。

        send_message 失败时输入框里的文字会残留下来，用户之后手动打开任意会话，
        这段残留文字就会被一起发出去 —— 也就是把招呼语发给了不相干的 HR。
        所以所有发送路径退出前都必须调用本方法。

        整个函数体都包在 try 里：它是"安全网"，本身抛异常会让调用方（可能在
        异常处理分支里调它）再炸一次，那就彻底失控了。
        """
        try:
            if not self._in_chat_context():
                return
            inp = self._chat_input_locator()
            if inp is None:
                return
            # 坐标点击聚焦（locator.click 在 BOSS 聊天页会卡到超时）
            self._click_by_coordinates(inp)
            time.sleep(0.15)
            for _ in range(2):
                self.page.keyboard.press("Control+a")
                time.sleep(0.05)
                self.page.keyboard.press("Backspace")
                time.sleep(0.05)
                if not (inp.inner_text() or "").strip():
                    break
        except Exception as e:
            print(f"  ⚠️ 清空聊天输入框失败: {e}")

    def _goto_chat_page(self) -> bool:
        """确保页面停在 BOSS 聊天页。已在该页则直接返回，避免多刷页面触发登录检查。"""
        try:
            if "/web/geek/chat" in (self.page.url or ""):
                return True
        except Exception:
            pass
        try:
            self.page.goto("https://www.zhipin.com/web/geek/chat", wait_until="load", timeout=45000)
            pause(2, 3)
            return self.check_page_safety()
        except Exception as e:
            print(f"  ⚠️ 导航聊天页失败: {e}")
            return False

    def _list_conversation_cards(self) -> List[Dict[str, Any]]:
        """按页面顺序返回可见会话卡片 [{locator, text, hr_name}]。

        只使用第一个能匹配到元素的选择器 —— 多套选择器会命中同一张卡片的不同层级，
        混用会让"点第 N 张"失去意义。
        """
        for sel in SELECTORS["conversation_items"]:
            try:
                locs = self.page.locator(sel)
                count = locs.count()
            except Exception:
                continue
            if count == 0:
                continue

            cards: List[Dict[str, Any]] = []
            seen = set()
            for i in range(count):
                loc = locs.nth(i)
                try:
                    if not loc.is_visible():
                        continue
                    text = (loc.inner_text() or "").strip()
                except Exception:
                    continue
                # 空状态占位不是会话
                if len(text) < 3 or "暂无" in text or "30天" in text:
                    continue
                key = re.sub(r"\s+", " ", text)[:60]
                if key in seen:
                    continue
                seen.add(key)
                try:
                    hr_name = (loc.locator(".name-text").first.inner_text() or "").strip()
                except Exception:
                    hr_name = ""
                cards.append({"locator": loc, "text": text, "hr_name": hr_name})
            if cards:
                return cards
        return []

    def _pick_conversation_card(
        self, cards: List[Dict[str, Any]], expected_hr: str, expected_company: str, expected_title: str
    ):
        """按三级优先级挑卡片，返回 (card, matched_by)；无匹配返回 (None, "")。"""
        hr = self._norm_text(expected_hr)
        company = self._norm_text(expected_company)
        title = self._norm_text(expected_title)

        # 1) HR 名命中（最可靠）
        if len(hr) >= 2:
            for c in cards:
                if hr in self._norm_text(c["text"]):
                    return c, "hr_name"

        # 2) 公司名 + 岗位名同时命中
        if company:
            for c in cards:
                t = self._norm_text(c["text"])
                if company in t and (not title or title in t):
                    return c, "company+job_title"

        # 3) 仅公司名命中：置信度低，打开后靠头部校验兜底
        if company:
            for c in cards:
                if company in self._norm_text(c["text"]):
                    return c, "company_only"

        return None, ""

    def _read_open_conversation_header(self, expected_hr: str = "", expected_company: str = "") -> str:
        """读取当前打开会话的头部文本（HR 名 / 公司名）。读不到返回空串。

        先按内容找：在右半边找"包含预期 HR 名/公司名的最小可见元素"。纯几何兜底
        不行 —— 顶部导航是**全宽**元素，中心点正好落在视口中央，会被当成会话头部
        （实测踩过：读回来的是"首页/职位/公司/校园…"）。
        """
        try:
            header = (
                self.page.evaluate(
                    """(args) => {
                    const vw = window.innerWidth || 1200;
                    const vh = window.innerHeight || 800;
                    const squeeze = s => (s || '').replace(/[\\s　]+/g, '');
                    const targets = [squeeze(args.hr), squeeze(args.company)]
                        .filter(t => t.length >= 2);
                    if (!targets.length) return '';
                    const visible = el => {
                        const s = getComputedStyle(el);
                        return s.display !== 'none' && s.visibility !== 'hidden' && s.opacity !== '0';
                    };
                    let best = null;
                    for (const el of document.querySelectorAll('div, section, header, span, p, h1, h2, h3, a, li')) {
                        const r = el.getBoundingClientRect();
                        if (r.width <= 0 || r.height <= 0) continue;
                        if (!visible(el)) continue;
                        if (r.left < vw * 0.4) continue;    // 会话列表在左半边，排除
                        if (r.width > vw * 0.9) continue;   // 全宽横条是站点导航
                        if (r.top > vh * 0.45) continue;    // 会话头部在上半部
                        const t = squeeze(el.innerText);
                        if (!t || t.length > 200) continue;
                        if (!targets.some(x => t.includes(x))) continue;
                        if (!best || t.length < best.len) {
                            best = {text: (el.innerText || '').trim(), len: t.length};
                        }
                    }
                    return best ? best.text.slice(0, 200) : '';
                }""",
                    {"hr": expected_hr, "company": expected_company},
                )
                or ""
            )
            if header.strip():
                return header.strip()
        except Exception:
            pass

        for sel in self._HEADER_SELECTORS:
            try:
                loc = self.page.locator(sel).first
                if loc.count() > 0 and loc.is_visible():
                    text = (loc.inner_text() or "").strip()
                    if text:
                        return text[:200]
            except Exception:
                continue
        return ""

    def _chat_pane_snapshot(self) -> str:
        """右栏快照（消息条数 + 文本前缀），用于判断会话到底有没有真的打开。"""
        try:
            return (
                self.page.evaluate(
                    """() => {
                    const vw = window.innerWidth || 1200;
                    const n = document.querySelectorAll(
                        'li.message-item, li[class*="message-item"]').length;
                    let text = '';
                    for (const el of document.querySelectorAll('div, section')) {
                        const r = el.getBoundingClientRect();
                        if (r.width <= 0 || r.height <= 0) continue;
                        if (r.left < vw * 0.5) continue;
                        const t = (el.innerText || '').trim();
                        if (!t) continue;
                        if (!text || t.length > text.length) text = t;
                    }
                    return '消息条数=' + n + ' 右栏前 80 字=' + JSON.stringify(text.slice(0, 80));
                }"""
                )
                or ""
            )
        except Exception:
            return ""

    def _click_by_coordinates(self, locator) -> Dict[str, Any]:
        """把元素滚进可视区后，用**真实鼠标坐标点击**。

        为什么不用 locator.click()：实测（2026-10-10 真实 BOSS 聊天页）
          - `locator.click()`   → 一直卡在 "performing click action" 直到超时，
                                  `force=True` 也一样（说明不是可操作性检查的问题）
          - 原生派发 MouseEvent → 返回成功但 BOSS 的 React 处理器毫无反应，
                                  会话根本不打开（"点了没反映"）
          - `page.mouse.click`  → 有效
        所以这里取元素坐标后交给 page.mouse。
        """
        try:
            locator.evaluate("el => el.scrollIntoView({block: 'center'})")
        except Exception:
            pass
        time.sleep(0.3)
        try:
            rect = locator.evaluate(
                "el => { const r = el.getBoundingClientRect();"
                " return {x: r.left, y: r.top, w: r.width, h: r.height}; }"
            )
        except Exception as e:
            return {"ok": False, "reason": f"取坐标失败: {e}"}
        if not isinstance(rect, dict) or rect.get("w", 0) <= 0 or rect.get("h", 0) <= 0:
            return {"ok": False, "reason": "元素尺寸为 0，可能不可见"}

        cx = rect["x"] + rect["w"] / 2
        cy = rect["y"] + rect["h"] / 2
        vp = self.page.viewport_size or {"width": 1280, "height": 800}
        if not (0 <= cx <= vp["width"] and 0 <= cy <= vp["height"]):
            return {
                "ok": False,
                "reason": f"坐标在视口外 ({cx:.0f},{cy:.0f})，滚动后仍未进入可视区",
            }
        try:
            self.page.mouse.click(cx, cy)
        except Exception as e:
            return {"ok": False, "reason": f"mouse.click 失败: {e}"}
        return {"ok": True, "reason": f"({cx:.0f},{cy:.0f})"}

    def _conversation_is_open(self) -> bool:
        """右栏是否真的打开了某个会话。

        未打开时 BOSS 会渲染 `.chat-no-data` 空态（"与您进行过沟通的 Boss 都会在
        左侧列表中显示"）；打开后换成 `.top-info-content` + `.message-content`。
        """
        try:
            return bool(
                self.page.evaluate(
                    """() => {
                    const conv = document.querySelector('.chat-conversation');
                    if (!conv) return false;
                    const empty = conv.querySelector('.chat-no-data');
                    if (empty && empty.getBoundingClientRect().height > 0) return false;
                    return !!conv.querySelector('.top-info-content, .message-content');
                }"""
                )
            )
        except Exception:
            return False

    def _wait_conversation_open(self, timeout_sec: float = 5.0) -> bool:
        """轮询等待右栏把会话渲染出来（点击后不是瞬时的）。"""
        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            if self._conversation_is_open():
                return True
            time.sleep(0.5)
        return False

    def _header_matches(self, header: str, expected_hr: str, expected_company: str) -> bool:
        """会话头部里出现预期的 HR 名或公司名，才算校验通过。"""
        h = self._norm_text(header)
        if not h:
            return False
        for v in (expected_hr, expected_company):
            v = self._norm_text(v)
            if len(v) >= 2 and v in h:
                return True
        return False

    def _header_starts_with(self, header: str, hr_name: str) -> bool:
        """会话头部的开头就是该 HR 名 —— 比 _header_matches 更严。

        头部形如「马先生 上海马帮 招聘专员 …」，HR 名永远在最前面。这里要求前缀
        相等而不是包含：包含匹配下，一个两字的名字（乃至"先生"这种常见词）在别人
        的头部里也能命中，而这条捷径一旦误判，就是**在错的会话里打字发送**。
        对不上只是退回正常的点击定位，代价仅仅是慢一点。
        """
        h = self._norm_text(header)
        n = self._norm_text(hr_name)
        return len(n) >= 2 and h.startswith(n)

    def _current_conversation_header(self) -> str:
        """右栏当前打开的会话头部文本；没打开或读不到返回空串。"""
        for sel in self._HEADER_SELECTORS:
            try:
                loc = self.page.locator(sel).first
                if loc.count() > 0:
                    txt = loc.inner_text() or ""
                    if txt.strip():
                        return txt
            except Exception:
                continue
        return ""

    def _is_card_active(self, locator) -> bool:
        """被点击的会话卡片是否已成为"当前选中"项。

        实测：卡片是 `li`（class 为空），选中标记 `selected` 加在**内部**的
        `div.friend-content` 上 —— 所以不能只看自己和父级，必须连子孙一起查。
        """
        try:
            return bool(
                locator.evaluate(
                    """(el) => {
                    const RE = /active|selected|current/i;
                    let n = el;
                    for (let i = 0; i < 3 && n; i++, n = n.parentElement) {
                        const cls = (n.className || '') + ' ' + (n.getAttribute('aria-selected') || '');
                        if (RE.test(String(cls))) return true;
                    }
                    for (const d of el.querySelectorAll('*')) {
                        const cls = (d.className || '') + ' ' + (d.getAttribute('aria-selected') || '');
                        if (RE.test(String(cls))) return true;
                    }
                    return false;
                }"""
                )
            )
        except Exception:
            return False

    def _open_conversation_for_job(self, app_record: Dict[str, Any]) -> Dict[str, Any]:
        """打开指定岗位对应的 HR 会话。只打开、只校验，**不发送任何内容**。

        数据全部取自数据库（applications 的 hr_name / company / job_title），不靠
        当前页面的 DOM 猜测 —— 旧的 _send_greeting_via_chat_page "盲点列表第一个会话"
        正是发错人的原因。

        匹配优先级见 _pick_conversation_card；打开后必须做身份校验，对不上就放弃
        （宁可不发，也不发错人）。

        返回 {opened, matched_by, header, expected, reason}
        """
        expected_hr = (app_record.get("hr_name") or "").strip()
        expected_company = (app_record.get("company") or "").strip()
        expected_title = (app_record.get("job_title") or "").strip()

        result: Dict[str, Any] = {
            "opened": False,
            "matched_by": "",
            "header": "",
            "expected": {
                "hr_name": expected_hr,
                "company": expected_company,
                "job_title": expected_title,
            },
            "reason": "",
        }

        if not (expected_hr or expected_company):
            result["reason"] = "数据库缺少 hr_name/company，无法定位会话（宁可不发，避免发错人）"
            print(f"  ⚠️ {result['reason']}")
            return result

        if not self._goto_chat_page():
            result["reason"] = "无法进入聊天页"
            return result

        # 刚投递的会话落在「新招呼」，是最强信号；没有再退回「全部」
        for tab in ("新招呼", "全部"):
            self._click_chat_tab(tab)
            cards = self._list_conversation_cards()
            if not cards:
                continue

            picked, matched_by = self._pick_conversation_card(
                cards, expected_hr, expected_company, expected_title
            )
            if picked is None:
                # 打点样例：匹配不是靠猜，得能看见列表里到底长什么样
                sample = " | ".join(re.sub(r"\s+", " ", c["text"])[:40] for c in cards[:4])
                print(f"  ℹ️ [{tab}] 有 {len(cards)} 张卡片均未匹配；样例: {sample}")
                continue

            clicked = self._click_by_coordinates(picked["locator"])
            if not clicked["ok"]:
                result["reason"] = f"点击会话失败: {clicked['reason']}"
                print(f"  ⚠️ {result['reason']}")
                continue
            # 等右栏把会话渲染出来（未打开时是 .chat-no-data 空态）
            if not self._wait_conversation_open():
                result["reason"] = f"点击后右栏未打开任何会话（匹配={matched_by}）"
                print(f"  ⚠️ {result['reason']}，放弃发送")
                continue

            header = self._read_open_conversation_header(expected_hr, expected_company)
            result["header"] = header
            result["matched_by"] = matched_by

            if self._header_matches(header, expected_hr, expected_company):
                result["opened"] = True
                print(f"  ✅ 已打开目标会话（{matched_by} → 头部={header[:40]!r}）")
                return result

            # 头部没读到目标信息时，退一步用"点中的卡片是否已成为当前选中项"佐证。
            # 前提必须是 HR 名精确命中 —— company_only 那种低置信匹配不给过。
            # 注意不能要求 header 为空：BOSS 改版后头部可能读到导航栏之类的无关文本，
            # 那同样属于"没读到目标信息"。
            if matched_by == "hr_name" and self._is_card_active(picked["locator"]):
                result["opened"] = True
                result["matched_by"] = "hr_name+active"
                result["reason"] = f"头部未读到目标信息（读到={header[:40]!r}），改用卡片选中态校验"
                print(f"  ⚠️ {result['reason']}，判定为已打开")
                return result

            result["reason"] = (
                f"打开后身份校验不通过（匹配={matched_by} 头部={header[:60]!r}；"
                f"{self._chat_pane_snapshot()}）"
            )
            print(f"  ⚠️ {result['reason']}，放弃发送")

        if not result["reason"]:
            result["reason"] = "未找到匹配的会话"
        print(f"  ⚠️ 未能打开目标会话: {result['reason']}")
        return result

    def send_message(self, text: str, fast: bool = True) -> bool:
        """逐字模拟键盘输入 + Enter 发送，确保 BOSS 检测到输入事件。

        多行文本按行键入，行间用 Shift+Enter 插入换行（不会提交），
        全部内容输出完毕后才按 Enter 发送 —— 保证一条回复始终是一条消息。
        """
        try:
            # 点击输入框激活（坐标点击，见 _focus_chat_editor 的说明）
            self._focus_chat_editor()

            # 清除已有内容
            try:
                self.page.keyboard.press("Control+a")
                time.sleep(0.05)
                self.page.keyboard.press("Backspace")
                time.sleep(0.05)
            except Exception:
                pass

            # 逐行键入：行内 type，行间 Shift+Enter 插入换行。
            # 注意不能用 keyboard.type(整段文本)：Playwright 遇到 "\n" 会派发 Enter 键，
            # BOSS 聊天框收到 Enter 就直接发送，多行回复会被拆成多条消息发出去。
            delay = 20 if fast else 40
            lines = text.split("\n")
            for idx, line in enumerate(lines):
                if idx > 0:
                    self.page.keyboard.press("Shift+Enter")
                    time.sleep(0.05)
                if line:
                    self.page.keyboard.type(line, delay=delay)
            pause(0.3, 0.6)

            # 全部内容输出完毕，才按 Enter 发送
            self.page.keyboard.press("Enter")
            pause(0.5, 1)

            # 验证：消息区出现了刚发的文本（用首个非空行判断，inner_text 会归一化换行）
            check_source = next((ln for ln in lines if ln.strip()), text)
            check = check_source[:8] if len(check_source) >= 8 else check_source[:4]
            if check and sent_text_visible(self.page, check):
                return True

            # 再试一次 Enter
            try:
                self.page.keyboard.press("Enter")
                pause(0.3, 0.5)
            except Exception:
                pass

            # 二次校验：仍看不到刚发的文本就认定没发出去。
            # 必须清空输入框 —— 残留文字会在用户之后打开任意会话时被一起发出，
            # 把消息发给不相干的 HR（见 docs/boss_search_and_greeting_fix_plan.md 3.2）
            try:
                if check and sent_text_visible(self.page, check):
                    return True
            except Exception:
                pass
            print("  ⚠️ send_message 未能确认发送成功，清空输入框避免残留文字被误发")
            self._clear_chat_input()
            return False
        except Exception as e:
            print(f"  ⚠️ send_message 失败: {e}")
            self._clear_chat_input()
            return False

    def _build_custom_greeting(
        self,
        greeting: Optional[str],
        page_title: str,
        page_desc: str,
        app_record: Dict[str, Any],
    ) -> str:
        """准备第二条（定制）话术：调用方传了就用它，否则按设置生成。"""
        job_title = (app_record.get("job_title") or "").strip() or (page_title or "").strip()
        company = (app_record.get("company") or "").strip()

        text = (greeting or "").strip()
        if not text:
            text = get_setting(
                "greeting_template",
                "您好，我对贵公司的{job_title}岗位很感兴趣，请问可以详细了解一下吗？",
            )
            if "{job_title}" in text:
                text = text.replace("{job_title}", job_title or "相关岗位")
            if "{company}" in text:
                text = text.replace("{company}", company or "贵公司")

        # smart 模式：模板话术不是给这个岗位定制的，用 LLM 按 JD 重写一条。
        # 调用方已经传了话术（如 /jobs/apply 生成过带优化建议的版本）时不覆盖。
        if not greeting and get_setting("greeting_mode", "template") == "smart":
            try:
                from boss_replier import generate_greeting_ai

                jd = (app_record.get("description") or "").strip() or (page_desc or "")
                text = (
                    generate_greeting_ai(
                        job_title or "相关岗位",
                        company or "贵公司",
                        "",
                        jd,
                        bool(app_record.get("is_boss")),
                        get_setting("ai_reply_style", "professional"),
                        get_setting("resume_summary", ""),
                    )
                    or text
                )
                print(f"  📝 smart 模式按 JD 生成定制话术: {text[:60]!r}...")
            except Exception as e:
                print(f"  ⚠️ 生成定制话术失败，沿用模板: {e}")

        try:
            max_len = int(get_setting("custom_greeting_max_len", "120") or 0)
        except (TypeError, ValueError):
            max_len = 120
        if max_len > 0 and len(text) > max_len:
            text = text[:max_len]
        return text.strip()

    def _send_custom_greeting_for_job(
        self,
        job_url: str,
        greeting: Optional[str],
        page_title: str,
        page_desc: str,
        app_record: Dict[str, Any],
    ) -> Dict[str, Any]:
        """投递成功后，给这个 HR 追加一条自有定制话术（第二条消息）。

        与旧 _send_greeting_via_chat_page 的关键区别：会话由 _open_conversation_for_job
        按 HR 名/公司名精确定位并校验身份，**找不到就不发**。旧实现盲点聊天列表第一个
        会话，可能把话术发给不相干的 HR。

        返回 {sent, text, reason, need_manual, skipped}
          skipped=True 表示开关关着、本次根本没打算发（与"发了但失败"区分开）
        """
        result: Dict[str, Any] = {
            "sent": False,
            "text": "",
            "reason": "",
            "need_manual": False,
            "skipped": False,
        }

        if get_setting("custom_greeting_enabled", "false") != "true":
            result["skipped"] = True
            result["reason"] = "custom_greeting_enabled=false，未发送"
            print("  ℹ️ 追加定制话术已关闭（设置项 custom_greeting_enabled），跳过")
            return result

        text = self._build_custom_greeting(greeting, page_title, page_desc, app_record)
        if not text or len(text) < 2:
            result["reason"] = "定制话术为空"
            print(f"  ⚠️ {result['reason']}，跳过追加")
            return result
        result["text"] = text

        # 与平台那条招呼语拉开间隔 —— 连着两条太密容易触发风控
        try:
            lo = float(get_setting("custom_greeting_delay_min_sec", "4") or 4)
            hi = float(get_setting("custom_greeting_delay_max_sec", "8") or 8)
        except (TypeError, ValueError):
            lo, hi = 4.0, 8.0
        time.sleep(random.uniform(min(lo, hi), max(lo, hi)))

        # 刚投递的会话在聊天列表里可能要几秒才出现，给它几次机会
        opened: Dict[str, Any] = {"opened": False, "reason": ""}
        for attempt in range(3):
            opened = self._open_conversation_for_job(app_record)
            if opened.get("opened"):
                break
            # 缺 hr_name/company 是硬伤，重试也没用
            if "无法定位会话" in (opened.get("reason") or ""):
                break
            if attempt < 2:
                wait = 5.0 * (attempt + 1)
                print(f"  ⏳ 还没找到目标会话，{wait:.0f}s 后重试（{attempt + 1}/3）")
                time.sleep(wait)

        if not opened.get("opened"):
            result["reason"] = opened.get("reason") or "未能打开目标会话"
            result["need_manual"] = True
            print(f"  ⚠️ 定制话术未发送（宁可不发也不发错人）: {result['reason']}")
            return result

        print(f"  📝 追加定制话术({len(text)}字): {text[:60]}...")
        try:
            result["sent"] = bool(self.send_message(text))
        finally:
            # send_message 失败时会自己清空；这里再兜一次，杜绝残留文字
            # 在用户之后打开任意会话时被误发出去
            self._clear_chat_input()
        if not result["sent"]:
            result["reason"] = "消息发送未确认成功"
        return result

    def _get_chat_security_id(self, hr_name: str = "") -> str:
        """从 BOSS API 或页面提取对方 securityId。"""
        import re

        for attempt in range(3):  # 重试3次
            try:
                # 方式1: 页面 HTML 正则搜
                html = self.page.content()
                m = re.search(r'securityId["\']?\s*[:=]\s*["\']([A-Za-z0-9_~+/=-]{30,})["\']', html)
                if m:
                    return m.group(1)

                # 方式2: JS 全局对象
                sid = self.page.evaluate("""() => {
                    for (const key of Object.keys(window)) {
                        try {
                            const v = window[key];
                            if (!v || typeof v !== 'object') continue;
                            if (v.securityId) return v.securityId;
                        } catch(e) {}
                    }
                    return '';
                }""")
                if sid:
                    return sid

                # 方式3: BOSS API 获取会话列表, 按 HR 名匹配
                encrypt_id = ""
                try:
                    encrypt_id = self.page.evaluate("""() => {
                        for (const key of Object.keys(window)) {
                            try { if (window[key] && window[key].encryptSystemId) return window[key].encryptSystemId; } catch(e) {}
                        }
                        return '';
                    }""")
                except Exception:
                    pass

                if encrypt_id and hr_name:
                    url = f"https://www.zhipin.com/wapi/zprelation/friend/geekFilterByLabel?labelId=0&encryptSystemId={encrypt_id}"
                    data = self.page.evaluate(
                        """async (url) => {
                        const r = await fetch(url, {headers:{'Accept':'application/json','x-requested-with':'XMLHttpRequest'}, credentials:'include'});
                        return await r.json();
                    }""",
                        url,
                    )
                    friends = (data or {}).get("zpData", {}).get("friends", [])
                    for f in friends:
                        fn = (f.get("bossName") or f.get("realName") or "").strip()
                        if fn == hr_name:
                            return f.get("securityId", "")

                if attempt < 2:
                    print(f"  [securityId] 第{attempt + 1}次获取失败，重试...")
                    pause(1, 2)

            except Exception as e:
                print(f"  [securityId] 获取异常: {e}")
                if attempt < 2:
                    pause(1, 2)

        print(f"  ⚠️ securityId 获取失败（3次重试），HR: {hr_name}")
        return ""

    def _click_chat_agree_button(self) -> bool:
        """在聊天消息区内点击BOSS系统简历索取的「同意」按钮。

        BOSS系统简历索取消息格式:
            "我想要一份您的附件简历，您是否同意"
            下面有两个按钮: [拒绝] [同意]
        只需要点击「同意」即可，不需要走工具栏发简历弹窗流程。
        """
        try:
            result = self.page.evaluate("""() => {
                // 在消息列表里找所有可见元素中文本精确为"同意"的可点击元素
                const all = document.querySelectorAll('span, button, div, a');
                for (const el of all) {
                    const txt = (el.innerText || el.textContent || '').trim();
                    if (txt === '同意' && el.offsetParent !== null) {
                        // 排除弹窗/对话框里的"同意"
                        if (el.closest('.dialog, .popup, .modal, [class*="dialog"], [class*="popup"], [class*="modal"]')) {
                            continue;
                        }
                        el.click();
                        return true;
                    }
                }
                // 兜底：匹配包含「同意」且文本较短的按钮
                const btns = document.querySelectorAll('button, [class*="btn"], [role="button"]');
                for (const b of btns) {
                    const txt = (b.innerText || '').trim();
                    if ((txt === '同意' || txt === '同意发送') && b.offsetParent !== null) {
                        b.click();
                        return true;
                    }
                }
                return false;
            }""")
            return bool(result)
        except Exception as e:
            print(f"  ⚠️ _click_chat_agree_button 异常: {e}")
            return False

    def send_wechat(self, hr_name: str = "") -> bool:
        """通过 BOSS API 发起交换，等弹窗出现后点「确定」。"""
        try:
            sid = self._get_chat_security_id(hr_name)

            if sid:
                self.page.evaluate(
                    """
                    async (sid) => {
                        await fetch('https://www.zhipin.com/wapi/zpchat/exchange/test', {
                            method: 'POST',
                            headers: {'Content-Type': 'application/x-www-form-urlencoded', 'x-requested-with': 'XMLHttpRequest'},
                            body: 'securityId=' + encodeURIComponent(sid) + '&type=2&friendSource=0',
                            credentials: 'include',
                        });
                    }
                """,
                    sid,
                )
                print("  [换微信] API /exchange/test 已调用")
            else:
                btn = self._find_element(SELECTORS["wechat_share_btn"], timeout_ms=5000)
                if not btn:
                    print("  ⚠️ send_wechat: 无法获取 securityId 且未找到按钮")
                    return False
                btn.click()
                print("  [换微信] 已点击换微信按钮")

            # 等弹窗 → 点「确定」
            confirm_clicked = self.page.evaluate("""() => {
                return new Promise((resolve) => {
                    let tries = 0;
                    const check = () => {
                        // 先找「确定与对方交换微信吗？」弹窗里的确定按钮
                        const btns = document.querySelectorAll('span');
                        for (const b of btns) {
                            if (b.innerText.trim() === '确定' && b.offsetParent !== null) {
                                const parent = b.closest('.secure-exchange, .sentence-popover, [class*="exchange"], [class*="popover"]');
                                if (parent) {
                                    b.click();
                                    resolve(true);
                                    return;
                                }
                            }
                        }
                        // 兜底：任何可见的"确定"按钮
                        const all = document.querySelectorAll('.btn-sure-v2, span');
                        for (const el of all) {
                            if (el.innerText.trim() === '确定' && el.offsetParent !== null && !el.closest('.btn-outline-v2')) {
                                el.click();
                                resolve(true);
                                return;
                            }
                        }
                        if (++tries < 30) setTimeout(check, 300);
                        else resolve(false);
                    };
                    check();
                });
            }""")
            if confirm_clicked:
                pause(0.5, 1)
                print("  [换微信] 已点确定按钮")
                return True

            print("  [换微信] 超时: 未找到确定按钮")
            return False

        except Exception as e:
            print(f"  ⚠️ send_wechat 失败: {e}")
            return False

    def send_phone(self, hr_name: str = "") -> bool:
        """通过 BOSS API 交换手机号（type=1），等弹窗出现后点「确定」。"""
        try:
            sid = self._get_chat_security_id(hr_name)

            if sid:
                self.page.evaluate(
                    """
                    async (sid) => {
                        await fetch('https://www.zhipin.com/wapi/zpchat/exchange/test', {
                            method: 'POST',
                            headers: {'Content-Type': 'application/x-www-form-urlencoded', 'x-requested-with': 'XMLHttpRequest'},
                            body: 'securityId=' + encodeURIComponent(sid) + '&type=1&friendSource=0',
                            credentials: 'include',
                        });
                    }
                """,
                    sid,
                )
                print("  [换电话] API /exchange/test (type=1) 已调用")
            else:
                btn = self._find_element(SELECTORS["phone_share_btn"], timeout_ms=5000)
                if not btn:
                    print("  ⚠️ send_phone: 无法获取 securityId 且未找到按钮")
                    return False
                btn.click()
                print("  [换电话] 已点击换电话按钮")

            # 等弹窗 → 点「确定」
            confirm_clicked = self.page.evaluate("""() => {
                return new Promise((resolve) => {
                    let tries = 0;
                    const check = () => {
                        const btns = document.querySelectorAll('span');
                        for (const b of btns) {
                            if (b.innerText.trim() === '确定' && b.offsetParent !== null) {
                                const parent = b.closest('.secure-exchange, .sentence-popover, .panel-contact, [class*="exchange"], [class*="popover"]');
                                if (parent) {
                                    b.click();
                                    resolve(true);
                                    return;
                                }
                            }
                        }
                        const all = document.querySelectorAll('.btn-sure-v2, span');
                        for (const el of all) {
                            if (el.innerText.trim() === '确定' && el.offsetParent !== null && !el.closest('.btn-outline-v2')) {
                                el.click();
                                resolve(true);
                                return;
                            }
                        }
                        if (++tries < 30) setTimeout(check, 300);
                        else resolve(false);
                    };
                    check();
                });
            }""")
            if confirm_clicked:
                pause(0.5, 1)
                print("  [换电话] 已点确定按钮")
                return True

            print("  [换电话] 超时: 未找到确定按钮")
            return False

        except Exception as e:
            print(f"  ⚠️ send_phone 失败: {e}")
            return False

    def send_resume(self) -> bool:
        """点击「发简历」按钮，等弹窗后点「发送」确认。

        策略：
        1. 优先用 page.evaluate 在 JS 里精确按 innerText 匹配 toolbar-btn 元素
           （Playwright `:has-text` 是部分匹配，可能误中其他按钮）
        2. 用 click() + 多次重试，等待 toolbar 完全渲染
        3. 弹窗里点「发送」用同样的精确匹配
        """
        try:
            # 等待 toolbar 渲染（最多 8 秒）
            clicked = False
            for attempt in range(16):
                try:
                    found = self.page.evaluate(
                        """() => {
                            const btns = document.querySelectorAll('.toolbar-btn, [class*="toolbar"] [class*="btn"]');
                            for (const b of btns) {
                                const txt = (b.innerText || '').trim();
                                if (txt === '发简历' && b.offsetParent !== null) {
                                    b.click();
                                    return {ok: true, txt: txt};
                                }
                            }
                            // 兜底：找所有可见元素中文本完全等于"发简历"的可点击元素
                            const all = document.querySelectorAll('div, span, button, a');
                            for (const el of all) {
                                const txt = (el.innerText || el.textContent || '').trim();
                                if (txt === '发简历' && el.offsetParent !== null) {
                                    el.click();
                                    return {ok: true, txt: txt, fallback: true};
                                }
                            }
                            const all_btns = Array.from(document.querySelectorAll('.toolbar-btn, .toolbar-btn-content'));
                            const names = all_btns.map(b => `"${(b.innerText||'').trim()}"`).join(', ');
                            return {ok: false, available: names};
                        }"""
                    )
                    if isinstance(found, dict) and found.get("ok"):
                        print(
                            f"  [发简历] 已点击发简历按钮 (txt={found.get('txt')!r}{', fallback' if found.get('fallback') else ''})"
                        )
                        clicked = True
                        break
                    if attempt == 15 and isinstance(found, dict):
                        print(f"  ⚠️ send_resume: 未找到发简历按钮，当前工具栏: {found.get('available', '?')}")
                except Exception as e:
                    if attempt == 15:
                        print(f"  ⚠️ send_resume evaluate 失败: {e}")
                time.sleep(0.5)

            if not clicked:
                # 最后兜底：用原 selector
                btn = self._find_element(SELECTORS["resume_attach_btn"], timeout_ms=2000)
                if not btn:
                    return False
                btn.click()
                print("  [发简历] 已点击发简历按钮 (selector fallback)")

            pause(1.2, 2.2)

            # ==========================================
            # 弹窗出来后，需要先勾选一份简历，"发送"按钮才会从 disabled 变 enabled
            # ==========================================
            _resume_selected = False
            for _sel_attempt in range(10):
                try:
                    _sel_result = self.page.evaluate(
                        """() => {
                            const dlgs = document.querySelectorAll('.choose-resume-dialog, .boss-popup, [class*="dialog"], [class*="popup"], [class*="modal"]');
                            for (const dlg of dlgs) {
                                if (dlg.offsetParent === null) continue;
                                const radios = dlg.querySelectorAll('input[type="radio"], input[type="checkbox"]');
                                for (const r of radios) {
                                    if (r.offsetParent !== null && !r.checked) {
                                        const label = r.closest('label') || r.parentElement;
                                        if (label) { label.click(); return {ok: true, method: 'radio'}; }
                                    }
                                }
                                const items = dlg.querySelectorAll('li, .resume-item, [class*="resume"], [class*="item"]');
                                for (const item of items) {
                                    const txt = (item.innerText || '').trim();
                                    if ((txt.includes('在线简历') || txt.includes('附件简历')) && item.offsetParent !== null) {
                                        item.click();
                                        return {ok: true, method: 'resume-item', txt: txt.slice(0, 30)};
                                    }
                                }
                            }
                            const allRadios = document.querySelectorAll('input[type="radio"]:checked, input[type="checkbox"]:checked');
                            for (const r of allRadios) {
                                const txt = (r.closest('label')?.innerText || r.parentElement?.innerText || '').trim();
                                if (txt.includes('简历')) {
                                    return {ok: true, method: 'already-checked', txt: txt.slice(0, 30)};
                                }
                            }
                            return {ok: false};
                        }"""
                    )
                    if isinstance(_sel_result, dict) and _sel_result.get("ok"):
                        print(f"  [发简历] 已选择简历 ({_sel_result.get('method')})")
                        _resume_selected = True
                        break
                except Exception:
                    pass
                pause(0.8, 1.5)

            if not _resume_selected:
                # 可能简历已默认选中（弹窗里只有一份简历），直接尝试点发送
                print(f"  [发简历] 未找到可点击的简历项（可能已默认选中），直接尝试点发送")

            pause(1.5, 2.5)

            # 点「发送」按钮（用 JS 原生 click 绕过 chat-op 遮挡）
            for attempt in range(8):
                try:
                    confirmed = self.page.evaluate(
                        """() => {
                            // 直接找弹窗内的发送/确定按钮（不依赖特定 class 组合）
                            const dlgs = document.querySelectorAll('.choose-resume-dialog, .boss-popup, [class*="dialog"], [class*="popup"], [class*="modal"]');
                            for (const dlg of dlgs) {
                                if (dlg.offsetParent === null) continue;
                                const btns = dlg.querySelectorAll('button, .btn, [class*="btn"]');
                                for (const b of btns) {
                                    if (b.disabled || b.classList.contains('disabled')) continue;
                                    const txt = (b.innerText || '').trim();
                                    if (txt === '发送' || txt === '确定' || txt === '确认发送') {
                                        b.click(); return {ok: true, txt: txt};
                                    }
                                }
                            }
                            // 兜底：不限制在弹窗内，找页面上任何可见的发送/确定按钮
                            const allBtns = document.querySelectorAll('button, .btn, [class*="btn"]');
                            for (const b of allBtns) {
                                if (b.disabled || b.classList.contains('disabled')) continue;
                                if (b.offsetParent === null) continue;
                                const txt = (b.innerText || '').trim();
                                if (txt === '发送') {
                                    b.click(); return {ok: true, txt: txt, fallback: true};
                                }
                            }
                            return {ok: false};
                        }"""
                    )
                    if isinstance(confirmed, dict) and confirmed.get("ok"):
                        pause(0.8, 1.5)
                        print(f"  [发简历] 已点发送按钮 ({confirmed.get('sel') or confirmed.get('txt')})")
                        return True
                except Exception:
                    pass
                pause(0.8, 1.5)

            # 兜底：用 JS 直接点击发送按钮（绕过 chat-op overlay）
            try:
                self.page.evaluate("""() => {
                    const dlgs = document.querySelectorAll('.choose-resume-dialog, .boss-popup, [class*="dialog"], [class*="popup"]');
                    for (const dlg of dlgs) {
                        if (dlg.offsetParent === null) continue;
                        const btns = dlg.querySelectorAll('button');
                        for (const b of btns) {
                            if (b.disabled || b.classList.contains('disabled')) continue;
                            const txt = (b.innerText || '').trim();
                            if (txt === '发送' || txt === '确定' || txt === '确认发送') {
                                b.click();
                                return true;
                            }
                        }
                    }
                    return false;
                }""")
                pause(0.8, 1.5)
                print("  [发简历] 已点发送按钮 (JS fallback)")
                return True
            except Exception:
                pass

            print("  [发简历] 无弹窗，按钮已点击但未确认")
            return True
        except Exception as e:
            print(f"  ⚠️ send_resume 失败: {e}")
            return False

    # ══════════════════════════════════════
    #  页面扫描 & 一键投递
    # ══════════════════════════════════════

    def scan_current_page(self) -> List[dict]:
        """扫描当前BOSS搜索结果页，提取所有可见岗位卡片。不跳转，只读当前页。"""
        print(f"  [扫描] 开始扫描当前页面...")
        self._scroll_all()
        jobs = self._extract_job_cards()
        if not jobs:
            lines = [l.strip() for l in self.page.inner_text("body").split("\n") if l.strip()]
            sal_idx = [i for i, l in enumerate(lines) if re.search(r"\d+[-~]\d+K", decode_salary(l), re.I)]
            for n, si in enumerate(sal_idx):
                if n > 0 and si - sal_idx[n - 1] < 3:
                    continue
                if si == 0:
                    continue
                title = lines[si - 1]
                if not (2 < len(title) < 60):
                    continue
                salary = decode_salary(lines[si])
                company = exp = edu = city = ""
                end = sal_idx[n + 1] if n + 1 < len(sal_idx) else min(si + 10, len(lines))
                for j in range(si + 1, min(end, len(lines))):
                    ln = lines[j]
                    if "经验" in ln or "应届" in ln:
                        exp = ln
                    elif re.search(r"本科|硕士|博士|大专|学历不限", ln):
                        edu = ln
                    elif "·" in ln and len(ln) < 30:
                        city = ln
                    elif (
                        not company
                        and len(ln) > 2
                        and len(ln) < 40
                        and not re.search(r"年|学历|大专|本科|硕士|博士|不限|应届|·", ln)
                    ):
                        company = ln
                jobs.append(
                    {
                        "title": title,
                        "salary": salary,
                        "company": company,
                        "experience": exp,
                        "education": edu,
                        "city": city,
                        "url": "",
                        "description": "",
                        "hr_name": "",
                        "hr_title": "",
                    }
                )
            links = self._extract_links()
            if links:
                lm = {l["title"][:12]: l["href"] for l in links if l["title"][:12]}
                for j in jobs:
                    if not j["url"] and j["title"][:12] in lm:
                        j["url"] = lm[j["title"][:12]]
        print(f"  [扫描] 从当前页面提取到 {len(jobs)} 个岗位")
        return jobs

    # ══════════════════════════════════════
    #  公司画像（用于 smart-send：先看公司再投递）
    # ══════════════════════════════════════

    def goto_company_similar_jobs(self, job_url: str) -> str:
        """从岗位详情页找到"查看该公司其他职位"链接并跳过去，返回 companyId。
        找不到链接抛 RuntimeError。"""
        if not job_url:
            raise RuntimeError("缺少岗位URL")
        try:
            self.page.goto(job_url, wait_until="load", timeout=45000)
        except Exception as e:
            raise RuntimeError(f"打开岗位详情页失败: {e}")
        pause(1, 2)

        href = None
        for sel in SELECTORS["company_similar_jobs_link"]:
            try:
                loc = self.page.locator(sel).first
                if loc.count() > 0 and loc.is_visible():
                    href = loc.get_attribute("href")
                    if href and "/gongsi/job/" in href:
                        break
            except Exception:
                continue

        # 回退：找不到 jobs 页链接时，用本岗位卡片关联的公司介绍页 /gongsi/<id>.html
        if not href or "/gongsi/job/" not in (href or ""):
            try:
                cid = self.page.evaluate(
                    r"""() => {
                    // 优先取页面内指向公司主页的链接（排除推荐区）
                    const a = document.querySelector('a[ka="company-name"][href*="/gongsi/"]')
                        || document.querySelector('.company-info a[href*="/gongsi/"]')
                        || document.querySelector('a[href*="/gongsi/"]:not([href$="/gongsi/"])');
                    if (!a) return "";
                    const m = (a.getAttribute('href') || '').match(/\/gongsi\/(?:job\/)?([0-9a-zA-Z]+~?)/);
                    return m ? m[1] : "";
                }"""
                )
                if cid:
                    # 直接拼 jobs 页 URL 跳过去
                    full_url = f"https://www.zhipin.com/gongsi/job/{cid}.html"
                    self.page.goto(full_url, wait_until="load", timeout=45000)
                    pause(2, 3)
                    if not self.check_page_safety():
                        raise RuntimeError("公司页安全检查未通过（可能被风控）")
                    return cid
            except RuntimeError:
                raise
            except Exception:
                pass

        if not href:
            raise RuntimeError("未找到 '查看该公司其他职位' 链接")

        m = re.search(r"/gongsi/job/([0-9a-zA-Z]+~?)", href)
        company_id = m.group(1) if m else ""
        full_url = urljoin("https://www.zhipin.com", href.split("?")[0])
        try:
            self.page.goto(full_url, wait_until="load", timeout=45000)
        except Exception as e:
            raise RuntimeError(f"打开公司页失败: {e}")
        pause(2, 3)
        if not self.check_page_safety():
            raise RuntimeError("公司页安全检查未通过（可能被风控）")
        return company_id

    def fetch_company_legal_rep(self, company_id: str) -> str:
        """从 BOSS 公司列表页注入对象里拿法定代表人。

        关键发现: BOSS 公司列表页
        https://www.zhipin.com/gongsi/job/<encryptBrandId>.html
        在 window.__BOSSCOMPANY__ 注入的初始数据里,brandInfo 节点
        直接带 `legalPerson`(法定代表人姓名)。

        注入结构路径: __BOSSCOMPANY__[1].data[0].brandJobInfo.brandComInfo
        实际不同版本路径可能不同,这里采用多路径容错:
          1. window.__BOSSCOMPANY__ 的 brandInfo.legalPerson
          2. window.__INITIAL_STATE__ 兜底
        若都拿不到则返回空字符串,不影响主流程。
        """
        cid = (company_id or "").strip()
        if not cid:
            return ""
        intro_url = f"https://www.zhipin.com/gongsi/{cid}.html"
        try:
            self.page.goto(intro_url, wait_until="load", timeout=45000)
        except Exception:
            return ""
        pause(1, 2)
        if not self.check_page_safety():
            return ""

        try:
            js = r"""
            () => {
                function tryRead(obj, path) {
                    try {
                        var cur = obj;
                        var parts = path.split('.');
                        for (var i = 0; i < parts.length && cur; i++) {
                            cur = cur[parts[i]];
                        }
                        return (typeof cur === 'string') ? cur : '';
                    } catch (e) { return ''; }
                }
                // 来自真实 BOSS 公司介绍页 __BOSSCOMPANY__ 注入结构路径
                var paths = [
                    '__BOSSCOMPANY__.brandInfo.legalPerson',
                    '__BOSSCOMPANY__.comInfo.legalPerson',
                    '__BOSSCOMPANY__.brandComInfo.legalPerson',
                    '__BOSSCOMPANY__.[1].data.[0].brandInfo.legalPerson',
                    '__BOSSCOMPANY__.[1].data.[0].brandComInfo.legalPerson',
                    '__BOSSCOMPANY__.[1].data.[0].comInfo.legalPerson',
                    '__INITIAL_STATE__.data.brandInfo.legalPerson'
                ];
                for (var i = 0; i < paths.length; i++) {
                    var segs = paths[i].split('.');
                    var root = segs[0];
                    var rest = segs.slice(1).join('.');
                    var v = tryRead(window[root], rest);
                    if (v) return v;
                }
                return '';
            }
            """
            result = self.page.evaluate(js) or ""
        except Exception:
            return ""

        result = str(result).strip()
        if result and len(result) <= 8 and not any(c in result for c in ("(", "（", " ", "\n")):
            return result
        return ""

    def parse_company_similar_jobs_page(self) -> dict:
        """解析 /gongsi/job/<id>.html 页面。
        返回 {open_count:int, jobs:[{title,salary,url,hr_block,hr_name,hr_title,company}], page_url:str, company:str}。

        核心策略：用 page.inner_text('body') 拿纯文本 → 按行分块解析。
        BOSS 公司页格式（每个岗位块）：
            标题
            薪资（5-8K）
            经验（1-3年）
            学历（大专）
            HR名·头衔
            城市·区·地标
        用"薪资行"作为锚点，往上取标题，往下取 HR。
        """
        result = {"open_count": 0, "jobs": [], "page_url": self.page.url, "company": ""}

        try:
            body_text = self.page.inner_text("body")
        except Exception as e:
            print(f"  [公司页] inner_text 失败: {e}")
            return result

        lines = [l.strip() for l in body_text.split("\n") if l.strip()]

        # 1) open_count：从文本里匹配 "11在招职位" / "在招 11 个" / "招聘职位(11)"
        for ln in lines[:30]:
            m = re.search(r"(\d+)\s*在招\s*职位", ln)
            if not m:
                m = re.search(r"在招\s*(\d+)\s*个", ln)
            if not m:
                m = re.search(r"招聘职位\s*\(\s*(\d+)\s*\)", ln)
            if not m:
                m = re.search(r"(\d+)\s*个在招", ln)
            if m:
                result["open_count"] = int(m.group(1))
                break

        # 2) 公司名：从页面 title 或文本中拿
        try:
            page_title = self.page.title() or ""
            # 格式："「淄博齐行家体育文...招聘」2026年..."  或 "XX公司招聘..."
            company_name = re.sub(r"[「【]", "", page_title)
            company_name = re.sub(r"招聘.*$", "", company_name)
            company_name = re.sub(r"[」】]", "", company_name).strip()
            if company_name:
                result["company"] = company_name
        except Exception:
            pass

        # 3) 解析岗位块。BOSS 公司页岗位块结构：
        #    标题 / 经验(3-5年|经验不限) / 学历(本科|大专|硕士|学历不限) / HR名·头衔 / 城市·区·地标
        #    注意：公司页岗位卡不一定显示薪资，所以用 HR 行作为锚点（最稳定），往上找标题。
        salary_re = re.compile(r"^\d+[-~]\d+K|\d+-\d+元|^\d+元/[时月]", re.I)
        exp_re = re.compile(r"^(?:\d+[-~]\d+年|\d+年以[上下]|经验不限|应届生?|在校生?|\d+年内)$")
        edu_re = re.compile(r"^(?:初中及?以下|高中|中专|大专|本科|硕士|博士|学历不限|MBA|EMBA)$")
        hr_re = re.compile(
            r"^([\u4e00-\u9fa5]{2,4})\s*[·•・]\s*"
            r"(HRBP|HR|猎头|顾问|招聘[\u4e00-\u9fa5]{0,3}|HR[\u4e00-\u9fa5]{0,3}|"
            r"[\u4e00-\u9fa5]{0,4}(?:经理|主管|总监|负责人|助理|专员|人事))$"
        )

        # 先提取所有 job_detail 链接
        try:
            links = (
                self.page.evaluate(r"""() => {
                const r = []; const s = new Set();
                document.querySelectorAll('a[href*="/job_detail/"]').forEach(a => {
                    const h = a.href, t = (a.innerText || '').trim();
                    if (h && t && !s.has(h) && t.length < 80) { s.add(h); r.push({href:h, title:t.substring(0,60)}); }
                });
                return r;
            }""")
                or []
            )
        except Exception:
            links = []
        link_map = {}
        for lk in links:
            key = (lk.get("title") or "")[:12]
            if key:
                link_map[key] = lk.get("href", "")

        # 以 HR 行为锚点定位岗位块
        jobs = []
        seen_titles = set()
        hr_indices = [i for i, ln in enumerate(lines) if hr_re.match(ln)]
        for hi in hr_indices:
            m = hr_re.match(lines[hi])
            if not m:
                continue
            hr_name = m.group(1)
            hr_title = m.group(2)
            hr_block = lines[hi]

            # 往上跳过 学历 / 经验 / 薪资 行，剩下的第一行即标题
            ti = hi - 1
            salary = ""
            while ti >= 0 and (edu_re.match(lines[ti]) or exp_re.match(lines[ti]) or salary_re.match(lines[ti])):
                if salary_re.match(lines[ti]) and not salary:
                    salary = lines[ti]
                ti -= 1
            if ti < 0:
                continue
            title = lines[ti]
            if not (2 < len(title) < 80):
                continue
            if title in seen_titles:
                continue
            # 过滤明显非岗位标题的导航/区块行
            if any(bad in title for bad in ("招聘职位", "公司简介", "职位类型", "工作城市", "全部(")):
                continue
            seen_titles.add(title)

            url = link_map.get(title[:12], "")
            jobs.append(
                {
                    "title": title,
                    "salary": salary,
                    "url": url,
                    "hr_block": hr_block,
                    "hr_name": hr_name,
                    "hr_title": hr_title,
                    "company": result.get("company") or "",
                }
            )

        result["jobs"] = jobs
        return result

    def aggregate_company_hrs(self, jobs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """从 parse_company_similar_jobs_page 返回的 jobs 列表里聚合去重 HR。
        每条 hr: {name, title, priority, associated_jobs:[...]}。
        同一 HR 出现在多个岗位时，职务取最高（按 _hr_title_score）。"""
        hrs_map: Dict[str, Dict[str, Any]] = {}
        hr_name_re = re.compile(
            r"([\u4e00-\u9fa5]{2,4})\s*[·｜\|\s]\s*"
            r"((?:HRBP|HR|猎头|顾问)|招聘[\u4e00-\u9fa5]{0,3}|"
            r"HR[\u4e00-\u9fa5]{0,3}|"
            r"[\u4e00-\u9fa5]{0,4}(?:经理|主管|总监|负责人|助理|专员|人事))"
        )
        for j in jobs or []:
            block = (j.get("hr_block") or "").strip()
            if not block:
                continue
            for m in hr_name_re.finditer(block):
                name = m.group(1).strip()
                title = m.group(2).strip()
                if name not in hrs_map:
                    hrs_map[name] = {
                        "name": name,
                        "title": title,
                        "priority": _hr_title_score(title),
                        "associated_jobs": [],
                    }
                else:
                    cur_score = hrs_map[name]["priority"]
                    new_score = _hr_title_score(title)
                    if new_score > cur_score:
                        hrs_map[name]["title"] = title
                        hrs_map[name]["priority"] = new_score
                if j.get("title") and j["title"] not in hrs_map[name]["associated_jobs"]:
                    hrs_map[name]["associated_jobs"].append(j["title"])
        hrs = list(hrs_map.values())
        hrs.sort(key=lambda h: (-h["priority"], h["name"]))
        for h in hrs:
            h["is_top"] = bool(hrs) and h["name"] == hrs[0]["name"]
        return hrs

    # 注：原先这里有个 scan_and_apply_current_page()——「扫描浏览器当前页 → 批量投递」。
    # 它已删除：apply_to_job 会 goto 到岗位详情页且不返回，所以投过任意一个岗位之后
    # 「当前页」就不再是搜索结果页，扫描恒为 0。一键投递现在由
    # /api/boss/jobs/scan-and-apply 从岗位库取 status=pending 的岗位来做。

    # ══════════════════════════════════════
    #  监控周期（供后台循环调用）
    # ══════════════════════════════════════

    def _click_chat_tab(self, label: str) -> bool:
        """切换聊天页顶部 Tab（全部/未读/新招呼/仅沟通）。

        Tab 也是 `li`/`span` 元素，同样踩 locator.click() 卡死的坑，所以走坐标点击。
        """
        for sel in (f'span.label-name:has-text("{label}")', f'.label-name:has-text("{label}")'):
            try:
                tab = self.page.locator(sel).first
                if tab.count() > 0 and tab.is_visible():
                    res = self._click_by_coordinates(tab)
                    if res["ok"]:
                        pause(0.8, 1.5)
                        return True
                    print(f"  ⚠️ 切换 Tab「{label}」失败: {res['reason']}")
            except Exception as e:
                print(f"  ⚠️ 切换 Tab「{label}」异常: {e}")
        return False

    def sync_conversation_list_full(self) -> int:
        """在「全部」Tab 下抽取每个会话卡片的 hr_name / company / job_title / last_msg
        并写回数据库。返回更新的会话数。失败返回 0，不抛异常。
        """
        try:
            self._click_chat_tab("全部")
        except Exception:
            pass
        try:
            cards = self.page.evaluate(
                """() => {
                const out = [];
                const seen = new Set();
                const items = document.querySelectorAll('li[role="listitem"], .user-list li');
                const visible = el => {
                    const r = el.getBoundingClientRect();
                    const s = getComputedStyle(el);
                    return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
                };
                const ROLE_RE = /(招聘者|招聘专员|招聘主管|招聘总监|HR(经理|主管|总监|专员)?|人力(资源)?(专员|经理|主管|总监|顾问)?|人力|人事(经理|主管|总监|专员)?|人事|猎头(顾问|经理)?|猎头|顾问|总监|经理|老板|创始人|合伙人|首席执行官|CEO|CTO|COO|CFO|VP|主管|专员|区域)$/;
                let pos = 0;
                for (const el of items) {
                    if (!visible(el)) continue;
                    const fullText = (el.innerText || '').trim();
                    if (!fullText || fullText.length < 2) continue;
                    const key = fullText.replace(/\\s+/g, ' ').slice(0, 80);
                    if (seen.has(key)) continue;
                    seen.add(key);
                    let hr = '';
                    try {
                        const nm = el.querySelector('.name-text, .geek-name, [class*="name-text"]');
                        if (nm) hr = (nm.innerText || '').trim();
                    } catch(e) {}
                    let wrap = '';
                    try {
                        const w = el.querySelector('[class*="name"]:not(.name-text):not(.geek-name)');
                        if (w) wrap = (w.innerText || '').trim();
                    } catch(e) {}
                    if (!hr && wrap) hr = (wrap.split('\\n')[0] || '').trim();
                    let after = wrap;
                    if (hr && wrap.startsWith(hr)) after = wrap.slice(hr.length).trim();
                    let role = '';
                    let company = after;
                    const m = ROLE_RE.exec(after);
                    if (m) {
                        role = m[0];
                        company = after.slice(0, m.index).trim();
                    }
                    let lastMsg = '';
                    try {
                        const lm = el.querySelector('[class*="last"], [class*="msg-text"], [class*="message-text"]');
                        if (lm) lastMsg = (lm.innerText || '').trim();
                    } catch(e) {}
                    lastMsg = lastMsg.replace(/^\\[(送达|已读|未读|发送失败|已发送)\\]\\s*/g, '').trim();
                    const unreadEl = el.querySelector('.red-dot, [class*="unread"], [class*="badge"]');
                    const unread = !!(unreadEl && visible(unreadEl));
                    out.push({
                        hr_name: hr,
                        company: company,
                        role: role,
                        job_title: '',
                        last_msg: lastMsg,
                        unread: unread,
                        raw: fullText.slice(0, 200),
                        position: pos++,
                    });
                }
                return out;
            }"""
            )
        except Exception as e:
            print(f"  [sync-list] DOM 抽取失败: {e}")
            return 0

        if not cards:
            return 0

        try:
            from boss_state import get_db, list_active_conversations
        except Exception:
            return 0

        known = {c["hr_name"]: c for c in list_active_conversations() if c.get("hr_name")}
        updated = 0
        db = get_db()
        for c in cards:
            hr = (c.get("hr_name") or "").strip()
            if not hr or len(hr) < 2:
                continue
            company = (c.get("company") or "").strip()
            job = (c.get("job_title") or "").strip()
            last_msg = (c.get("last_msg") or "").strip()
            kc = known.get(hr)
            if not kc:
                continue
            sets = []
            args: list = []
            old_company = (kc.get("hr_company") or "").strip()
            if company and company != old_company and len(company) >= 2:
                sets.append("hr_company=?")
                args.append(company[:80])
            if job and not kc.get("job_title"):
                sets.append("job_title=?")
                args.append(job[:80])
            old_last = (kc.get("last_message_text") or "").strip()
            if last_msg and last_msg != old_last:
                sets.append("last_message_text=?")
                args.append(last_msg[:200])
            if not sets:
                continue
            sets.append("updated_at=CURRENT_TIMESTAMP")
            args.append(kc["id"])
            try:
                db.execute(f"UPDATE conversations SET {', '.join(sets)} WHERE id=?", tuple(args))
                updated += 1
            except Exception:
                pass
        try:
            db.commit()
        except Exception:
            pass
        if updated:
            print(f"  [sync-list] 回填 {updated} 个会话字段")
        return updated

    def run_chat_monitor_cycle(self) -> dict:
        """
        一个完整的监控周期:
        1. 导航到聊天页
        2. 扫描未读会话
        3. 对每个未读会话: 打开→读消息→存库→AI回复

        注意：用户可能正在前台等浏览器（见 _should_yield），所以每个会话开头
        都要查一次并提前收尾。
        """
        import datetime
        _t_start = time.time()
        print(f"\n{'='*60}")
        print(f"[监控] ====== 开始新一轮监控周期 {datetime.datetime.now().strftime('%H:%M:%S')} ======")
        result = {"checked": 0, "new_messages": 0, "replies_sent": 0}

        # 只在不在聊天页时才导航（避免每轮刷新页面，触发 BOSS 登录检查）
        current_url = self.page.url
        need_nav = "/web/geek/chat" not in current_url
        print(f"[监控] 当前URL: {current_url[:80]}  need_nav={need_nav}")
        if need_nav:
            print(f"[监控] 导航到聊天页...")
            if not self.navigate_to_chat():
                print(f"[监控] ❌ 导航到聊天页失败")
                return result
            print(f"[监控] ✅ 导航完成")

        # 先在「全部」Tab 下抓一次完整列表，回填 company/job_title/last_msg（修 P10 漏字段）
        print(f"[监控] 同步会话列表...")
        try:
            self.sync_conversation_list_full()
            print(f"[监控] ✅ 会话列表同步完成")
        except Exception as e:
            print(f"[监控] ⚠️ sync_conversation_list_full 异常: {e}")

        # 切到「未读」Tab 处理待回复会话。
        #
        # 必须**无条件**切：sync_conversation_list_full() 为了回填字段会点「全部」，
        # 把 navigate_to_chat() 刚选好的「未读」顶掉了。原实现写成 `if not need_nav`
        # （意思是"刚导航过就别重复点"），结果只要本轮发生了导航，列表就停在「全部」
        # —— poll_conversation_list() 会把**全部联系人**当成未读返回，而本轮又只处理
        # 前 3 个。真正的 HR 回复只要排在列表第 4 位之后，就永远轮不到回复。
        self._click_chat_tab("未读")

        if not self.check_page_safety():
            print(f"[监控] ❌ 安全检查未通过（登录过期/验证码等），终止本轮")
            return result

        conversations = self.poll_conversation_list()
        result["checked"] = len(conversations)
        print(f"[监控] 扫描到 {len(conversations)} 个会话")
        try:
            preview = (self.page.inner_text("body") or "")[:800].replace("\n", " | ")
            print(f"[监控] Body预览: {preview}")
        except Exception:
            pass

        from boss_state import list_active_conversations

        known_convs = list_active_conversations()
        print(f"[监控] DB已知活跃会话: {len(known_convs)}个")

        if not conversations:
            print(f"[监控] 无未读消息，周期结束")
            print(f"{'='*60}\n")
            return result
        if len(conversations) > 3:
            print(f"[监控] 未读会话 {len(conversations)} 个，本轮处理前3个")
            conversations = conversations[:3]

        _processed_conv_ids = set()  # 追踪已处理的会话ID，供DB兜底去重
        _reply_count = 0             # 本轮已回复数，最多3条
        _MAX_REPLIES_THIS_CYCLE = 3

        for idx, conv_data in enumerate(conversations):
            if scan_should_yield(self):
                print("  [监控] 有前台操作在等浏览器，提前结束本轮扫描")
                break
            print(f"\n[监控] --- 处理会话 {idx+1}/{len(conversations)} ---")
            text = conv_data.get("text", "")
            has_unread = conv_data.get("has_unread", False)
            element = conv_data.get("element")

            if not text:
                continue

            # 尝试匹配已知会话：用提取的 HR 名字精确匹配
            matched_conv = None
            extracted_name = conv_data.get("hr_name", "")
            for kc in known_convs:
                kc_name = kc.get("hr_name", "")
                if kc_name and extracted_name and kc_name == extracted_name:
                    matched_conv = kc
                    break

            if not matched_conv:
                for kc in known_convs:
                    kc_name = kc.get("hr_name", "")
                    if kc_name and len(kc_name) >= 3 and kc_name in text:
                        matched_conv = kc
                        break

            if not matched_conv:
                lines = [l.strip() for l in text.split("\n") if l.strip()]
                hr_name = conv_data.get("hr_name", "") or lines[0] if lines else ""
                hr_name = hr_name[:20] if len(hr_name) > 20 else hr_name

                # 过滤无效名称
                skip_keywords = [
                    "消息",
                    "联系人",
                    "沟通",
                    "设置",
                    "搜索",
                    "我的",
                    "首页",
                    "已沟通",
                    "继续沟通",
                    "新对话",
                    "系统",
                    "通知",
                    "BOSS",
                    "在线",
                    "离线",
                    "刚刚",
                    "分钟",
                    "小时",
                    "昨天",
                    "简历",
                    "附件",
                    "上传",
                    "制作",
                    "更新",
                    "AI",
                ]
                is_valid = (
                    hr_name
                    and len(hr_name) >= 2
                    and not hr_name.isdigit()
                    and not any(kw == hr_name for kw in skip_keywords)
                    and not any(kw in hr_name and len(hr_name) <= len(kw) + 1 for kw in skip_keywords)
                )
                if not is_valid:
                    print(f"  [监控] 跳过无效会话名: '{hr_name}' (原文: {text[:50]})")
                    continue

                conv_id = get_or_create_conversation(
                    None, hr_name, conv_data.get("company", ""), conv_data.get("job_title", "")
                )
                known_convs = list_active_conversations()
                matched_conv = get_conversation(conv_id)
                if not matched_conv:
                    continue
                print(f"  [监控] 新建会话: {hr_name}")
                _processed_conv_ids.add(conv_id)
                # 标记用于 WebSocket 广播
                result.setdefault("new_conversations", []).append(hr_name)
            else:
                conv_id = matched_conv["id"]
                _processed_conv_ids.add(conv_id)
                # 提取的名字比 DB 更精确时自动修正
                if extracted_name and len(extracted_name) >= 2:
                    old_name = matched_conv.get("hr_name", "")
                    if old_name != extracted_name and (
                        old_name in extracted_name or extracted_name in old_name or len(extracted_name) < len(old_name)
                    ):
                        try:
                            from boss_state import get_db as _gdb2

                            _gdb2().execute("UPDATE conversations SET hr_name=? WHERE id=?", (extracted_name, conv_id))
                            _gdb2().commit()
                            matched_conv["hr_name"] = extracted_name
                        except Exception:
                            pass

            # 从会话文本里提取公司名（格式：HR名+公司名+岗位）
            if not matched_conv.get("hr_company"):
                company_info = text.split("\n")[0] if "\n" in text else text
                import re as _re3

                hr_name_part = matched_conv.get("hr_name", "")
                if hr_name_part and len(hr_name_part) >= 2:
                    company_info = company_info.replace(hr_name_part, "", 1)
                # 去掉时间/状态/括号等
                company_info = _re3.sub(r"\d{1,2}:\d{2}|\[.*?\]|送达|已读|未读", "", company_info)
                # 提取公司名（纯中文 4-12字）
                m = _re3.search(r"[\u4e00-\u9fa5]{4,12}", company_info)
                if m:
                    company = m.group()
                    try:
                        from boss_state import get_db as _gdb3

                        _gdb3().execute("UPDATE conversations SET hr_company=? WHERE id=?", (company, conv_id))
                        _gdb3().commit()
                        matched_conv["hr_company"] = company
                        print(f"  [监控] 提取公司名: {company}")
                    except Exception:
                        pass

            if matched_conv.get("status") != "active":
                print(f"  [监控] 跳过: 会话状态={matched_conv.get('status')}，非active")
                continue
            if not matched_conv.get("auto_reply_enabled"):
                print(f"  [监控] 跳过: auto_reply_enabled=False")
                continue

            # 读取消息：打开会话从 DOM 提取
            hr_name_to_open = matched_conv["hr_name"]
            print(f"  [监控] 匹配到会话 id={conv_id} hr={hr_name_to_open}，正在打开...")
            opened = self.open_conversation_by_name(hr_name_to_open)
            if not opened and len(hr_name_to_open) > 4:
                short = re.match(r"^[\u4e00-\u9fff]{2,3}", hr_name_to_open)
                if short:
                    opened = self.open_conversation_by_name(short.group(0))
            if not opened:
                print(f"  [监控] 无法打开会话: {hr_name_to_open}")
                continue
            pause(1, 2)
            msgs = self.read_visible_messages()
            print(f"  [监控] 会话 {matched_conv.get('hr_name')}: 读到 {len(msgs)} 条消息")

            new_count = 0
            clean_msgs = []
            for msg in msgs:
                sender = msg.get("sender", "hr")
                content = (msg.get("content") or "").strip()
                if not content:
                    continue
                clean_msgs.append({"sender": sender, "content": content, "status": msg.get("status", "")})

            if clean_msgs:
                replace_conversation_messages(conv_id, clean_msgs)
                last_msg = clean_msgs[-1]
                update_conversation_last_message(conv_id, last_msg["content"], last_msg["sender"], 0)

                # 从 HR 消息里提取微信号
                if not matched_conv.get("hr_wechat"):
                    import re as _re

                    for m in clean_msgs:
                        if m["sender"] == "hr":
                            patterns = [
                                # wxid_xxxxxxxx 格式
                                r"(?:wxid|WXID)[_\-]?\s*[:：]?\s*([a-zA-Z0-9_-]{6,30})",
                                # 微信/VX/WeChat：xxx 格式
                                r"(?:微信|VX|vx|wechat|WeChat)[号：:]*\s*[:：]?\s*([a-zA-Z0-9_-]{4,30})",
                                # 加我/加V -> xxx
                                r"(?:加我|加V|找V|加个V)\s*[:：]?\s*([a-zA-Z0-9_-]{4,30})",
                                # 微信号 xxx（纯中文前缀）
                                r"\u5fae\u4fe1\u53f7\s+([a-zA-Z0-9_-]{4,30})",
                            ]
                            for pat in patterns:
                                match = _re.search(pat, m["content"])
                                if match:
                                    wx_id = match.group(1).strip()
                                    if wx_id and len(wx_id) >= 5:
                                        update_conversation_wechat(conv_id, wx_id)
                                        matched_conv["hr_wechat"] = wx_id
                                        result["wechat_exchanged"] = True
                                        print(f"  [监控] 提取HR微信: {wx_id}")
                                        break

            # 检测需要回复的 HR 消息：跳过 BOSS 的系统卡片（见 is_system_notification）
            unreplied_hr_msg = None
            for i in range(len(clean_msgs) - 1, -1, -1):
                m = clean_msgs[i]
                if m["sender"] == "me":
                    continue
                if is_system_notification(m["content"]):
                    continue
                # HR 消息
                has_reply_after = any(clean_msgs[j]["sender"] == "me" for j in range(i + 1, len(clean_msgs)))
                if not has_reply_after:
                    unreplied_hr_msg = m["content"]
                    new_count = 1
                    print(f"  [监控] 待回复HR消息: {m['content'][:60]}...")
                break

            if unreplied_hr_msg:
                result["new_messages"] += 1
            else:
                print(f"  [监控] 无待回复HR消息 (msgs={len(clean_msgs)}, last_sender={clean_msgs[-1]['sender'] if clean_msgs else 'none'})")

            # 自动回复
            auto_reply_enabled = auto_reply_allowed()
            if unreplied_hr_msg and not auto_reply_enabled:
                print(f"  [监控] 有待回复消息但自动回复已关闭，跳过")
            if unreplied_hr_msg and auto_reply_enabled and _reply_count >= _MAX_REPLIES_THIS_CYCLE:
                print(f"  [监控] 本轮已回复{_reply_count}条(上限{_MAX_REPLIES_THIS_CYCLE})，该会话消息已保存，下轮或DB兜底处理")
            if unreplied_hr_msg and auto_reply_enabled and _reply_count < _MAX_REPLIES_THIS_CYCLE:
                print(f"[监控] 自动回复: 已启用, 开始生成回复...")
                today_replies = get_today_auto_reply_count()
                if today_replies >= MAX_AUTO_REPLY_PER_DAY:
                    print(f"[监控] 自动回复: 已达日上限({MAX_AUTO_REPLY_PER_DAY}条), 跳过")
                    continue

                # 风控冷却期：本轮不发消息
                if not self._respect_cooldown():
                    print(f"[监控] 自动回复: 风控冷却中, 跳过")
                    continue

                try:
                    from boss_replier import generate_reply

                    job_title = matched_conv.get("job_title", "")
                    job_company = matched_conv.get("hr_company", "")
                    job_desc = ""
                    app = None
                    app_id = matched_conv.get("application_id")
                    if app_id:
                        from boss_state import get_application

                        app = get_application(app_id)
                        if app:
                            job_desc = app.get("description") or ""
                            job_title = job_title or app.get("job_title", "")
                            job_company = job_company or app.get("company", "")

                    job_info = {
                        "title": job_title,
                        "company": job_company,
                        "description": job_desc,
                        "city": app.get("city", "") if app else "",
                    }
                    style = get_setting("ai_reply_style", "professional")
                    resume = get_setting("resume_summary", "")
                    wechat = get_setting("wechat_id", "")

                    # 检测线下面试要求并记录
                    from boss_state import _detect_offline_interview_requirement, _extract_city_from_text
                    from boss_state import save_offline_interview_location
                    if _detect_offline_interview_requirement(unreplied_hr_msg):
                        city, detail = _extract_city_from_text(unreplied_hr_msg)
                        # 如果消息里没提取到城市，尝试从岗位信息取
                        if not city:
                            city = job_info.get("city", "")
                        save_offline_interview_location(
                            conv_id,
                            hr_name=matched_conv.get("hr_name", ""),
                            hr_company=matched_conv.get("hr_company", ""),
                            job_title=job_title,
                            city=city,
                            location_detail=detail,
                            hr_message=unreplied_hr_msg,
                        )
                        print(f"  [监控] 检测到线下面试要求 → 城市: {city or '未识别'} 详情: {detail or '无'}")

                    # 生成前再过一次闸：开关已关就不必白花一次 LLM 调用
                    if not auto_reply_allowed():
                        print("  [监控] 自动回复已关闭，跳过本轮生成")
                        continue

                    gen = generate_reply(conv_id, unreplied_hr_msg, job_info, style, resume, wechat)
                    reply = gen["reply"]
                    interest = gen.get("interest", "")
                    if gen.get("transfer"):
                        update_conversation_transfer_requested(conv_id)
                        result["transfer_requested"] = True
                    if reply:
                        # 发送前的最后一道闸：生成 + 下面的拟人停顿加起来可能十几秒，
                        # 用户完全可能在这段窗口里点「停止回复」。此处漏检就等于
                        # "设了停止却没真停"，所以每一条消息都必须现场重读开关。
                        if not auto_reply_allowed():
                            print("  [监控] 自动回复已在生成期间被关闭，放弃发送本条回复")
                            continue
                        # 先执行发送操作（简历/微信/电话），确保AI说"已发送"时东西已经发出去了
                        msg_lower = unreplied_hr_msg.lower()

                        # 发简历：HR明确要求简历时，且未发送过
                        if any(kw in msg_lower for kw in ("简历", "cv", "resume")):
                            if not matched_conv.get("resume_sent"):
                                print(f"  [监控] HR要简历，正在处理...")
                                # 先判断是不是 BOSS 系统级简历索取（消息里有"同意"按钮）
                                resume_agreed = False
                                if "同意" in unreplied_hr_msg and ("是否同意" in unreplied_hr_msg or "附件简历" in unreplied_hr_msg):
                                    print(f"  [监控] 检测到BOSS系统简历索取，尝试点击聊天内「同意」按钮...")
                                    resume_agreed = self._click_chat_agree_button()
                                    if resume_agreed:
                                        print(f"  [监控] 已点击同意发送简历")
                                    else:
                                        print(f"  [监控] 聊天内未找到同意按钮，回退工具栏发简历")
                                if not resume_agreed:
                                    resume_agreed = self.send_resume()
                                if resume_agreed:
                                    from boss_state import mark_resume_sent

                                    mark_resume_sent(conv_id)
                                    pause(1, 2)

                        # 换微信：HR主动要联系方式时（排除"保持联系"等模糊表达）
                        wechat_keywords = (
                            "加微信",
                            "加个微信",
                            "微信聊",
                            "vx",
                            "加v",
                            "v我",
                            "加个v",
                            "微信号",
                            "换微信",
                        )
                        if any(kw in msg_lower for kw in wechat_keywords):
                            if not matched_conv.get("hr_wechat"):
                                print(f"  [监控] HR要微信，正在发送...")
                                self.send_wechat(hr_name_to_open)
                                pause(1, 2)

                        # 换电话：HR明确要电话时，且未发送过
                        if any(kw in msg_lower for kw in ("电话", "手机号")):
                            if not matched_conv.get("phone_shared"):
                                print(f"  [监控] HR要电话，正在发送...")
                                if self.send_phone(hr_name_to_open):
                                    from boss_state import mark_phone_shared

                                    mark_phone_shared(conv_id)
                                    pause(1, 2)

                        # 然后再发送AI回复
                        print(f"  [监控] AI回复: {reply[:60]}...")
                        # 两次高风险操作之间保持人性化间隔
                        self._human_pace(min_gap=5.0, max_gap=15.0)
                        # 模拟真人"看到消息→思考→打字"的延迟
                        think = random.uniform(1, 3) + min(len(reply) * 0.03, 3)
                        time.sleep(think)
                        if self.send_message(reply):
                            add_message(conv_id, "me", reply, ai_generated=True)
                            update_conversation_last_message(conv_id, reply, "me", 0)
                            increment_daily_stat("auto_replies_sent")
                            result["replies_sent"] += 1
                            _reply_count += 1
                            if interest:
                                update_conversation_interest(conv_id, interest)
                                print(f"  [监控] HR兴趣度: {interest}")
                            print(f"  [监控] 回复已发送")
                        else:
                            print(f"  [监控] 回复发送失败!")
                        pause(2, 5)

                        # ══════════════════════════════════════
                        # 回复后再扫描：防止HR在等待期间发新消息被自动已读
                        # 问题：回复+等待期间HR回复多条消息，BOSS自动已读，
                        # 下个周期 poll_conversation_list 在"未读"tab看不到此会话
                        # 解决：回复后重新扫描当前会话，有新未回复HR消息则追加回复
                        # ══════════════════════════════════════
                        _recheck_rounds = 0
                        _max_recheck = 2
                        while _recheck_rounds < _max_recheck:
                            _recheck_rounds += 1
                            pause(1, 2)
                            _msgs2 = self.read_visible_messages()
                            _clean2 = []
                            for _m2 in _msgs2:
                                _s2 = _m2.get("sender", "hr")
                                _c2 = (_m2.get("content") or "").strip()
                                if not _c2:
                                    continue
                                _clean2.append({"sender": _s2, "content": _c2, "status": _m2.get("status", "")})

                            if not _clean2 or len(_clean2) <= len(clean_msgs):
                                print(f"  [监控] 无新消息，退出再检查 (round={_recheck_rounds})")
                                break

                            # 有新消息，保存到DB
                            print(f"  [监控] 检测到新消息 ({len(_clean2)} 条, 原{len(clean_msgs)}条)，重新保存")
                            replace_conversation_messages(conv_id, _clean2)

                            # 找最新一条未回复的HR消息
                            _new_hr_msg = None
                            for _i2 in range(len(_clean2) - 1, -1, -1):
                                _m2 = _clean2[_i2]
                                if _m2["sender"] == "me":
                                    continue
                                if is_system_notification(_m2["content"]):
                                    continue
                                _has_my_reply = any(
                                    _clean2[_j]["sender"] == "me" for _j in range(_i2 + 1, len(_clean2))
                                )
                                if not _has_my_reply:
                                    _new_hr_msg = _m2["content"]
                                    break

                            if not _new_hr_msg:
                                print(f"  [监控] 新消息已全部回复，退出再检查")
                                break

                            print(f"  [监控] 再检查发现待回复HR消息: {_new_hr_msg[:60]}...")

                            # 追回回复同样要过闸。此前这个循环只查日配额和风控冷却，
                            # 完全没查开关 —— 点「停止回复」后它还能再发最多 2 条。
                            if not auto_reply_allowed():
                                print("  [监控] 自动回复已关闭，放弃本轮追回回复")
                                break

                            # 检查日配额
                            _today_replies = get_today_auto_reply_count()
                            if _today_replies >= MAX_AUTO_REPLY_PER_DAY:
                                print(f"  [监控] 再回复已达日上限({MAX_AUTO_REPLY_PER_DAY}条), 跳过")
                                break

                            if not self._respect_cooldown():
                                print(f"  [监控] 再回复风控冷却中, 跳过")
                                break

                            try:
                                _gen2 = generate_reply(conv_id, _new_hr_msg, job_info, style, resume, wechat)
                                _reply2 = _gen2["reply"]
                                if _reply2:
                                    self._human_pace(min_gap=3.0, max_gap=8.0)
                                    _think2 = random.uniform(1, 2) + min(len(_reply2) * 0.03, 3)
                                    time.sleep(_think2)
                                    if not auto_reply_allowed():
                                        print("  [监控] 自动回复已在追回生成期间被关闭，放弃发送")
                                        break
                                    if self.send_message(_reply2):
                                        add_message(conv_id, "me", _reply2, ai_generated=True)
                                        update_conversation_last_message(conv_id, _reply2, "me", 0)
                                        increment_daily_stat("auto_replies_sent")
                                        result["replies_sent"] += 1
                                        _reply_count += 1
                                        _interest2 = _gen2.get("interest", "")
                                        if _interest2:
                                            update_conversation_interest(conv_id, _interest2)
                                        print(f"  [监控] 追回回复已发送: {_reply2[:40]}...")
                                        clean_msgs = _clean2  # 更新基线，下一轮检查用
                                        pause(1, 3)
                                    else:
                                        print(f"  [监控] 追回回复发送失败!")
                                        break
                                else:
                                    print(f"  [监控] 追回回复为空，跳过")
                                    break
                            except Exception as _e2:
                                print(f"  ⚠️ 追回回复生成失败: {_e2}")
                                break
                except Exception as e:
                    print(f"  ⚠️ AI回复生成失败: {e}")
            elif unreplied_hr_msg and not auto_reply_enabled:
                print(f"  [监控] 自动回复已关闭，跳过")

            # 下一个会话前确保输入框已清空，避免残留文字
            try:
                input_el = self.page.locator("#chat-input").first
                text = input_el.inner_text().strip()
                if text:
                    print(f"  [监控] 输入框残留文字「{text[:30]}...」，正在清空")
                    input_el.click()
                    self.page.keyboard.press("Control+a")
                    self.page.keyboard.press("Backspace")
                    pause(0.3, 0.5)
            except Exception:
                pass
            pause(0.5, 1)

        # ── DB 兜底：处理 has_unreplied=1 但 DOM 扫描没覆盖到的会话 ──
        try:
            from boss_state import list_unreplied_conversations
            db_unreplied = list_unreplied_conversations()
            if db_unreplied:
                print(f"[监控] DB兜底: 发现 {len(db_unreplied)} 个 has_unreplied=1 的会话")
                for db_conv in db_unreplied:
                    if scan_should_yield(self):
                        print("  [监控] 有前台操作在等浏览器，跳过 DB 兜底")
                        break
                    conv_id_db = db_conv["id"]
                    if conv_id_db in _processed_conv_ids:
                        continue  # DOM 轮已处理过
                    hr_name_db = db_conv.get("hr_name", "")
                    if not hr_name_db or len(hr_name_db) < 2:
                        continue
                    print(f"  [监控] DB兜底: 尝试处理会话 id={conv_id_db} hr={hr_name_db}")
                    if not self._respect_cooldown():
                        continue
                    opened_db = self.open_conversation_by_name(hr_name_db)
                    if not opened_db:
                        continue
                    pause(1, 2)
                    msgs_db = self.read_visible_messages()
                    print(f"  [监控] DB兜底: 读到 {len(msgs_db)} 条消息")
                    if not msgs_db:
                        continue
                    clean_db = []
                    for m in msgs_db:
                        s = m.get("sender", "hr")
                        c = (m.get("content") or "").strip()
                        if not c:
                            continue
                        clean_db.append({"sender": s, "content": c, "status": m.get("status", "")})
                    if clean_db:
                        replace_conversation_messages(conv_id_db, clean_db)  # has_unreplied 在此自动更新
                    # 找未回复的HR消息
                    if not auto_reply_allowed():
                        print("  [监控] DB兜底: 自动回复已关闭，跳过")
                        continue
                    today_r = get_today_auto_reply_count()
                    if today_r >= MAX_AUTO_REPLY_PER_DAY:
                        continue
                    unreplied_db = None
                    for i2 in range(len(clean_db) - 1, -1, -1):
                        _m2 = clean_db[i2]
                        if _m2["sender"] == "me":
                            continue
                        if is_system_notification(_m2["content"]):
                            continue
                        _has_r = any(
                            clean_db[j2]["sender"] == "me"
                            for j2 in range(i2 + 1, len(clean_db))
                        )
                        if not _has_r:
                            unreplied_db = _m2["content"]
                            break
                    if not unreplied_db:
                        continue
                    result["new_messages"] += 1
                    self._human_pace(min_gap=5.0, max_gap=15.0)
                    # 复用已有的 job_info 查询逻辑
                    job_title_db = db_conv.get("job_title", "")
                    job_company_db = db_conv.get("hr_company", "")
                    job_desc_db = ""
                    app_db = None
                    app_id_db = db_conv.get("application_id")
                    if app_id_db:
                        try:
                            from boss_state import get_application
                            app_db = get_application(app_id_db)
                            if app_db:
                                job_desc_db = app_db.get("description") or ""
                                job_title_db = job_title_db or app_db.get("job_title", "")
                                job_company_db = job_company_db or app_db.get("company", "")
                        except Exception:
                            pass
                    job_info_db = {
                        "title": job_title_db,
                        "company": job_company_db,
                        "description": job_desc_db,
                        "city": app_db.get("city", "") if app_db else "",
                    }
                    style_db = get_setting("ai_reply_style", "professional")
                    resume_db = get_setting("resume_summary", "")
                    wechat_db = get_setting("wechat_id", "")
                    try:
                        # 检测线下面试要求并记录（DB兜底）
                        from boss_state import _detect_offline_interview_requirement as _detect_off, _extract_city_from_text as _extract_city
                        from boss_state import save_offline_interview_location as _save_offline_loc
                        if _detect_off(unreplied_db):
                            _city_db, _detail_db = _extract_city(unreplied_db)
                            if not _city_db:
                                _job_city_db = job_info_db.get("city", "")
                                if _job_city_db:
                                    _city_db = _job_city_db
                            _save_offline_loc(
                                conv_id_db,
                                hr_name=db_conv.get("hr_name", ""),
                                hr_company=db_conv.get("hr_company", ""),
                                job_title=job_title_db,
                                city=_city_db,
                                location_detail=_detail_db,
                                hr_message=unreplied_db,
                            )
                            print(f"  [监控] DB兜底 检测到线下面试要求 → 城市: {_city_db or '未识别'}")

                        from boss_replier import generate_reply
                        gen_db = generate_reply(conv_id_db, unreplied_db, job_info_db, style_db, resume_db, wechat_db)
                        reply_db = gen_db.get("reply", "")
                        if reply_db:
                            think_db = random.uniform(1, 3) + min(len(reply_db) * 0.03, 3)
                            time.sleep(think_db)
                            if not auto_reply_allowed():
                                print("  [监控] DB兜底: 自动回复已在生成期间被关闭，放弃发送")
                                continue
                            if self.send_message(reply_db):
                                add_message(conv_id_db, "me", reply_db, ai_generated=True)
                                update_conversation_last_message(conv_id_db, reply_db, "me", 0)
                                increment_daily_stat("auto_replies_sent")
                                result["replies_sent"] += 1
                                _reply_count += 1
                                interest_db = gen_db.get("interest", "")
                                if interest_db:
                                    update_conversation_interest(conv_id_db, interest_db)
                                print(f"  [监控] DB兜底回复已发送: {reply_db[:40]}...")
                            else:
                                print(f"  [监控] DB兜底回复发送失败!")
                            pause(2, 5)
                    except Exception as e_db:
                        print(f"  [监控] DB兜底回复生成失败: {e_db}")
                # DB兜底后切回「全部」Tab，避免状态残留
                try:
                    self._click_chat_tab("全部")
                except Exception:
                    pass
        except Exception as e:
            print(f"  [监控] DB兜底异常: {e}")

        # 监控循环结束后切回「全部」Tab，避免 BOSS 页面长期停留在未读视图（P09）
        try:
            self._click_chat_tab("全部")
        except Exception:
            pass

        _t_elapsed = time.time() - _t_start
        print(f"[监控] ====== 本轮完成: 扫描{result['checked']}个会话, 新消息{result['new_messages']}条, 回复{result['replies_sent']}条, 耗时{_t_elapsed:.1f}s ======")
        print(f"{'='*60}\n")
        return result
