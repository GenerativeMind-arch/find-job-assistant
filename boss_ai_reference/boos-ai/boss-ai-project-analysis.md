# BOSS-AI 项目全景解读

> **项目名称**: lakejobai-job-radar（BOSS-AI）
> **定位**: AI驱动的BOSS直聘智能求职助手
> **版本**: 0.1.0
> **Python要求**: ≥3.10
> **许可证**: 见 LICENSE

---

## 一、项目一句话概括

**用AI帮你自动在BOSS直聘上找工作、投简历、和HR聊天。** 不是简单的自动化脚本，而是一个完整的「Plan-then-Execute」智能Agent系统，能自主规划搜索策略、分析JD匹配度、生成个性化招呼语、自动回复HR消息，甚至还能模拟面试。

---

## 二、架构总览

项目由5个核心层构成，从上到下：

```
┌─────────────────────────────────────────┐
│          lakejob_cli (CLI层)             │  ← 命令行入口（供外部Agent调用）
│  cli.py → client.py → output.py         │
│  schema.json（AI Agent工具描述）          │
├─────────────────────────────────────────┤
│          boss_app (Web后端层)             │  ← FastAPI + WebSocket + 仪表盘
│  REST API / WebSocket广播 / 监控循环      │
├─────────────────────────────────────────┤
│          agent (智能Agent层)              │  ← Plan-then-Execute循环
│  loop.py / tools.py / skills.py          │
│  prompts.py                              │
├─────────────────────────────────────────┤
│          核心功能层                       │
│  boss_automation.py (交互自动化)          │
│  boss_replier.py (AI回复生成)            │
│  boss_firefox.py (浏览器爬虫)            │
│  boss_company.py (公司画像)              │
│  boss_rag.py (JD检索增强)                │
│  boss_geo.py (城市/区解析)               │
│  interview/ (面试模拟引擎)               │
├─────────────────────────────────────────┤
│          boss_state (数据层)              │  ← SQLite WAL模式
│  7张核心表 + 迁移机制                     │
└─────────────────────────────────────────┘
```

---

## 三、模块详解

### 3.1 CLI层：lakejob_cli

**设计理念**: CLI不是给人类用的，而是给**AI Agent**用的。每个命令输出标准JSON信封，让另一个AI程序能程序化地调用和解析。

#### 四层架构

| 层 | 文件 | 职责 |
|---|---|---|
| 命令定义 | `cli.py` | Click装饰器定义18个子命令，调用client方法，用output格式化 |
| HTTP客户端 | `client.py` | httpx调用FastAPI后端（默认 `http://127.0.0.1:8010`） |
| 输出格式 | `output.py` | JSON信封 `{ok, command, data, error}`，stdout=JSON, stderr=日志 |
| 工具描述 | `schema.json` | 给LLM看的工具清单：命令名、描述、CLI语法、端点、参数 |

#### JSON信封格式

```json
{
  "ok": true,
  "command": "search",
  "data": { "jobs_found": 42, ... },
  "error": null
}
```

- **stdout** 只输出JSON（方便外部Agent解析）
- **stderr** 输出人类日志（方便调试）
- Windows UTF-8编码修复 + Unicode编码失败自动降级到ASCII

#### 18个CLI命令

| 命令 | 功能 |
|---|---|
| `version` | 版本号 |
| `schema` | 输出schema.json（Agent自发现） |
| `search` | 搜索岗位 |
| `status` | 系统状态 |
| `stats` | 投递漏斗统计 |
| `jobs` | 列出已投岗位 |
| `apply` | 投递单个岗位 |
| `apply-batch` | 批量投递 |
| `scan` | 扫描当前页面 |
| `scan-apply` | 扫描+一键投递 |
| `conversations` | 列出会话 |
| `chat` | 查看聊天消息 |
| `send` | 发送消息 |
| `doctor` | 诊断环境 |
| `login` | 登录BOSS |
| `analyze` | JD分析 |
| `shortlist` | 候选池管理 |
| `server` / `restart` / `smart-send` | 服务管理 |

#### 进程管理亮点

`cli.py` 中的 `_kill_boss_app()` 用 psutil/wmic 精准杀进程（只杀匹配 `boss_app.py` 命令行的进程），不会误杀其他Python进程。

