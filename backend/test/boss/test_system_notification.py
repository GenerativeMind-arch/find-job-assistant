"""BOSS 系统卡片 vs 真人 HR 消息的区分。

自动回复只该回**真人**。但 BOSS 会把自己的卡片/提示混进 HR 消息流里，同一条
`sender='hr'` —— 对它回复既没意义，又让账号显得像机器人。

下面这些字符串全部取自真实会话（2026-10-12 把 messages 表里 sender='hr' 的
去重捞了一遍），前半是系统卡片、后半是真的 HR 发言。
"""

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2]
BOSS_DIR = BACKEND_DIR / "boss"
for p in [str(BACKEND_DIR), str(BOSS_DIR)]:
    if p not in sys.path:
        sys.path.insert(0, p)

import pytest

from boss_automation import is_system_notification


# ── 系统卡片：不能回 ──

SYSTEM_CARDS = [
    "你与该职位竞争者PK情况\n共12人投递，你超过8人竞争者",
    "对方已查看了您的附件简历",
    "对方已同意，您的附件简历已发送给对方",
    "您的附件简历 AI(大模型)应用开发-焦春晓.pdf 已发送给Boss点击查看附件",
    "附件简历请求",
    "我想要一份您的附件简历，您是否同意\n拒绝\n同意",
    # 微信交换卡片：把安全提示和按钮全塞在一条里，远超 80 字
    "我想要和您交换微信，您是否同意\n\n"
    "为保障您的安全建议在平台内沟通，微信沟通中需特别保护您的个人信息，谨防受骗。\n\n拒绝\n同意",
    "为保障您的安全建议在平台内沟通，微信沟通中需特别保护您的个人信息，谨防受骗。",
    "18652992330\n蓝先生的微信号\n18652992330\n复制微信号",
    "点击预览附件简历",
]


@pytest.mark.parametrize("text", SYSTEM_CARDS)
def test_system_card_is_not_treated_as_hr_message(text):
    assert is_system_notification(text) is True, f"系统卡片被当成 HR 发言会去回复它: {text[:30]!r}"


def test_long_system_card_is_still_filtered():
    """回归：原实现有「超过 80 字就直接放行」的长度过滤，等于给长卡片开后门。

    系统卡片完全可能很长（微信交换卡片会把安全提示、按钮全拼在同一条里）。
    """
    long_card = "我想要和您交换微信，您是否同意\n" + "为保障您的安全建议在平台内沟通。" * 8
    assert len(long_card) > 80
    assert is_system_notification(long_card) is True


# ── 真人消息：必须回 ──

REAL_HR_MESSAGES = [
    "你好，我们有Python岗位",
    "好的 麻烦加下我微信 安排面试",
    "方便发一份简历过来吗？",
    "好哦~方便发一份您的简历过来吗？",
    "你好啊，可以聊一聊~",
    "加我vx",
    "打扰你一下，请问你还在找python工程师（agent方向）的工作吗？",
    "目前在职吗？最快到岗时间大概是什么时间段？",
    "收到啦！谢谢您，我把您的简历发送给用人部门领导，如果通过初选，我会再来联系您确认面试安排的。",
    "感谢关注！请发送中文简历给我们，或登录官网投递（www.pokemon.com.cn）。初筛通过后，我们会邮件通知您后续安排。",
]


@pytest.mark.parametrize("text", REAL_HR_MESSAGES)
def test_real_hr_message_is_not_filtered(text):
    assert is_system_notification(text) is False, f"真人消息被当成系统卡片，HR 就等不到回复了: {text[:30]!r}"


def test_empty_content_is_safe():
    assert is_system_notification("") is False
    assert is_system_notification(None) is False
    assert is_system_notification("   ") is False
