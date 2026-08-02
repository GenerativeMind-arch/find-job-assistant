#!/usr/bin/env python
"""
岗位知识库爬虫 — 批量导入岗位数据到 ChromaDB。

支持三种数据来源：
  1. --mock           生成样本数据（用于测试全流程，无需联网）
  2. --parse-html     解析本地 HTML 文件（从浏览器保存的 BOSS直聘页面）
  3. --import-dir     直接导入 JSON 文件

用法:
    # 测试模式（推荐首次使用）
    python scripts/crawl_jobs.py --mock --limit 20

    # 解析本地 HTML（从浏览器保存 BOSS直聘页面）
    python scripts/crawl_jobs.py --parse-html data/manual_html/

    # 导入已整理好的 JSON 文件
    python scripts/crawl_jobs.py --import-dir data/manual_jobs/

    # 查看知识库统计
    python scripts/crawl_jobs.py --stats

    # 清空知识库
    python scripts/crawl_jobs.py --clear

工作流程（推荐）:
  1. 打开浏览器访问 BOSS直聘搜索页面
  2. 按 Ctrl+S 保存整个页面为 HTML 到 data/manual_html/
  3. 运行 python scripts/crawl_jobs.py --parse-html data/manual_html/
  4. 数据自动保存到 data/crawled_jobs/*.json.txt 并导入 ChromaDB

注意: BOSS直聘有强反爬机制，无法通过自动化脚本直接抓取。
      必须使用真实浏览器手动打开页面后保存 HTML。


依赖:
    httpx (已安装: 0.28.1)
    lxml  (已安装: 6.1.1)

注意:
    BOSS直聘有浏览器 JS 验证机制，直接 HTTP 请求可能被拦截返回空数据。
    建议:
    1. 先用 --mock 模式跑通全流程
    2. 手动从 BOSS直聘复制岗位数据存到 data/manual_jobs/*.json
    3. 用 --import-dir data/manual_jobs 导入
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import random
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote

# ---- path setup: 确保 backend 可导入 ----
_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent
_BACKEND_DIR = _PROJECT_ROOT / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("crawl_jobs")

import httpx
from lxml import etree, html as lxml_html

# ── 可选 Playwright（浏览器模式） ─────────────────────────

try:
    from playwright.async_api import async_playwright

    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False

# ── 路径常量 ──────────────────────────────────────────────
DATA_DIR = _PROJECT_ROOT / "data"
CRAWLED_DIR = DATA_DIR / "crawled_jobs"
MANUAL_DIR = DATA_DIR / "manual_jobs"
os.makedirs(CRAWLED_DIR, exist_ok=True)
os.makedirs(MANUAL_DIR, exist_ok=True)

# ── 配置 ──────────────────────────────────────────────────

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}

BOSS_SEARCH_URL = "https://www.zhipin.com/web/geek/job?query={query}&city={city}&page={page}"

CITY_CODES = {
    "北京": "101010100", "上海": "101020100", "深圳": "101280600",
    "杭州": "101210100", "广州": "101280100", "成都": "101270100",
    "南京": "101190100", "武汉": "101200100", "西安": "101110100",
    "重庆": "101040100",
}

# ── 样本数据（--mock 模式用）─────────────────────────────

MOCK_JOBS = [
    {"title": "AI Agent 应用开发工程师", "company": "字节跳动", "city": "北京",
     "salary": "40K-70K", "experience": "3-5年", "education": "本科",
     "tech_stack": ["Python", "LangChain", "FastAPI", "Redis", "Docker"],
     "description": "负责 AI Agent 产品的架构设计与开发，基于大模型构建智能对话系统，参与 RAG 系统的优化与迭代。"},
    {"title": "大模型算法工程师", "company": "阿里巴巴", "city": "杭州",
     "salary": "50K-80K", "experience": "3-5年", "education": "硕士",
     "tech_stack": ["PyTorch", "DeepSpeed", "Megatron", "Transformer"],
     "description": "负责大语言模型的预训练、微调与推理优化，研究模型压缩与蒸馏技术，支持公司内部 AI 业务落地。"},
    {"title": "NLP 算法工程师", "company": "腾讯", "city": "深圳",
     "salary": "35K-60K", "experience": "3-5年", "education": "硕士",
     "tech_stack": ["BERT", "GPT", "PyTorch", "TensorFlow", "jieba"],
     "description": "从事自然语言处理相关算法研发，包括文本分类、实体识别、语义理解等方向。"},
    {"title": "机器学习平台开发工程师", "company": "美团", "city": "北京",
     "salary": "30K-55K", "experience": "3-5年", "education": "本科",
     "tech_stack": ["Kubernetes", "MLflow", "Kubeflow", "Python", "Go"],
     "description": "负责机器学习平台的架构设计与开发，建设模型训练、部署、监控一体化的 ML 基础设施。"},
    {"title": "推荐系统工程师", "company": "快手", "city": "北京",
     "salary": "35K-65K", "experience": "2-5年", "education": "本科",
     "tech_stack": ["Python", "C++", "Spark", "Flink", "TensorFlow"],
     "description": "负责推荐系统的算法策略优化与工程落地，包括召回、排序、重排等核心模块。"},
    {"title": "多模态算法工程师", "company": "百度", "city": "北京",
     "salary": "40K-70K", "experience": "3-5年", "education": "硕士",
     "tech_stack": ["CLIP", "ViT", "PyTorch", "ONNX", "TensorRT"],
     "description": "从事多模态理解与生成算法研发，涵盖图文理解、视频理解、多模态对话等方向。"},
    {"title": "AI 推理引擎开发工程师", "company": "华为", "city": "深圳",
     "salary": "35K-60K", "experience": "3-5年", "education": "本科",
     "tech_stack": ["C++", "CUDA", "TensorRT", "ONNX Runtime", "Triton"],
     "description": "负责 AI 推理引擎的核心模块开发与性能优化，支持大规模模型的高效部署。"},
    {"title": "后端开发工程师（AI 方向）", "company": "小红书", "city": "上海",
     "salary": "30K-55K", "experience": "2-5年", "education": "本科",
     "tech_stack": ["Go", "Python", "Kubernetes", "Redis", "Kafka"],
     "description": "负责人 AI 应用的后端服务开发，包括模型服务编排、数据管道和在线推理系统的建设。"},
    {"title": "搜广推算法工程师", "company": "京东", "city": "北京",
     "salary": "35K-65K", "experience": "3-5年", "education": "本科",
     "tech_stack": ["TensorFlow", "PyTorch", "DSSM", "DeepFM", "AUC"],
     "description": "负责搜索排序、广告 CTR 预估、推荐召回等算法优化，提升场景流量转化效率。"},
    {"title": "数据科学家", "company": "滴滴", "city": "北京",
     "salary": "30K-55K", "experience": "3-5年", "education": "硕士",
     "tech_stack": ["Python", "SQL", "Spark", "XGBoost", "AB Testing"],
     "description": "通过数据分析和机器学习模型驱动业务决策，负责指标体系建设与因果推断。"},
    {"title": "LLM 应用开发工程师", "company": "MiniMax", "city": "上海",
     "salary": "45K-75K", "experience": "3-5年", "education": "本科",
     "tech_stack": ["Python", "LangChain", "ChromaDB", "FastAPI", "Weaviate"],
     "description": "基于大模型构建企业级 AI 应用，包括 RAG 系统、Agent 框架和 API 服务开发。"},
    {"title": "深度学习框架工程师", "company": "旷视科技", "city": "北京",
     "salary": "40K-70K", "experience": "3-5年", "education": "硕士",
     "tech_stack": ["C++", "CUDA", "Python", "TVM", "XLA"],
     "description": "负责深度学习框架的核心算子开发与编译器优化，支持多平台部署。"},
    {"title": "AI 产品经理（大模型方向）", "company": "字节跳动", "city": "深圳",
     "salary": "35K-60K", "experience": "3-5年", "education": "本科",
     "tech_stack": ["AI 产品设计", "数据分析", "A/B Test", "Prompt 工程"],
     "description": "负责 AI 产品的规划与设计，深入理解大模型能力边界，推动 AI 功能落地并持续迭代。"},
    {"title": "强化学习算法工程师", "company": "商汤科技", "city": "上海",
     "salary": "35K-65K", "experience": "3-5年", "education": "硕士",
     "tech_stack": ["PyTorch", "Ray", "RLlib", "MuJoCo", "AlphaZero"],
     "description": "从事强化学习算法的研究与落地，包括游戏 AI、机器人控制和自动决策系统。"},
    {"title": "AIGC 图像生成算法工程师", "company": "腾讯", "city": "深圳",
     "salary": "40K-70K", "experience": "3-5年", "education": "硕士",
     "tech_stack": ["Stable Diffusion", "ControlNet", "LoRA", "PyTorch", "Diffusers"],
     "description": "负责 AIGC 图像生成模型的研发与优化，包括文生图、图生图、视频生成等方向。"},
    {"title": "搜索算法工程师", "company": "百度", "city": "北京",
     "salary": "35K-65K", "experience": "3-5年", "education": "本科",
     "tech_stack": ["ERNIE", "PyTorch", "语义匹配", "索引", "Query 理解"],
     "description": "负责搜索引擎核心算法优化，包括语义理解、相关性排序和多模态检索。"},
    {"title": "Agent 框架开发工程师", "company": "阿里云", "city": "杭州",
     "salary": "40K-70K", "experience": "3-5年", "education": "本科",
     "tech_stack": ["Python", "LangGraph", "CrewAI", "FastAPI", "gRPC"],
     "description": "负责企业级 Agent 编排框架的架构设计与核心模块开发，支持多 Agent 协作、工具调用和记忆管理。"},
    {"title": "向量数据库研发工程师", "company": "Zilliz", "city": "北京",
     "salary": "35K-65K", "experience": "3-5年", "education": "本科",
     "tech_stack": ["C++", "Milvus", "Faiss", "HNSW", "gRPC"],
     "description": "负责向量数据库的核心存储引擎和索引算法开发，优化大规模向量检索性能。"},
    {"title": "AI 安全研究员", "company": "奇安信", "city": "北京",
     "salary": "35K-60K", "experience": "3-5年", "education": "硕士",
     "tech_stack": ["Python", "对抗样本", "模型审计", "联邦学习", "差分隐私"],
     "description": "从事 AI 安全领域的研究工作，包括模型鲁棒性测试、对抗攻防、隐私保护等方向。"},
    {"title": "RAG 系统开发工程师", "company": "智谱 AI", "city": "北京",
     "salary": "35K-65K", "experience": "3-5年", "education": "本科",
     "tech_stack": ["Python", "ElasticSearch", "ChromaDB", "LangChain", "Docker"],
     "description": "负责 RAG 系统的架构设计与优化，包括文档解析、分块策略、检索排序和答案生成等环节。"},
]

# ── 数据模型 ──────────────────────────────────────────────

@dataclass
class RawJobItem:
    """从页面解析出的原始岗位数据。"""
    title: str = ""
    company: str = ""
    city: str = ""
    salary: str = ""
    experience: str = ""
    education: str = ""
    tech_stack: list[str] = field(default_factory=list)
    description: str = ""
    url: str = ""
    hard_skills: list[str] = field(default_factory=list)
    responsibilities: list[str] = field(default_factory=list)
    source: str = "boss-zhipin"

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "company": self.company,
            "city": self.city,
            "salary": self.salary,
            "experience": self.experience,
            "education": self.education,
            "tech_stack": self.tech_stack,
            "hard_skills": self.hard_skills,
            "description": self.description,
            "responsibilities": self.responsibilities,
            "source": self.source,
        }

    def is_valid(self) -> bool:
        return bool(self.title) and len(self.title) >= 2


# ── HTTP 客户端（httpx + 可选 Playwright） ──────────────

class BossHTTPClient:
    """BOSS直聘 HTTP 客户端。

    两种模式：
    - standard: 用 httpx 发送请求（被 JS 验证拦截时无法获取数据）
    - browser:  用 Playwright 启动 Chromium（自动执行 JS，绕过验证）
    """

    def __init__(self, request_interval: float = 3.0, use_browser: bool = False):
        self._client = httpx.AsyncClient(
            headers=DEFAULT_HEADERS,
            follow_redirects=True,
            timeout=httpx.Timeout(15.0),
        )
        self._interval = request_interval
        self._last_request = 0.0
        self._use_browser = use_browser
        self._browser = None

    async def __aenter__(self):
        if self._use_browser:
            if not HAS_PLAYWRIGHT:
                logger.error(
                    "Playwright 未安装，无法使用浏览器模式。\n"
                    "请执行: pip install playwright && python -m playwright install chromium"
                )
                raise RuntimeError("Playwright not installed")
            p = await async_playwright().start()
            # 使用隐藏自动化痕迹的参数启动 Chromium
            self._browser = await p.chromium.launch(
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--disable-features=IsolateOrigins,site-per-process",
                    "--no-sandbox",
                    "--disable-web-security",
                    "--disable-features=VizDisplayCompositor",
                    "--disable-dev-shm-usage",
                ],
                # 屏蔽自动化检测的额外环境变量
                env={
                    "PLAYWRIGHT_BROWSERS_PATH": "0",
                } if os.name == "nt" else None,
            )
            logger.info("Playwright Chromium launched (headless, stealth mode)")
        return self

    async def __aexit__(self, *args):
        await self._client.aclose()
        if self._browser:
            await self._browser.close()

    async def fetch(self, url: str) -> Optional[str]:
        """获取页面 HTML。

        如果 use_browser=True，用 Playwright 加载页面（自动执行 JS 绕过验证）。
        否则用 httpx 发送标准 HTTP 请求。
        """
        if self._use_browser and self._browser:
            return await self._fetch_with_browser(url)
        return await self._fetch_with_httpx(url)

    async def _fetch_with_httpx(self, url: str) -> Optional[str]:
        """标准 HTTP 请求（可能被 JS 验证拦截）。"""
        now = time.monotonic()
        elapsed = now - self._last_request
        if elapsed < self._interval:
            await asyncio.sleep(self._interval - elapsed)

        try:
            resp = await self._client.get(url)
            self._last_request = time.monotonic()

            if resp.status_code != 200:
                logger.warning("HTTP %d for %s", resp.status_code, url[:80])
                return None

            text = resp.text
            logger.info("httpx fetch: %s → %d bytes", url[:80], len(text))
            return text

        except Exception as exc:
            logger.warning("httpx fetch failed %s: %s", url[:80], exc)
            self._last_request = time.monotonic()
            return None

    async def _fetch_with_browser(self, url: str) -> Optional[str]:
        """用 Playwright 浏览器加载页面（自动执行 JS，绕过验证）。"""
        now = time.monotonic()
        elapsed = now - self._last_request
        if elapsed < self._interval:
            await asyncio.sleep(self._interval - elapsed)

        try:
            page = await self._browser.new_page()
            # 隐藏自动化痕迹（防止网站检测 headless 浏览器）
            await page.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
                Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN', 'zh', 'en']});
                window.chrome = {runtime: {}};
                Object.defineProperty(navigator, 'connection', {get: () => ({rtt: 100})});
            """)
            # 设置浏览器语言和视图大小，模拟真实用户
            await page.set_extra_http_headers({
                "Accept-Language": "zh-CN,zh;q=0.9",
            })
            await page.set_viewport_size({"width": 1920, "height": 1080})

            # 先访问首页获取 cookie，再访问搜索页面
            await page.goto("https://www.zhipin.com/", wait_until="domcontentloaded", timeout=15000)
            await asyncio.sleep(1)

            # 加载搜索页面，等待网络空闲（JS 执行完毕）
            await page.goto(url, wait_until="networkidle", timeout=30000)

            # 额外等待页面渲染完成
            await asyncio.sleep(2)

            # 获取完整 HTML
            html = await page.content()
            await page.close()

            self._last_request = time.monotonic()
            logger.info("browser fetch: %s → %d bytes", url[:80], len(html))

            # 检查是否仍然被拦截
            if len(html) < 2000 or "浏览器" in html[:500] and "验证" in html[:500]:
                logger.warning("浏览器模式也返回了验证页面（可能需要手动处理验证码）")
                # 保存验证页面供调试
                debug_path = CRAWLED_DIR / "browser_check_failed.html"
                with open(debug_path, "w", encoding="utf-8") as f:
                    f.write(html)
                logger.info("验证页面已保存到 %s", debug_path)
                return None

            return html

        except Exception as exc:
            logger.error("browser fetch failed %s: %s", url[:80], exc)
            self._last_request = time.monotonic()
            return None

    async def close(self):
        await self._client.aclose()
        if self._browser:
            await self._browser.close()