### 3.2 Web后端层：boss_app.py

**技术栈**: FastAPI + Uvicorn + WebSocket + 单线程Playwright Executor

这是项目的「大脑中枢」，所有操作都经过这里调度。

#### 关键设计

1. **单线程Playwright Executor**: Playwright的同步API要求所有操作在同一线程。用 `ThreadPoolExecutor(max_workers=1)` + `_run_pw()` 保证不并发冲突。

2. **浏览器锁 `_lock_browser()`**: 暂停监控循环 + 获取asyncio Lock，确保搜索/投递不跟聊天监控争夺浏览器资源。

3. **WebSocket广播**: 所有操作完成后实时通知前端（`broadcast_ws`），前端仪表盘即时刷新。

4. **双路数据抓取**: 搜索岗位时优先XHR API抓取（更完整、拿JD+HR名+法人），XHR失败回退DOM抓取。

5. **鉴权缓存**: `_refresh_zp_cache()` 从浏览器提取 zp_token/token/Cookie，缓存15分钟，让geo等纯HTTP端点免走浏览器。

#### API端点概览

| 分类 | 端点 | 说明 |
|---|---|---|
| 系统 | `/api/system/start`, `stop`, `relogin`, `heartbeat` | 浏览器生命周期 |
| 监控 | `/api/monitor/pause`, `resume` | 暂停/恢复聊天监控 |
| 岗位 | `/api/jobs/search`, `apply`, `apply-batch`, `scan`, `scan-and-apply` | 搜索+投递 |
| 分析 | `/api/jobs/analyze`, `optimize-resume`, `chat-suggestion` | AI分析JD |
| 会话 | `/api/conversations`, `sync-list`, `sync`, `send` | 聊天管理 |
| 公司 | `/api/companies/preview`, `smart-send` | 公司画像+智能投递 |
| 设置 | `/api/settings` | 全局配置 |
| 候选池 | `/api/shortlists` | 收藏岗位 |
| 诊断 | `/api/doctor`, `debug/selector-test`, `page-stats` | 调试工具 |

#### 核心流程：搜索岗位

```
用户请求 → boss_app.search_jobs()
  → _lock_browser() (暂停监控)
  → _run_pw(automation.search) (浏览器操作)
  → XHR优先 / DOM兜底 (双路抓取)
  → 补抓法人信息 (批量fetch_company_legal_rep)
  → 写库 (add_application / update_application_from_job)
  → _search_job_payload() (统一字段)
  → broadcast_ws (通知前端)
  → _refresh_zp_cache() (刷新鉴权缓存)
```

### 3.3 智能Agent层：agent/

这是项目最精巧的部分，实现了「Plan-then-Execute」模式。

#### 3.3.1 AgentLoop（loop.py）

**四阶段循环**: Plan → Execute → Replan → Summarize

```
用户目标 → _plan() [LLM生成JSON执行计划]
  → 逐步执行每个step
    → _resolve_args() [解析$N变量引用]
    → ToolRegistry.execute() [调用工具]
    → _inter_step_delay() [2-6秒随机延迟]
    → 约束检查 / 空结果检测 / 错误退避
  → 连续失败3次 → _replan() [LLM重新规划]
  → 所有step完成 → _summarize() [LLM总结结果]
```

**关键机制**:

| 机制 | 说明 |
|---|---|
| `$N`变量引用 | 步骤间传递结果，如 `$2.analyzed[0].url` 引用第2步结果 |
| 约束检查 | 20个中文关键词（"不投递"、"太远了"等），命中则跳过apply类工具 |
| 错误退避 | 5-10s → 15-25s → 30-45s，递增等待 |
| 风控感知 | `_check_automation_cooldown()` 检查BOSS冷却期 |
| 空结果检测 | `_is_empty_result()` 判断工具返回是否为空 |
| 里程碑标记 | 搜索/分析/投递三类工具，执行后标记阶段 |
| 重规划限制 | 最多2次replan，避免无限循环 |
| 步数上限 | 最多12步 |

#### 3.3.2 ToolRegistry + ToolContext（tools.py）

