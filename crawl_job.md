# 岗位知识库方案 — AI/技术岗 离线采集 + RAG 检索

## Context

当前系统在岗位搜索阶段的瓶颈：国内招聘平台（BOSS直聘、猎聘等）没有公开 API，也没有成熟的 MCP Server 可用。之前的实时爬虫方案有两个硬伤——反爬机制严格、且用户等不起实时爬取的时间。

本方案换个思路：**不做实时爬取，做离线预采集。** 用爬虫按周批量抓取 AI/技术岗的 JD，清洗后存入 ChromaDB 形成岗位知识库。用户搜索时直接查知识库，毫秒级返回。

## 整体架构

```
离线（每周运行一次）                         在线（用户请求时）
─────────────────                         ─────────────────

BOSS直聘搜索页 / 猎聘等                        用户输入 "AI Agent工程师"
    │                                               │
    ▼                                               ▼
Crawler Script                              JobSearchService.search_jd()
(scripts/crawl_jobs.py)                         │
    │                                           ├─ 第 1 层：岗位知识库检索（NEW）
    │                                           │   └─ 查 ChromaDB job_knowledge 集合
    │                                           ├─ 第 2 层：LLM 生成（fallback）
    │                                           └─ 第 3 层：提示手动输入
    ▼                                               │
清洗 + 去重 + 结构化                                  ▼
    │                                           返回结果给用户（同现有格式）
    ▼
ChromaDB collection: "job_knowledge"
    │
    ├─ documents: JD 全文
    ├─ metadatas: {title, company, industry, tech_stack, skills, ...}
    └─ ids: job_{sha256_prefix}
    │
    ▼
MySQL: job_freshness_index
    ├─ 记录每个 job_id 的抓取时间、源平台
    └─ 支持按周增量更新
```

## 新增/修改文件

### 1. `backend/services/job_knowledge.py`（新增）— 岗位知识库服务

岗位知识库的核心服务，封装对 ChromaDB job_knowledge 集合的所有操作。

```
class JobKnowledgeBase:
    COLLECTION_NAME = "job_knowledge"
    
    @staticmethod
    async def search_jobs(query: str, top_k: int = 10, top_n: int = 5, 
                          filters: dict | None = None) -> list[dict]:
        """搜索岗位知识库，返回格式与 _normalize_mcp_results 兼容。
        
        内部流程：
        1. 调用 RAGRetriever.retrieve(query, top_k, top_n, 
               collection_name=COLLECTION_NAME, filter_metadata=filters)
        2. 将 ChromaDB 结果（text + metadata）组装为统一格式
        3. 返回 [{job_id, title, company, salary, description, tech_stack, ...}]
        """
    
    @staticmethod
    async def add_jobs(jobs: list[dict]) -> int:
        """批量导入岗位到知识库。
        
        每个 job 的处理：
        1. 用 job_id 去重（已存在则跳过，实现幂等）
        2. 构建 documents 文本（用于 embedding）
        3. 构建 metadatas 字典（用于过滤）
        4. 调用 RAGRetriever.add_documents(texts, metadatas, ids, ...)
        """
    
    @staticmethod
    async def remove_jobs(job_ids: list[str]) -> int:
        """从知识库中删除指定岗位。"""
    
    @staticmethod
    async def get_stats() -> dict:
        """知识库统计：总数、按行业分布、按城市分布等。"""
    
    @staticmethod
    def _build_document_text(job: dict) -> str:
        """构建用于 embedding 的文本。
        
        策略：将 title + industry + tech_stack + hard_skills + 
              responsibilities + description 拼接成一段文本。
        这样语义搜索时，用户输入"AI Agent 工程师 大模型"能匹配到相关内容。
        """
    
    @staticmethod
    def _build_metadata(job: dict) -> dict:
        """构建 ChromaDB metadata（扁平键值对）。
        
        包含：title, company, city, salary, industry, 
              tech_stack_str, hard_skills_str, experience, education,
              source, crawled_at
        注意：list 类型字段需转为逗号分隔字符串（ChromaDB metadata 限制）
        """
```

**为什么复用 `RAGRetriever.add_documents` 而不是走 `KnowledgeBase.ingest_bytes`？**

因为 `KnowledgeBase` 的 ingest 流水线是文件导向的（FileClassifier → Parse → Chunk → Embed），不适合结构化 JSON 数据。`RAGRetriever.add_documents` 直接接受 text + metadata，更灵活。

### 2. `backend/services/job_search.py` — 新增"岗位知识库"检索层

在现有的三层 fallback 中，将 MCP 层替换为岗位知识库层：