# ── HTML 解析 ────────────────────────────────────────────

def parse_job_list(html: str, city: str = "") -> list[RawJobItem]:
    """从 BOSS直聘搜索结果页 HTML 中提取岗位列表。"""
    if not html or len(html) < 500:
        return []

    # 检查是否被反爬拦截（页面包含浏览器验证脚本）
    if "browser-check" in html or "验证" in html[:2000]:
        logger.warning("BOSS直聘返回了浏览器验证页面，无法直接抓取")
        return []

    jobs = _parse_from_json_state(html, city)
    if jobs:
        return jobs

    jobs = _parse_from_lxml(html, city)
    if jobs:
        return jobs

    logger.warning("Could not parse any jobs from HTML (%d bytes)", len(html))
    return []


def _parse_from_json_state(html: str, city: str) -> list[RawJobItem]:
    """从页面内嵌 JSON 状态中提取岗位数据。"""
    # 查找 window.__NUXT__ = {...}
    patterns = [
        r"window\.__NUXT__\s*=\s*({.*?});?\s*</script>",
        r'<script[^>]*id="__NEXT_DATA__"[^>]*>({.*?})</script>',
        r'<script[^>]*data-name="jobList"[^>]*>({.*?})</script>',
    ]

    for pattern in patterns:
        match = re.search(pattern, html, re.DOTALL)
        if not match:
            continue
        try:
            data = json.loads(match.group(1))
            jobs = _extract_jobs_from_nested(data, city)
            if jobs:
                return jobs
        except (json.JSONDecodeError, TypeError):
            continue

    # 兜底：直接找 JSON 数组片段
    for match in re.finditer(r'\[{"jobName".*?}\]', html, re.DOTALL):
        try:
            items = json.loads(match.group())
            results = []
            for item in items:
                if isinstance(item, dict) and item.get("jobName"):
                    results.append(RawJobItem(
                        title=item.get("jobName", ""),
                        company=item.get("brandName", ""),
                        city=item.get("cityName", city),
                        salary=item.get("salaryDesc", ""),
                        experience=item.get("experienceDesc", ""),
                        education=item.get("educationDesc", ""),
                        tech_stack=item.get("skills", []),
                    ))
            if results:
                return results
        except (json.JSONDecodeError, TypeError):
            continue

    return []