**依赖注入模式**:
- `ToolContext` 单例持有 `automation` 对象和 `_run_pw` 函数
- `ToolRegistry` 管理14个原子工具的注册、执行、描述生成
- 每个工具函数通过 `ToolContext` 获取依赖，不直接import

**14个原子工具**:

| 工具 | 输入 | 输出 |
|---|---|---|
| `search_jobs` | keyword, city, limit | 岗位列表 |
| `list_jobs` | status, limit | 已投岗位列表 |
| `get_job_detail` | job_url | 岗位详情+JD |
| `analyze_jd` | job_url | 匹配度+建议 |
| `optimize_resume_for_jd` | job_url | 简历优化建议 |
| `get_chat_suggestion` | job_url | 沟通策略 |
| `apply_job` | job_url, greeting | 投递结果 |
| `batch_apply` | job_urls, greeting | 批量投递结果 |
| `list_conversations` | - | 会话列表 |
| `get_chat_messages` | conv_id | 聊天记录 |
| `generate_reply` | conv_id, hr_message | AI回复+兴趣度 |
| `get_status` | - | 系统状态 |
| `get_stats` | - | 统计漏斗 |
| `preview_companies` | keyword, city | 公司画像 |

**`summarize_result()`**: 把工具原始输出压缩成LLM友好的文本摘要，避免token爆炸。

#### 3.3.3 复合技能（skills.py）

3个组合多个原子工具的高级技能：

| 技能 | 流程 | 特点 |
|---|---|---|
| `smart_scan` | search → get_detail×N → analyze×N → 按score排序 | 深度扫描+评估 |
| `prepare_application` | get_detail → analyze → (optimize_resume + get_chat_suggestion 并行) | 准备投递材料 |
| `smart_apply` | prepare_application → (score ≥ min_score) apply_job | 评估后投递 |

`prepare_application` 用 `asyncio.gather` 并行执行简历优化和沟通建议，节省时间。

#### 3.3.4 Prompt模板（prompts.py）

| Prompt | 用途 | 关键规则 |
|---|---|---|
| `PLANNING_SYSTEM_PROMPT` | LLM生成执行计划 | 严禁编造参数值，必须引用真实URL/ID |
| `USER_GOAL_PROMPT` | 格式化用户目标+上下文 | 注入已有数据 |
| `REPLAN_PROMPT` | 失败后重新规划 | 前几步结果+失败原因 |
| `SUMMARY_PROMPT` | 总结执行结果 | 步骤摘要+最终建议 |

### 3.4 核心功能层

#### 3.4.1 BossAutomation（boss_automation.py）

继承 `BossScraper`，增加交互能力：

- `apply_to_job()` — 导航到详情页→点击「立即沟通」→输入招呼语→发送
- `apply_batch()` — 批量投递，带延迟和日上限检查
- `scan_current_page()` / `scan_and_apply_current_page()` — 扫描当前搜索页
- `open_conversation_by_name()` / `read_visible_messages()` — 聊天交互
- `send_message_in_chat()` — 在聊天页发送消息
- `navigate_to_chat()` — 跳转聊天页
- `heartbeat()` — 心跳保活
- `sync_conversation_list_full()` — 同步会话列表元数据
- `fetch_company_legal_rep()` — 抓公司法人信息
- `goto_company_similar_jobs()` / `parse_company_similar_jobs_page()` — 公司画像
- `aggregate_company_hrs()` — 从岗位卡聚合HR

**选择器配置**: `SELECTORS` dict 定义所有关键元素的CSS选择器列表，BOSS改版时只需改这里。还支持通过设置表 `selector_overrides` 动态覆盖。

**反检测**: `ANTI_DETECT` JS脚本注入每个页面，隐藏webdriver标记、伪造硬件参数、干扰canvas/WebGL指纹。

**投递流程** (`__bossApply` JS函数):
1. 检查页面状态（已投递/上限/频繁）
2. 查找「立即沟通」按钮（3路策略）
3. 原生点击（mousedown→mouseup→click序列）
4. 等待聊天弹窗（40次检测，10s超时）
5. 逐字输入招呼语（60-180ms/字，模拟真人打字）
6. 找发送按钮并发送
7. 确认发送成功

#### 3.4.2 BossScraper（boss_firefox.py）

