"""列表 XHR 翻页测试。

背景（真实事故）：`hasMore=false` 的提前停止判断原先放在「把本页岗位 append 进
all_jobs」的 for 循环**之前**，于是 hasMore 变 false 的那一页被整页丢掉：

    实测 resCount=27，第 1 页 15 条 + 第 2 页 12 条 → 只返回了 15 条
    上一次 resCount=32，15+15+2                  → 只返回了 30 条

这两条日志就是下面这些用例要锁住的不变量：**BOSS 报多少条，就得收下多少条**。
"""

import sys
from pathlib import Path
from types import SimpleNamespace

BACKEND_DIR = Path(__file__).resolve().parents[2]
BOSS_DIR = BACKEND_DIR / "boss"
for p in [str(BACKEND_DIR), str(BOSS_DIR)]:
    if p not in sys.path:
        sys.path.insert(0, p)

from boss_firefox import BossScraper


def _payload(n_items: int, res_count: int, has_more: bool, start: int = 0) -> dict:
    """造一页 joblist.json 响应。securityId/lid 留空 → 跳过详情补抓，专心测翻页。"""
    return {
        "code": 0,
        "zpData": {
            "resCount": res_count,
            "hasMore": has_more,
            "jobList": [
                {
                    "jobName": f"岗位{start + i}",
                    "salaryDesc": "10-20K",
                    "brandName": f"公司{start + i}",
                    "encryptJobId": f"enc{start + i}",
                    "securityId": "",
                    "lid": "",
                }
                for i in range(n_items)
            ],
        },
    }


def _scraper(pages, monkeypatch):
    """构造一个不碰浏览器的 BossScraper，_post_joblist 按页返回 pages[i]。"""
    scraper = BossScraper()
    scraper.page = SimpleNamespace(url="https://www.zhipin.com/web/geek/job?query=ai")
    scraper._extract_zp_headers = lambda: {}

    calls: list = []

    def fake_post(page_num, *args, **kwargs):
        calls.append(page_num)
        return pages[page_num - 1]

    scraper._post_joblist = fake_post

    # 翻页间隔 3.5-7s、重试退避 8-15s → 测试里清零，否则每个用例白等几十秒
    monkeypatch.setattr(BossScraper, "SEARCH_PAGE_GAP_SEC", (0.0, 0.0))
    monkeypatch.setattr(BossScraper, "SEARCH_RETRY_BACKOFF_SEC", (0.0, 0.0))
    return scraper, calls


def test_last_page_is_kept_when_has_more_false(monkeypatch):
    """resCount=27 → 15+12，必须返回 27 条（不是 15 条）。"""
    pages = [
        _payload(15, res_count=27, has_more=True),
        _payload(12, res_count=27, has_more=False, start=15),
    ]
    scraper, calls = _scraper(pages, monkeypatch)

    jobs = scraper._fetch_jobs_via_xhr("ai", "100010000", None, None, max_pages=4)

    assert len(jobs) == 27
    assert jobs[-1]["title"] == "岗位26"
    assert calls == [1, 2], "hasMore=false 之后不该再请求第 3 页"
    assert scraper._last_pages_fetched == 2
    assert scraper._last_res_count == 27
    assert scraper._last_has_more is False


def test_last_page_is_kept_with_trailing_small_page(monkeypatch):
    """上一次事故的形态：15+15+2=32，最后一页只有 2 条，同样不能丢。"""
    pages = [
        _payload(15, res_count=32, has_more=True),
        _payload(15, res_count=32, has_more=True, start=15),
        _payload(2, res_count=32, has_more=False, start=30),
    ]
    scraper, calls = _scraper(pages, monkeypatch)

    jobs = scraper._fetch_jobs_via_xhr("ai", "100010000", None, None, max_pages=4)

    assert len(jobs) == 32
    assert calls == [1, 2, 3]


def test_fetches_all_max_pages_when_has_more_stays_true(monkeypatch):
    """还有下一页就一直翻到 max_pages 上限。"""
    pages = [_payload(15, res_count=450, has_more=True, start=15 * i) for i in range(4)]
    scraper, calls = _scraper(pages, monkeypatch)

    jobs = scraper._fetch_jobs_via_xhr("ai", "100010000", None, None, max_pages=4)

    assert len(jobs) == 60
    assert calls == [1, 2, 3, 4]


def test_stops_on_empty_page_without_losing_earlier_pages(monkeypatch):
    """某页返回空列表时停下，但前面几页的结果必须还在。"""
    pages = [
        _payload(15, res_count=999, has_more=True),
        {"code": 0, "zpData": {"resCount": 999, "hasMore": True, "jobList": []}},
    ]
    scraper, calls = _scraper(pages, monkeypatch)

    jobs = scraper._fetch_jobs_via_xhr("ai", "100010000", None, None, max_pages=6)

    assert len(jobs) == 15
    assert calls == [1, 2]