def _extract_jobs_from_nested(data: Any, city: str, depth: int = 0) -> list[RawJobItem]:
    """递归遍历嵌套 JSON，寻找岗位列表数据。"""
    if depth > 6:
        return []

    results = []

    if isinstance(data, dict):
        # 检查当前节点是不是一个岗位对象
        if "jobName" in data or "jobId" in data:
            single = RawJobItem(
                title=data.get("jobName", data.get("title", "")),
                company=data.get("brandName", data.get("companyName",
                           data.get("company", {}).get("name", ""))),
                city=data.get("cityName", data.get("city", city)),
                salary=data.get("salaryDesc", data.get("salary", "")),
                experience=data.get("experienceDesc", data.get("experience", "")),
                education=data.get("educationDesc", data.get("education", "")),
                tech_stack=data.get("skills", data.get("techStack", [])),
                description=data.get("jobDetail", data.get("description", "")),
            )
            if single.is_valid():
                return [single]

        # 遍历所有值
        for key in ("jobList", "geekJobList", "list", "data", "jobCards", "result"):
            val = data.get(key)
            if isinstance(val, list):
                sub = _extract_jobs_from_nested(val, city, depth + 1)
                if sub:
                    return sub

        for val in data.values():
            sub = _extract_jobs_from_nested(val, city, depth + 1)
            if sub:
                return sub

    elif isinstance(data, list) and len(data) > 0:
        # 检查是否整体是一个岗位列表
        if isinstance(data[0], dict) and ("jobName" in data[0] or "jobId" in data[0]):
            for item in data:
                sub = _extract_jobs_from_nested(item, city, depth + 1)
                if sub:
                    results.extend(sub)
            return results

        for item in data:
            sub = _extract_jobs_from_nested(item, city, depth + 1)
            if sub:
                return sub

    return results