纯爬虫层，不涉及交互：

- `search()` — 搜索岗位（XHR优先/DOM兜底）
- `_fetch_jobs_via_xhr()` — 通过BOSS内部API抓取列表+详情
- `_extract_job_cards()` — DOM方式提取岗位卡片
- `fetch_detail()` — 详情页提取JD+HR信息
- `login()` — 扫码登录，10分钟超时
- 反检测脚本（`ANTI_DETECT`）

**XHR抓取策略**: 在浏览器内用 `page.evaluate(fetch(...))` 发请求，自动带Cookie/Referer，绕过BOSS的风控（code 37）。

**登录态管理**: 持久化Profile目录 + `storage_state()` 导出Cookie/localStorage，下次启动自动恢复。

#### 3.4.3 AI回复生成（boss_replier.py）

**两层分类机制**:
1. **快速关键词判断**: 明显关键词（"转人工"、"真人回复"等）秒判
2. **LLM上下文判断**: 模糊表达调Ollama qwen2.5:1.5b（带上最近3条聊天记录）

**SYSTEM_PROMPT** 核心规则:
- 坦诚告知是AI助手（本身就是技术能力证明）
- 只从简历摘要引用技能，不编造
- 2-4句话，自然真诚
- 线下面试→引导加微信/转人工
- 微信/简历/电话→系统自动发按钮，回复只说"已发送"
- 输出格式: `{"reply": "...", "interest": "high/medium/low"}`

**招呼语生成** (`generate_greeting_ai`):
- 两种模式: `template`（模板替换）/ `smart`（用户自定义规则化Prompt）
- 简历驱动而非JD驱动（先放真实经历，再放JD，防止LLM把JD当自己的技能）
- RAG增强: 检索历史相似JD的招呼语经验
- 质量校验: 太短(<6字)/太长(>220字)/含联系方式→回退模板
- smart模式额外校验: 占位符残留→回退

#### 3.4.4 公司画像（boss_company.py）

薄封装层，组合3个automation方法：
- 跳转公司页 → 解析在招岗位+HR → 聚合去重HR → `pick_top_hr` 推荐最佳联系人

**HR排序**: `_hr_title_score()` 给HR头衔打分（CEO/创始人=100, 总监=80, 经理=60...），法人额外加分。

#### 3.4.5 JD检索增强（boss_rag.py）

**RAG流程**:
```
当前JD → get_embedding() (Ollama nomic-embed-text)
  → similar_jds() (余弦相似度≥0.55)
  → build_rag_context() (格式化为LLM few-shot上下文)
```

三种context_type:
- `optimize`: 重点引用历史优化建议
- `greeting`: 重点引用历史招呼语 + HR反馈信号（兴趣度/要简历/加微信）
- `chat`: 重点引用历史沟通建议

HR反馈信号让LLM知道哪种招呼语效果好：高兴趣度+要了简历 → 值得模仿；低兴趣度 → 避雷。

#### 3.4.6 城市/区解析（boss_geo.py）

- 从BOSS公开API拉取城市列表和区/商圈数据
- 内存缓存（6小时TTL），API失败回退静态表
- 支持多区筛选（`multiBusinessDistrict` URL重复参数）
- 支持工作区域筛选（行政区域6位code vs 商圈3-8位code）

#### 3.4.7 面试模拟引擎（interview/）

| 文件 | 职责 |
|---|---|
| `engine.py` | 对话式面试官，5-8轮追问+评价 |
| `llm_client.py` | Ollama(embedding+出题) + DeepSeek(批改) |
| `db.py` | MySQL知识库（语义搜索QA） |
| `fast_qa.py` | 快速问答 |
| `benchmark.py` / `benchmark_rag.py` | 基准测试 |
| `batch_seed.py` / `seed_data.py` | 种子数据 |

面试引擎特点:
- 交叉验证简历（候选人说法 vs 简历描述）
- 弱项优先出题（根据历史记录分析）
- 自然收尾+整体评价+学习建议
- 持久化面试会话（SQLite interview_sessions表，支持暂停/恢复）

### 3.5 数据层：boss_state.py

**SQLite WAL模式** + 线程本地连接 + 自动迁移

