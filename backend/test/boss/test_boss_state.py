"""Boss 模块状态管理（SQLite）单元测试。

测试 boss_state 模块的各种数据库操作：settings、applications、conversations 等。
不依赖 Playwright，纯 SQLite 操作。
"""

import sys
from pathlib import Path

import pytest

# 确保路径
BACKEND_DIR = Path(__file__).resolve().parents[2]
BOSS_DIR = BACKEND_DIR / "boss"
for p in [str(BACKEND_DIR), str(BOSS_DIR)]:
    if p not in sys.path:
        sys.path.insert(0, p)

from boss_state import (
    get_setting,
    set_setting,
    get_all_settings,
    get_today_application_count,
    get_today_auto_reply_count,
    get_daily_stats,
    add_application,
    get_application,
    get_application_by_url,
    update_application_status,
    list_applications,
    get_or_create_conversation,
    get_conversation,
    list_active_conversations,
    add_message,
    get_messages,
    update_conversation_last_message,
    update_conversation_status,
    set_auto_reply,
    add_to_shortlist,
    remove_from_shortlist,
    list_shortlists,
    is_in_shortlist,
    get_db,
)


# ── Settings 测试 ──

class TestBossSettings:
    """测试 settings 表的读写操作。"""

    def test_get_setting_default(self):
        """未设置的 key 应返回默认值。"""
        val = get_setting("non_existent_key_12345", "default_val")
        assert val == "default_val"

    def test_set_and_get_setting(self):
        """set_setting 后 get_setting 应返回设置的值。"""
        set_setting("test_key_abc", "test_value_123")
        assert get_setting("test_key_abc") == "test_value_123"

    def test_set_setting_overwrite(self):
        """重复设置同一个 key 应覆盖旧值。"""
        set_setting("test_overwrite_key", "first")
        set_setting("test_overwrite_key", "second")
        assert get_setting("test_overwrite_key") == "second"

    def test_get_all_settings(self):
        """get_all_settings 应返回字典。"""
        settings = get_all_settings()
        assert isinstance(settings, dict)
        assert len(settings) >= 0

    def test_setting_boolean_string(self):
        """布尔值以字符串形式存储。"""
        set_setting("test_bool", "true")
        assert get_setting("test_bool") == "true"
        assert get_setting("test_bool", "false") == "true"


# ── Application 测试 ──

class TestBossApplications:
    """测试岗位投递记录的 CRUD 操作。"""

    TEST_JOB_URL = "https://www.zhipin.com/job_detail/test-123456.html"

    def teardown_method(self):
        """每个测试方法后清理测试数据。"""
        try:
            db = get_db()
            db.execute("DELETE FROM applications WHERE job_url LIKE '%test-123456%'")
            db.execute("DELETE FROM conversations WHERE job_title LIKE '%测试%'")
            db.commit()
        except Exception:
            pass

    def test_add_and_get_application(self):
        """添加投递记录后应能通过 url 查到。"""
        job_data = {
            "url": self.TEST_JOB_URL,
            "title": "Python开发工程师",
            "company": "测试公司",
            "city": "上海",
            "salary": "15-25K",
            "experience": "3-5年",
            "education": "本科",
        }
        app_id = add_application(job_data)
        assert app_id is not None
        app = get_application_by_url(self.TEST_JOB_URL)
        assert app is not None
        assert app["job_title"] == "Python开发工程师"
        assert app["company"] == "测试公司"

    def test_update_application_status(self):
        """更新投递状态应生效。"""
        job_data = {
            "url": self.TEST_JOB_URL,
            "title": "测试岗位",
            "company": "测试公司",
        }
        app_id = add_application(job_data)
        update_application_status(app_id, "greeting_sent")
        app = get_application(app_id)
        assert app is not None
        assert app["status"] == "greeting_sent"

    def test_list_applications(self):
        """list_applications 应返回列表。"""
        jobs = list_applications(limit=10)
        assert isinstance(jobs, list)

    def test_today_application_count(self):
        """get_today_application_count 应返回非负整数。"""
        count = get_today_application_count()
        assert isinstance(count, int)
        assert count >= 0