def _parse_from_lxml(html: str, city: str) -> list[RawJobItem]:
    """用 lxml 解析 HTML 中的岗位卡片。"""
    try:
        root = lxml_html.fromstring(html)
    except Exception:
        return []

    jobs = []

    # 尝试多种岗位卡片选择器
    card_xpaths = [
        '//*[contains(@class,"job-card")]',
        '//*[contains(@class,"job-list")]//li',
        '//*[contains(@class,"job-primary")]',
        '//*[@class="job-box"]',
        '//li[contains(@class,"job")]',
    ]

    cards = []
    for xpath in card_xpaths:
        cards = root.xpath(xpath)
        if cards:
            break

    if not cards:
        # 尝试找包含薪资的元素所在的父级
        salary_elms = root.xpath('//*[contains(@class,"salary") or contains(@class,"job-salary")]')
        if salary_elms:
            for se in salary_elms[:50]:
                parent = se.getparent()
                for _ in range(3):
                    if parent is not None:
                        parent = parent.getparent()
                    else:
                        break
                if parent is not None:
                    cards.append(parent)

    for card in cards[:50]:
        try:
            # 提取文本辅助函数
            def text_of(xpaths):
                for xp in xpaths:
                    elms = card.xpath(xp)
                    for el in elms:
                        txt = (el.text or "").strip()
                        if txt:
                            return txt
                return ""

            title = text_of([
                './/*[contains(@class,"job-name")]//text()',
                './/*[contains(@class,"job-title")]//text()',
                './/h3//text()',
                './/a//text()',
            ])
            if not title:
                continue

            company = text_of([
                './/*[contains(@class,"company-name")]//text()',
                './/*[contains(@class,"brand")]//text()',
                './/*[contains(@class,"company")]//text()',
            ])

            salary = text_of([
                './/*[contains(@class,"salary")]//text()',
                './/*[contains(@class,"job-salary")]//text()',
            ])

            city_elem = text_of([
                './/*[contains(@class,"job-area")]//text()',
                './/*[contains(@class,"city")]//text()',
            ])

            tags = card.xpath('.//*[contains(@class,"tag") or contains(@class,"label")]//text()')
            tags = [t.strip() for t in tags if t.strip()]

            jobs.append(RawJobItem(
                title=title.strip(),
                company=company.strip(),
                city=city_elem or city,
                salary=salary.strip(),
                tech_stack=tags,
                source="boss-zhipin",
            ))
        except Exception:
            continue

    return jobs