#### 7张核心表

| 表 | 说明 | 关键字段 |
|---|---|---|
| `applications` | 投递记录 | job_url(唯一), status, greeting_text, optimize_result, embedding |
| `conversations` | 会话 | hr_name, interest_level, hr_wechat, transfer_requested, has_unreplied |
| `messages` | 聊天消息 | sender, content, ai_generated, delivery_status |
| `settings` | 全局设置 | key-value模式 |
| `daily_stats` | 每日统计 | applications_sent, messages_sent, auto_replies_sent |
| `shortlists` | 候选池 | job_url(唯一) |
| `interview_sessions` | 面试会话 | session_id, history_json, round_count |

**迁移模式**: 每个新字段用 `ALTER TABLE ADD COLUMN` + `try/except`，已存在则跳过。这样旧数据库不需要重建就能升级。

**`has_unreplied` 标记**: 在 `replace_conversation_messages()` 中自动计算——找到最后一条非系统HR消息，检查之后是否有"me"的回复。这是聊天监控的核心判断依据。

**默认设置**:
```python
"greeting_template": "...",
"ai_greeting_enabled": "true",
"daily_apply_limit": "15",
"auto_reply_enabled": "false",
"min_reply_delay_sec": "20-40",
"default_city": "淄博",
```

---

## 四、数据流：从搜索到投递的完整流程

```
用户/Agent发起搜索
  → CLI: lakejob search --keyword "Python" --city "上海"
  → client.py: httpx POST /api/jobs/search
  → boss_app.search_jobs()
    → _lock_browser() (暂停监控)
    → _run_pw(automation.search(keyword, city_code))
      → BossScraper.search()
        → 构建URL (keyword + city + scale + districts)
        → page.goto() 导航到搜索页
        → _fetch_jobs_via_xhr() (XHR优先)
          → _extract_zp_headers() (提取鉴权token)
          → page.evaluate(fetch(...)) (浏览器内HTTP请求)
          → 列表API + 详情API (拿JD+HR+法人)
        → 回退DOM: _extract_job_cards()
        → _scroll_all() (滚动加载更多)
      → 返回岗位列表
    → 补抓法人信息 (batch fetch_company_legal_rep)
    → 写库 (add_application / update_application_from_job)
    → 统一字段 (_search_job_payload)
    → broadcast_ws (通知前端)
  → client.py: 解析JSON信封
  → output.py: 格式化输出
```

---

## 五、聊天监控循环

这是项目最复杂的后台流程：

```
chat_monitor_loop (asyncio Task, 后台常驻)
  → 循环执行:
    → 如果 monitor_paused → sleep等待
    → 获取浏览器锁 browser_sync_lock
    → _run_pw(automation.read_unread_conversations) — 读未读会话列表
    → 对每个有未读的会话:
      → _run_pw(automation.open_conversation_by_name) — 打开会话
      → _run_pw(automation.read_visible_messages) — 读消息
      → replace_conversation_messages() — 写库+标记has_unreplied
      → 如果 auto_reply_enabled:
        → 判断是否需要转人工 (_needs_human_transfer)
        → 如果HR要简历 → 点击BOSS「发简历」按钮
        → 如果HR要微信 → 点击BOSS「换微信」按钮
        → 如果HR要电话 → 点击BOSS「换电话」按钮
        → 如果线下面试 → 记录地点到 offline_interview_locations 表
        → generate_reply() — AI生成回复
        → _run_pw(automation.send_message_in_chat) — 发送回复
        → 更新interest_level / daily_stats
      → 释放浏览器锁
    → sleep(检查间隔)
```

---

## 六、关键设计决策与亮点

### 6.1 双路抓取（XHR + DOM）

BOSS的XHR API能拿到更完整的数据（JD全文、真实HR名、法人、可投递权限），但依赖zp_token。DOM抓取是兜底，不受token限制。

### 6.2 简历驱动而非JD驱动

招呼语生成和简历优化时，Prompt**先放简历（真实经历），再放JD（目标岗位）**，物理分隔防止LLM把JD要求当作自己的技能。这是防止AI"美化简历"的关键设计。

### 6.3 风控感知