# ── Conversation 测试 ──

class TestBossConversations:
    """测试会话记录的 CRUD 操作。"""

    TEST_JOB_URL = "https://www.zhipin.com/job_detail/test-conv-999.html"

    def teardown_method(self):
        """清理测试数据。"""
        try:
            db = get_db()
            db.execute("DELETE FROM conversations WHERE job_title LIKE '%测试会话%'")
            db.execute("DELETE FROM applications WHERE job_url LIKE '%test-conv-999%'")
            db.commit()
        except Exception:
            pass

    def test_get_or_create_conversation(self):
        """get_or_create_conversation 应创建新会话并返回 ID。"""
        # 先创建一个 application 作为关联
        app_id = add_application({
            "url": self.TEST_JOB_URL,
            "title": "测试会话岗位",
            "company": "测试公司",
        })
        assert app_id is not None
        conv_id = get_or_create_conversation(
            application_id=app_id,
            hr_name="测试HR",
            hr_company="测试公司",
            job_title="测试会话岗位",
        )
        assert conv_id is not None
        assert isinstance(conv_id, int)

    def test_add_and_get_messages(self):
        """添加消息后应能查到。"""
        app_id = add_application({
            "url": self.TEST_JOB_URL,
            "title": "测试消息岗位",
            "company": "测试公司",
        })
        conv_id = get_or_create_conversation(
            application_id=app_id,
            hr_name="测试HR",
            hr_company="测试公司",
            job_title="测试消息岗位",
        )
        add_message(conv_id, "hr", "你好，我们有Python岗位")
        msgs = get_messages(conv_id)
        assert isinstance(msgs, list)
        assert len(msgs) >= 1

    def test_list_active_conversations(self):
        """list_active_conversations 应返回列表。"""
        convs = list_active_conversations()
        assert isinstance(convs, list)


# ── Shortlist 测试 ──

class TestBossShortlist:
    """测试收藏夹功能。"""

    TEST_JOB_URL = "https://www.zhipin.com/job_detail/test-shortlist-999.html"

    def teardown_method(self):
        try:
            db = get_db()
            db.execute("DELETE FROM shortlists WHERE job_url LIKE '%test-shortlist-999%'")
            db.execute("DELETE FROM applications WHERE job_url LIKE '%test-shortlist-999%'")
            db.commit()
        except Exception:
            pass

    def test_add_to_shortlist(self):
        """添加到收藏夹。"""
        add_to_shortlist(
            job_url=self.TEST_JOB_URL,
            title="收藏测试岗位",
            company="测试公司",
            note="薪资高",
        )
        assert is_in_shortlist(self.TEST_JOB_URL) is True

    def test_remove_from_shortlist(self):
        """从收藏夹移除。"""
        # 先添加
        add_to_shortlist(
            job_url=self.TEST_JOB_URL,
            title="收藏测试岗位2",
            company="测试公司",
        )
        # 查到 shortlist_id 后移除
        lists = list_shortlists()
        target = None
        for item in lists:
            if item.get("job_url") == self.TEST_JOB_URL:
                target = item
                break
        assert target is not None
        remove_from_shortlist(target["id"])
        assert is_in_shortlist(self.TEST_JOB_URL) is False

    def test_list_shortlists(self):
        """list_shortlists 应返回列表。"""
        result = list_shortlists()
        assert isinstance(result, list)


# ── Stats 测试 ──

class TestBossStats:
    """测试统计功能。"""

    def test_daily_stats(self):
        """get_daily_stats 应返回包含统计字段的字典。"""
        stats = get_daily_stats()
        assert isinstance(stats, dict)

    def test_today_auto_reply_count(self):
        """get_today_auto_reply_count 应返回非负整数。"""
        count = get_today_auto_reply_count()
        assert isinstance(count, int)
        assert count >= 0