# ── 数据清洗 ──────────────────────────────────────────────

def clean_jobs(raw_jobs: list[RawJobItem]) -> list[dict]:
    """清洗和去重，返回结构化的 dict 列表。"""
    seen = set()
    cleaned = []

    for raw in raw_jobs:
        if not raw.is_valid():
            continue
        key = f"{raw.title}|{raw.company}".lower().strip()
        if key in seen:
            continue
        seen.add(key)

        job = raw.to_dict()
        job["title"] = job["title"][:200]
        job["salary"] = _clean_salary(job["salary"])
        job["tech_stack"] = [s.strip().rstrip("，,") for s in job["tech_stack"] if s.strip()]
        cleaned.append(job)

    return cleaned


def _clean_salary(s: str) -> str:
    if not s:
        return ""
    for sep in ["·", "~", "—", "～", "—"]:
        s = s.replace(sep, "-")
    return s.strip()


# ── JSON 文件输出 ─────────────────────────────────────────

def save_as_json_files(jobs: list[dict], tag: str = "jobs") -> int:
    """将岗位数据保存为 JSON 文件（.txt 格式）。

    每个文件包含一个数组，文件名：{tag}_{timestamp}_{index}.json.txt
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # 每 50 条存一个文件
    chunk_size = 50
    total = 0

    for i in range(0, len(jobs), chunk_size):
        chunk = jobs[i:i + chunk_size]
        idx = i // chunk_size
        filename = f"{tag}_{timestamp}_{idx:03d}.json.txt"
        filepath = CRAWLED_DIR / filename

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(chunk, f, ensure_ascii=False, indent=2)

        total += len(chunk)
        logger.info("Saved %d jobs → %s", len(chunk), filepath)

    return total


def load_json_files(directory: Path) -> list[dict]:
    """从目录中加载所有 *.json.txt 和 *.json 文件。"""
    jobs = []
    for fpath in sorted(directory.glob("*.json*")):
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                jobs.extend(data)
                logger.info("Loaded %d jobs from %s", len(data), fpath.name)
            elif isinstance(data, dict):
                jobs.append(data)
        except Exception as exc:
            logger.warning("Failed to load %s: %s", fpath.name, exc)
    return jobs


# ── Mock 数据生成 ─────────────────────────────────────────

def generate_mock_jobs(count: int = 20) -> list[dict]:
    """生成指定数量的样本岗位数据用于测试。"""
    selected = random.sample(MOCK_JOBS, min(count, len(MOCK_JOBS)))
    jobs = []
    for item in selected:
        job = dict(item)
        if "hard_skills" not in job:
            job["hard_skills"] = []
        if "responsibilities" not in job:
            job["responsibilities"] = []
        if "source" not in job:
            job["source"] = "mock"
        if "description" not in job:
            job["description"] = f"负责{job['title']}的相关工作。"
        jobs.append(job)
    return jobs


# ── ChromaDB 导入 ─────────────────────────────────────────

async def import_to_knowledge_base(jobs: list[dict]) -> tuple[int, str]:
    """导入岗位数据到 ChromaDB job_knowledge 集合。

    Returns:
        (count, status_message)
    """
    try:
        from services.job_knowledge import JobKnowledgeBase
        count = await JobKnowledgeBase.add_jobs(jobs)
        return count, f"成功导入 {count} 条到 ChromaDB"
    except Exception as exc:
        msg = str(exc)
        logger.warning("ChromaDB 导入失败（JSON 文件已保存，失败原因: %s）", msg[:200])
        # 常见错误提示
        if "401" in msg or "InvalidApiKey" in msg or "api_key" in msg.lower():
            hint = "Embedding API Key 无效，请检查 DASHSCOPE_API_KEY 环境变量"
        elif "timeout" in msg.lower():
            hint = "Embedding API 请求超时，请检查网络连接"
        else:
            hint = msg[:200]
        return 0, f"ChromaDB 导入失败（{hint}）"


# ── Freshness 索引更新 ───────────────────────────────────

async def update_freshness_index(jobs: list[dict]) -> None:
    """将本次导入的岗位信息写入 MySQL freshness 索引表（可选，失败不阻塞）。"""
    try:
        from storage.mysql_client import get_mysql_pool

        pool = await get_mysql_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                for job in jobs:
                    jid = _fid(job)
                    cks = _cksum(job)
                    await cur.execute("""
                        INSERT INTO job_freshness_index
                            (job_id, title, company, city, source, crawled_at, checksum, status)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,'active')
                        ON DUPLICATE KEY UPDATE crawled_at=%s, checksum=%s, status='active'
                    """, (jid, job.get("title",""), job.get("company",""), job.get("city",""),
                          job.get("source","boss-zhipin"), now, cks, now, cks))
                await conn.commit()
        logger.info("Updated freshness index for %d jobs", len(jobs))
    except Exception as exc:
        logger.warning("Freshness index update skipped (non-fatal): %s", exc)


def _fid(job: dict) -> str:
    raw = f"{job.get('title','')}|{job.get('company','')}|{job.get('city','')}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _cksum(job: dict) -> str:
    return hashlib.md5(json.dumps(job, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


# ── 主流程 ────────────────────────────────────────────────

async def crawl_and_ingest(
    keywords: list[str],
    cities: list[str],
    limit: int = 200,
    save_html: bool = False,
    use_browser: bool = False,
) -> int:
    """爬取 → 解析 → 保存 JSON → 导入知识库。"""
    if not keywords or not cities:
        logger.error("keywords 和 cities 不能为空")
        return 0

    tag = f"crawl_{keywords[0][:10]}"
    all_raw: list[RawJobItem] = []

    async with BossHTTPClient(use_browser=use_browser) as client:
        for kw in keywords:
            for city in cities:
                pages = max(1, limit // len(keywords) // len(cities) // 30 + 1)
                logger.info("Crawl: keyword='%s', city='%s', pages=%d", kw, city, pages)

                for page in range(1, pages + 1):
                    url = BOSS_SEARCH_URL.format(
                        query=quote(kw),
                        city=CITY_CODES.get(city, ""),
                        page=page,
                    )

                    html = await client.fetch(url)
                    if not html:
                        continue

                    if save_html:
                        html_path = CRAWLED_DIR / f"raw_{kw}_{city}_p{page}.html"
                        with open(html_path, "w", encoding="utf-8") as f:
                            f.write(html)
                        logger.info("Saved raw HTML → %s", html_path.name)

                    jobs = parse_job_list(html, city)
                    all_raw.extend(jobs)
                    logger.info("  Page %d: +%d jobs (total raw: %d)", page, len(jobs), len(all_raw))

                    if len(jobs) < 15:
                        break
                    await asyncio.sleep(1.0)

    if not all_raw:
        logger.warning("No jobs found. Pages may be blocked by JS verification.")
        return 0

    cleaned = clean_jobs(all_raw)
    logger.info("Cleaned: %d raw → %d unique", len(all_raw), len(cleaned))

    # 保存 JSON 文件
    saved = save_as_json_files(cleaned, tag=tag)
    logger.info("Saved %d jobs as JSON files", saved)

    # 导入知识库
    imported, import_status = await import_to_knowledge_base(cleaned)
    logger.info("Import result: %s", import_status)

    return imported


async def import_from_directory(directory: Path) -> int:
    """从目录中的 JSON 文件导入到知识库。"""
    if not directory.exists():
        logger.error("Directory not found: %s", directory)
        return 0

    jobs = load_json_files(directory)
    if not jobs:
        logger.warning("No JSON files found in %s", directory)
        return 0

    logger.info("Loaded %d jobs from %s", len(jobs), directory)

    # 保存到 crawled（统一管理）
    saved = save_as_json_files(jobs, tag="manual")
    logger.info("Backed up %d jobs to crawled_jobs/", saved)

    imported, import_status = await import_to_knowledge_base(jobs)
    logger.info("Import result: %s", import_status)
    return imported


async def run_mock(limit: int = 20) -> int:
    """生成样本数据 → 保存 JSON → 导入知识库（端到端测试）。"""
    logger.info("Generating %d mock job samples...", limit)
    jobs = generate_mock_jobs(limit)

    saved = save_as_json_files(jobs, tag="mock")
    logger.info("Saved %d mock jobs as JSON files", saved)

    imported, import_status = await import_to_knowledge_base(jobs)
    logger.info("Import result: %s", import_status)

    if imported > 0:
        await update_freshness_index(jobs)

    return imported


async def parse_html_directory(directory: Path) -> int:
    """从目录中读取 HTML 文件，解析岗位数据后导入。

    Args:
        directory: 包含 HTML 文件的目录（从浏览器保存的 BOSS直聘页面）

    使用方式:
        1. 在浏览器中打开 BOSS直聘搜索页
        2. Ctrl+S 保存完整页面到 data/manual_html/
        3. 运行此函数自动解析
    """
    if not directory.exists() or not directory.is_dir():
        logger.error("目录不存在: %s", directory)
        return 0

    html_files = []
    for ext in ("*.html", "*.htm"):
        html_files.extend(sorted(directory.glob(ext)))

    if not html_files:
        logger.warning("目录中没有 HTML 文件: %s", directory)
        return 0

    logger.info("找到 %d 个 HTML 文件", len(html_files))

    all_raw: list[RawJobItem] = []
    for fpath in html_files:
        try:
            html = fpath.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            try:
                html = fpath.read_text(encoding="gbk")
            except Exception:
                logger.warning("无法解码 %s，跳过", fpath.name)
                continue

        logger.info("Parsing %s (%d bytes)...", fpath.name, len(html))

        # 检查是否是 BOSS直聘验证页面
        if len(html) < 2000 or ("browser-check" in html and len(html) < 10000):
            logger.warning("%s 是浏览器验证页面，不包含岗位数据", fpath.name)
            continue

        jobs = parse_job_list(html, "")
        if jobs:
            logger.info("  → 解析出 %d 个岗位", len(jobs))
            all_raw.extend(jobs)
        else:
            logger.warning("  → 未能从 %s 中解析出岗位", fpath.name)

    if not all_raw:
        logger.warning("所有 HTML 文件均未解析到岗位数据")
        return 0

    cleaned = clean_jobs(all_raw)
    logger.info("清洗去重: %d raw → %d unique", len(all_raw), len(cleaned))

    # 保存 JSON
    saved = save_as_json_files(cleaned, tag="parsed")
    logger.info("JSON 文件已保存: %d 条", saved)

    # 导入知识库
    imported, status = await import_to_knowledge_base(cleaned)
    logger.info("导入结果: %s", status)

    return imported


async def show_stats() -> None:
    """显示岗位知识库统计。"""
    from services.job_knowledge import JobKnowledgeBase
    stats = await JobKnowledgeBase.get_stats()
    print("[知识库统计]")
    print(f"  Collection:  {stats['collection_name']}")
    print(f"  岗位总数:    {stats['document_count']}")
    print(f"  状态:        {stats['status']}")

    json_count = len(list(CRAWLED_DIR.glob("*.json*")))
    print(f"  JSON 文件数: {json_count}（{CRAWLED_DIR}）")
    print()


async def clear_knowledge_base() -> None:
    """清空岗位知识库。"""
    from services.rag_retriever import _get_collection, _get_chroma_client

    try:
        client = _get_chroma_client()
        client.delete_collection("job_knowledge")
        print("[已清空] ChromaDB job_knowledge 集合")
    except Exception as exc:
        print(f"[清空失败] {exc}")


# ── CLI ───────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="岗位知识库工具 — 导入/解析/管理 AI/技术岗 JD 数据",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例:
  # 1. 测试模式：生成样本数据验证全流程
  python scripts/crawl_jobs.py --mock --limit 20

  # 2. 解析本地 HTML：从浏览器保存的 BOSS直聘页面中提取岗位
  python scripts/crawl_jobs.py --parse-html data/manual_html/

  # 3. 导入已整理好的 JSON 文件
  python scripts/crawl_jobs.py --import-dir data/manual_jobs/

  # 4. 查看统计
  python scripts/crawl_jobs.py --stats
        """,
    )
    p.add_argument("--mock", action="store_true",
                   help="生成样本数据测试全流程（无需联网）")
    p.add_argument("--parse-html", type=str, default="",
                   help="解析本地 HTML 文件目录（从浏览器保存的 BOSS直聘页面）")
    p.add_argument("--import-dir", type=str, default="",
                   help="从指定目录导入 JSON 文件")
    p.add_argument("--limit", type=int, default=20,
                   help="Mock 模式生成条数（默认 20）")
    p.add_argument("--stats", action="store_true",
                   help="查看知识库统计")
    p.add_argument("--clear", action="store_true",
                   help="清空岗位知识库")
    return p.parse_args()


def main():
    args = parse_args()

    if args.clear:
        asyncio.run(clear_knowledge_base())
        return

    if args.stats:
        asyncio.run(show_stats())
        return

    if args.mock:
        logger.info("=" * 50)
        logger.info("🔄 Mock 模式：生成样本数据测试全流程")
        logger.info("=" * 50)
        count = asyncio.run(run_mock(limit=args.limit))
        print(f"\n[完成] {count} 条样本岗位已入库\n")
        return

    if args.import_dir:
        directory = Path(args.import_dir)
        count = asyncio.run(import_from_directory(directory))
        print(f"\n[完成] {count} 条岗位已从 {directory} 导入\n")
        return

    if args.parse_html:
        directory = Path(args.parse_html)
        count = asyncio.run(parse_html_directory(directory))
        print(f"\n[完成] {count} 条岗位已解析导入\n")
        return

    # 没有指定任何模式
    print("请指定操作模式：")
    print("  --mock         生成样本数据测试全流程")
    print("  --parse-html   解析本地 HTML 文件")
    print("  --import-dir   导入 JSON 文件")
    print("  --stats        查看知识库统计")
    print()
    print("示例: python scripts/crawl_jobs.py --mock --limit 20")


if __name__ == "__main__":
    main()