```python
class JobSearchService:
    
    @staticmethod
    async def search_jd(job_title: str, city: str = "", force_llm: bool = False) -> dict:
        """
        JD 搜索统一入口（新版三层 fallback）：
        Layer 1: 岗位知识库（NEW — 替换原来的 MCP）
        Layer 2: LLM 生成（原有的 fallback）
        Layer 3: 提示手动输入
        """
        # Layer 1: 岗位知识库检索
        if not force_llm:
            kb_results = await JobSearchService.search_via_knowledge_base(job_title, city)
            if kb_results:
                return {
                    "source": "job_kb",
                    "query": job_title,
                    "jobs": kb_results,
                    "count": len(kb_results),
                    "message": f"从岗位知识库中找到 {len(kb_results)} 个相关职位，请选择最匹配的一个。",
                }
        
        # Layer 2: LLM 生成（原有）
        ...
    
    @staticmethod
    async def search_via_knowledge_base(job_title: str, city: str = "") -> Optional[list[dict]]:
        """从岗位知识库中检索匹配的职位。
        
        1. 调用 JobKnowledgeBase.search_jobs(job_title, filters={city} if city else None)
        2. 如果结果为空，尝试用关键词拆解后二次检索
           （如 "AI Agent应用开发工程师" → 拆成 "AI Agent" + "开发工程师"）
        3. 返回标准化结果列表
        """
```

**注意：** 这个改动同时解决了之前 MCP 那条路不通的问题。新的 `source = "job_kb"` 在 `planner.py` 的 `_handle_search_jd` 里走和 `source = "mcp"` 相同的展示路径（显示编号列表让用户选择）。只需要改一行：

```python
# planner.py:180 — 把 "mcp" 改为支持 "job_kb"
# 原来：if jobs and source == "mcp":
# 改为：
if jobs and source in ("mcp", "job_kb"):
```

### 3. `scripts/crawl_jobs.py`（新增）— 离线爬虫脚本

这是每周定时运行的 CLI 脚本，职责是：爬取 → 清洗 → 去重 → 入库。

```python
"""
岗位知识库爬虫 — 离线批量采集 AI/技术岗 JD。

用法：
    python scripts/crawl_jobs.py --keywords "AI Agent,大模型,后端开发" --city "北京" --limit 200
    python scripts/crawl_jobs.py --config crawler_config.yaml     # 使用配置文件
    python scripts/crawl_jobs.py --incremental                     # 增量更新（只爬新岗位）
"""

async def crawl_and_ingest(keywords: list[str], city: str = "", limit: int = 200):
    """主流程：爬取 → 清洗 → 去重 → 入库"""
    
    # Step 1: 爬取（根据关键词逐页爬取）
    all_raw_jobs = []
    for keyword in keywords:
        raw_jobs = await crawl_boss_zhipin(keyword, city, limit=limit // len(keywords))
        all_raw_jobs.extend(raw_jobs)
    
    # Step 2: 清洗 + 去重 + 结构化
    cleaned_jobs = clean_and_deduplicate(all_raw_jobs)
    
    # Step 3: 导入岗位知识库
    count = await JobKnowledgeBase.add_jobs(cleaned_jobs)
    
    # Step 4: 更新 freshness 索引
    await update_freshness_index(cleaned_jobs)
    
    return count
```

**反爬策略**（离线脚本，允许更长时间）：
- 使用 `curl_cffi` 做 TLS 指纹模拟
- 爬取间隔 3-5 秒/请求，总量 200 条/周，远低于反爬阈值
- 首次请求先访问首页拿 cookie，后续请求带上
- 遇到 403 时随机等待 30-60 秒后重试
- 不轮换 IP，不激进并发——"低调慢爬"是这个方案的核心原则

### 4. `backend/config.yaml` — 新增 job_knowledge 配置

```yaml
job_knowledge:
  enabled: true
  chroma_collection: "job_knowledge"
  search_top_k: 10
  search_top_n: 5
  
  # 爬虫配置（给 crawl_jobs.py 用）
  crawler:
    keywords: ["AI Agent", "大模型应用开发", "人工智能工程师", "NLP工程师"]
    cities: ["北京", "上海", "深圳", "杭州"]
    max_jobs_per_keyword: 100
    request_interval_sec: 3
    max_retries: 3
    user_agent: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 ..."
```

### 5. `backend/config_loader.py` — 新增 `get_job_knowledge_config()`

```python
def get_job_knowledge_config() -> dict:
    """返回岗位知识库配置。"""
    cfg = get_config().get("job_knowledge", {})
    return {
        "enabled": cfg.get("enabled", True),
        "chroma_collection": cfg.get("chroma_collection", "job_knowledge"),
        "search_top_k": cfg.get("search_top_k", 10),
        "search_top_n": cfg.get("search_top_n", 5),
    }
```

### 6. `backend/sql/init_schema.sql` — 新增 job_freshness_index 表