- 每步间2-6秒随机延迟
- 日投递上限（默认15）
- BOSS冷却期检测（"沟通次数已用完"）
- 操作频繁退避（5-10s → 15-25s → 30-45s）
- 反检测脚本（隐藏webdriver、伪造指纹）

### 6.4 HR兴趣度评估

每次AI回复都要求LLM输出 `interest: high/medium/low`，存入数据库。这是后续RAG的关键信号——历史招呼语效果好（high + 要了简历）就模仿，效果差就避雷。

### 6.5 自动触发BOSS官方按钮

HR要简历→点「发简历」按钮，要微信→点「换微信」按钮，要电话→点「换电话」按钮。这些都是BOSS官方功能，不是在聊天文字里发联系方式（会被BOSS过滤）。

### 6.6 转人工机制

两层判断：
1. 明显关键词秒判（"转人工"、"真人回复"）
2. 模糊表达调本地小模型（Ollama qwen2.5:1.5b，带上最近3条上下文）

触发后：标记 `transfer_requested`，通知用户手动处理。

---

## 七、项目文件地图

```
boos-ai/
├── boss_app.py          ← FastAPI后端（最大文件，2000+行）
├── boss_automation.py   ← 浏览器交互自动化（继承BossScraper）
├── boss_firefox.py      ← 爬虫+反检测+投递JS（1700行）
├── boss_replier.py      ← AI回复+招呼语生成（540行）
├── boss_state.py        ← SQLite数据层（1100行）
├── boss_company.py      ← 公司画像封装
├── boss_rag.py          ← JD RAG检索
├── boss_geo.py          ← 城市/区解析
├── scraper.py           ← 早期智联爬虫（独立，可忽略）
├── pyproject.toml       ← hatchling构建配置
├── static/              ← 仪表盘前端（dashboard.html）
│
├── agent/
│   ├── loop.py          ← Plan-then-Execute AgentLoop（647行）
│   ├── tools.py         ← 14原子工具+3技能+ToolRegistry（955行）
│   ├── skills.py        ← 3复合技能（240行）
│   ├── prompts.py       ← LLM Prompt模板（137行）
│   └── __init__.py
│
├── interview/
│   ├── engine.py        ← 面试引擎
│   ├── llm_client.py    ← LLM客户端（Ollama+DeepSeek）
│   ├── db.py            ← MySQL知识库
│   ├── fast_qa.py       ← 快速问答
│   ├── benchmark.py     ← 基准测试
│   ├── batch_seed.py    ← 批量种子
│   ├── seed_data.py     ← 种子数据
│   ├── main.py          ← 面试入口
│   └── mysql_config.py  ← MySQL配置
│
├── lakejob_cli/
│   ├── cli.py           ← Click命令定义（410行）
│   ├── client.py        ← httpx HTTP客户端（159行）
│   ├── output.py        ← JSON信封格式化（56行）
│   ├── schema.json      ← AI Agent工具描述（149行）
│   └─ __init__.py
│
├── tests/
│   ├── test_boss_state.py
│   └── test_smart_send.py
│
├── .boss_profile/       ← 持久化目录
│   ├── boss_state.db    ← SQLite数据库
│   ├── firefox_state.json ← 浏览器登录态
│   └── firefox_user_data/ ← Firefox Profile
```

---

## 八、改进与扩展建议

### 8.1 代码组织改进

| 问题 | 建议 |
|---|---|
| `boss_app.py` 超过2000行，混杂API+业务逻辑 | 拆分为 `api/` 子包：`api/jobs.py`, `api/conversations.py`, `api/system.py` 等 |
| `boss_state.py` 1100行纯函数，无ORM | 可迁移到 SQLAlchemy / TortoiseORM，或至少用 dataclass 封装 |
| `boss_automation.py` 太大 | 拆分：`automation_apply.py`（投递）、`automation_chat.py`（聊天）、`automation_scrape.py`（抓取） |
| `scraper.py` 是早期智联爬虫，与主线无关 | 移到 `legacy/` 或删除，避免混淆 |
| 测试太少（仅2个测试文件） | 补充核心流程测试：搜索→写库→投递→聊天监控 |

### 8.2 Agent层改进

| 问题 | 建议 |
|---|---|
| AgentLoop只支持单轮Plan-then-Execute | 加入多轮对话能力，允许用户中途修正目标 |
| `$N`变量引用只支持简单路径 | 支持过滤/排序表达式（如 `$2.analyzed[?score>80]`） |
| 约束关键词硬编码 | 从设置表读取，允许用户自定义 |
| 没有工具使用统计 | 记录每个工具的调用频次/成功率，用于Prompt优化 |

### 8.3 风控与稳定性

| 问题 | 建议 |
|---|---|
| 没有持久化错误日志 | 加入结构化日志（structlog），写文件+WebSocket推送 |
| 登录态丢失只能手动重新登录 | 加入自动检测+自动重登录（带人工确认） |
| 选择器硬编码在SELECTORS dict | 加一个选择器版本管理机制，随BOSS版本号切换 |
| 没有并发安全（多用户同时操作） | 加入用户会话隔离或操作队列 |

### 8.4 功能扩展方向

| 方向 | 说明 |
|---|---|
| 多平台支持 | 扩展到猎聘、拉勾等，用抽象层统一接口 |
| Agent对话模式 | 让用户像跟真人对话一样指导Agent："先搜上海Python岗，挑匹配度>80的投" |
| 简历版本管理 | 同一岗位生成多版优化简历，A/B测试招呼语效果 |
| 面试日历 | 把线下面试地点+时间整合成日历视图 |
| 数据可视化 | 投递漏斗、兴趣度分布、技能热力图 |
| 微信小程序 | 把仪表盘做成小程序，随时随地查看状态 |

### 8.5 技术栈升级建议

| 当前 | 建议 | 原因 |
|---|---|---|
| SQLite | 保持，但加索引优化 | WAL模式够用，但 `applications` 表缺少 `(status, company_id)` 复合索引 |
| FastAPI 同步Playwright | 可研究 Playwright async API | 减少线程池开销，但需要大改造 |
| Ollama本地小模型 | 可替换为更快的分类器 | 转人工判断可以用sklearn/fasttext，比调Ollama快10倍 |
| httpx 同步客户端 | 已经够用 | CLI场景不需要async |
| Click CLI | 保持 | 简单可靠，schema.json才是给Agent看的 |

---

## 九、快速上手指南

### 9.1 安装

```bash
cd boos-ai/
pip install -e .               # 安装CLI工具
playwright install firefox      # 安装浏览器
```

### 9.2 启动后端

```bash
python boss_app.py --port 8010  # 启动FastAPI后端
```

打开浏览器访问 `http://127.0.0.1:8010` 看到仪表盘。

### 9.3 首次登录

```bash
lakejob login                   # 打开浏览器扫码
```

或通过仪表盘「设置」Tab点击「启动浏览器」→手动扫码登录。

### 9.4 CLI使用（给AI Agent用）

```bash
lakejob schema                  # 输出工具描述（Agent自发现）
lakejob search --keyword "Python" --city "上海"
lakejob apply --job-url "https://www.zhipin.com/job_detail/..."
lakejob status                  # 查看系统状态
```

### 9.5 配置AI

在仪表盘「设置」Tab中配置：
- AI API Key（DeepSeek）
- AI Base URL
- AI Model
- 简历摘要
- 微信号
- 默认城市
- 日投递上限

---

## 十、术语表

| 术语 | 含义 |
|---|---|
| JD | Job Description，岗位描述 |
| HR | Human Resources，招聘方 |
| 法人 | 公司法定代表人，如果是HR本人说明是老板 |
| zp_token | BOSS直聘内部鉴权token |
| XHR | XMLHttpRequest，浏览器内HTTP请求 |
| WAL | Write-Ahead Logging，SQLite并发安全模式 |
| RAG | Retrieval-Augmented Generation，检索增强生成 |
| Plan-then-Execute | 先规划再执行，Agent的经典模式 |
| JSON信封 | `{ok, command, data, error}` 标准输出格式 |
| $N引用 | 步骤间变量传递，如 `$2.result[0].url` |
| 退避 | 遇到错误后递增等待时间再重试 |
| has_unreplied | 标记会话中是否有HR未回复的消息 |
| 兜底 | 主策略失败后的备用方案 |