```sql
CREATE TABLE IF NOT EXISTS job_freshness_index (
    job_id      VARCHAR(64)  PRIMARY KEY,
    title       VARCHAR(255) NOT NULL,
    company     VARCHAR(255) DEFAULT '',
    city        VARCHAR(64)  DEFAULT '',
    source      VARCHAR(64)  DEFAULT 'boss-zhipin',
    crawled_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    checksum    VARCHAR(64)  NOT NULL DEFAULT '',   -- 内容哈希，用于检测变化
    status      ENUM('active', 'removed') NOT NULL DEFAULT 'active',
    INDEX idx_jfi_crawled (crawled_at),
    INDEX idx_jfi_title (title)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
```

**作用：** 追踪哪些岗位已经在知识库中、何时抓取的、内容有没有变化。下次增量爬取时，通过 `checksum` 判断是否需更新。

### 7. `backend/agents/planner.py` — 接受 `source = "job_kb"`

```python
# planner.py:180 — 修改条件，接受知识库来源
# 原来：if jobs and source == "mcp":
# 改为：
if jobs and source in ("mcp", "job_kb"):
```

## 数据流

### 写入流（离线爬虫）

```
crawl_jobs.py
  │
  ├─ 1. 对每个关键词，爬取 BOSS直聘搜索结果页 1-3 页
  │     → 解析 HTML 提取：title, company, salary, city, 
  │        experience, education, tech_stack, description
  │
  ├─ 2. 数据清洗
  │     → 去重（相同 job 只保留一份）
  │     → 标准化（统一薪资格式 "30K-50K"、城市名等）
  │     → 过滤（硬性要求匹配 AI/技术岗）
  │
  ├─ 3. 构建 ChromaDB 文档
  │     → documents: 联合 title + tech_stack + responsibilities + description
  │     → metadatas: title, company, city, salary, tech_stack_str, ...
  │     → ids: job_{sha256_of_url}
  │
  ├─ 4. RAGRetriever.add_documents() → 写入 ChromaDB
  │
  └─ 5. MySQL job_freshness_index → 更新 freshness
```

### 读取流（用户在线请求）

```
用户说 "AI Agent工程师"
  │
  ▼
planner._handle_search_jd()
  │
  ▼
JobSearchService.search_jd("AI Agent工程师")
  │
  ├─ JobKnowledgeBase.search_jobs("AI Agent工程师")
  │     ├─ RAGRetriever.retrieve(query="AI Agent工程师",
  │     │       collection_name="job_knowledge", top_k=10, top_n=5)
  │     │     ├─ Query Embedding
  │     │     ├─ ChromaDB Top-10 召回
  │     │     └─ Rerank Top-5 精排
  │     │
  │     └─ 返回 [{title, company, salary, description, tech_stack, ...}, ...]
  │
  ├─ 有结果 → 返回 source="job_kb", jobs=[...], count=N
  │     → planner 展示编号列表 → 用户选择
  │     → build_job_from_search_result() → state.job
  │
  └─ 无结果 → 走 LLM fallback（现有逻辑）
```

## 为什么这个方案可行

| 维度 | 实时爬虫方案 | 离线知识库方案（本方案） |
|------|-----------|---------------------|
| **反爬压力** | 必须几秒内返回，容易被封 | 每周跑一次，每次几小时，低调慢爬 |
| **用户体验** | 等待 5-30 秒爬取 | 毫秒级从 ChromaDB 检索 |
| **数据质量** | 原始数据未清洗 | 去重 + 标准化 + 结构化 |
| **运维成本** | 需要维护爬虫稳定在线 | 离线脚本 + cron，挂了下周再跑 |
| **覆盖范围** | 随用户输入变化 | 预设关键词 + 城市，定向覆盖 |
| **更新频率** | 实时 | 每周（对 JD 来说足够）|

**对 JD 类数据来说，周级更新是合理的。** 一个岗位描述不会一天一变，大多数 JD 挂在网上几周甚至几个月。所以每周更新一次的知识库新鲜度完全足够。

## 不做的事

1. **不爬取所有岗位**——只按预设关键词爬取 AI/技术岗（用户确认的范围）
2. **不实时更新**——每周一次定时任务触发，而不是每次用户搜索时爬
3. **不存储敏感信息**——不爬用户信息、公司内部信息
4. **不全文存储**——ChromaDB 中存的是 JD 文本，不是完整的 HTML 页面

## 验证方案

```python
# 1. 爬虫脚本可用
python scripts/crawl_jobs.py --keywords "AI Agent" --city "北京" --limit 10
# 预期输出：成功爬取 X 条，清洗后 Y 条，入库 Z 条

# 2. 知识库查询正常
python -c "
from services.job_knowledge import JobKnowledgeBase
import asyncio
result = asyncio.run(JobKnowledgeBase.search_jobs('AI Agent 工程师'))
print(f'找到 {len(result)} 条结果')
print(result[0]['title'] if result else '无结果')
"

# 3. 搜索接口正常
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "AI Agent工程师"}'
# 预期返回：展示知识库中匹配的岗位编号列表

# 4. 增量更新幂等
python scripts/crawl_jobs.py --keywords "AI Agent" --incremental
# 重复执行不应该产生重复数据
```
