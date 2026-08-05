# CLAUDE.md — Interview Assistant 项目认知文档

## 1. 项目背景与研究动机

在当前求职市场，求职者面临的核心痛点不是"找不到招聘信息"，而是**无法高效地将自身经历与目标岗位进行精准匹配**：

- **JD 解析靠人工**：求职者需要逐字阅读长篇岗位描述，手动提取关键要求，效率低且容易遗漏隐性偏好
- **简历定制成本高**：针对不同岗位定制简历需要反复修改措辞、调整重点，一份简历海投的策略已不再有效
- **面试准备碎片化**：市面上找面经靠搜索引擎和论坛零散帖子，缺乏针对"你这个人 + 这个岗位"的个性化面试准备
- **真实经验获取难**：大多数人的真实面试经历并不会上传到网络，高质量、一手的面经数据极度稀缺

**Interview Assistant** 的定位是：一款 **AI 驱动的多 Agent 协作系统**，覆盖从岗位理解 → 候选人画像构建 → 简历生成与渲染 → 差距分析 → 面试准备的求职全流程。它不是简单的"ChatGPT 写简历"，而是一个有状态、有记忆、多 Agent 分工协作的求职工作站。

---
## 2. 总体功能全景

```
┌────────────────────────────────────────────────────────────────────┐
│                        Interview Assistant                           │
│                    多 Agent 智能求职助手                             │
├────────────────────────────────────────────────────────────────────┤
│                                                                    │
│  ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐        │
│  │  🔍 JD   │   │  👤 候选人 │   │  📊 差距  │   │  📝 简历  │        │
│  │  智能搜索 │   │  画像提取 │   │  分析    │   │  内容生成 │        │
│  │          │   │          │   │          │   │          │        │
│  │ MCP 真实 │   │ 文件解析 │   │ 技能缺口 │   │ STAR 改写│        │
│  │ 招聘数据 │   │ 事实抽取 │   │ 匹配评分 │   │ 关键词对齐│        │
│  │ 多平台聚合│  │ 增量合并 │   │ 追问生成 │   │ 局部更新 │        │
│  └──────────┘   └──────────┘   └──────────┘   └──────────┘        │
│                                                                    │
│  ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐        │
│  │  🎨 简历 │   │  💬 面试 │   │  📚 RAG  │   │  📤 导出 │        │
│  │  渲染    │   │  问答生成 │   │  知识库   │   │  服务    │        │
│  │          │   │          │   │          │   │          │        │
│  │ 多主题   │   │ 技术问题 │   │ 真实面经 │   │ HTML/PDF │        │
│  │ 实时预览 │   │ 项目深挖 │   │ 向量检索 │   │ JSON/MD  │        │
│  │ 响应式   │   │ 行为面试 │   │ 智能重排 │   │ 一键导出 │        │
│  └──────────┘   └──────────┘   └──────────┘   └──────────┘        │
│                                                                    │
└────────────────────────────────────────────────────────────────────┘
```

### 六大核心能力

| 能力 | 说明 |
|------|------|
| **JD 智能搜索** | 输入职位名称，通过 MCP Server 实时搜索 BOSS直聘 / 猎聘 / 智联 / 51job 的真实 JD |
| **候选人画像提取** | 自动解析上传的简历 / 项目文档（PDF / DOCX / 文本），抽取结构化事实 |
| **智能简历生成** | 基于 JD + 候选人画像，用 LLM 生成 STAR 风格、关键词对齐的简历内容 |
| **HTML 简历渲染** | 将结构化简历 JSON 渲染为专业排版的 HTML，支持主题切换、布局调整 |
| **差距分析与追问** | 对比岗位要求与候选人能力，找出技能缺口并主动追问缺失信息 |
| **交互式模拟面试** | 一问一答模拟面试：基于岗位 + 简历 + 知识库生成 5 道题，支持文字/语音作答，Judge Agent 四维度评分 + 综合评价 |

---

## 3. 使用流程

典型的用户旅程分为 4 步：

```
用户                               系统
 │                                  │
 │  ① 输入职位名称                  │
 │  "Agent应用开发工程师 北京"       │
 │ ──────────────────────────────> │
 │                                  │  MCP 搜索真实 JD
 │                                  │  ┌─ BOSS直聘
 │                                  │  ├─ 猎聘
 │                                  │  ├─ 智联
 │                                  │  └─ 51job
 │                                  │  JD Agent 结构化解析
 │                                  │
 │  ② 上传个人材料                  │
 │  简历.pdf + 项目总结.docx        │
 │ ──────────────────────────────> │
 │                                  │  Profile Agent 事实抽取
 │                                  │  Gap Agent 差距分析
 │                                  │  → 提示缺失信息
 │                                  │
 │  ③ 补充信息 + 微调简历           │
 │  "把项目经历写得更突出后端能力"    │
 │ ──────────────────────────────> │
 │                                  │  Content Agent 局部更新
 │                                  │  Render Agent 实时预览
 │                                  │
 │  ④ 导出简历 + 准备面试           │
 │  "导出PDF" "生成面试题"           │
 │ ──────────────────────────────> │
 │                                  │  Interview Agent 生成问答
 │                                  │  Export Service 输出文件
 │                                  │
 │  获得：定制简历 + 面试准备资料     │
```

---

## 4. 核心技术架构

### 4.1 多 Agent 协作架构

系统采用 **LangGraph 状态图** 编排多个独立 Agent。每个 Agent 有严格的读写契约，通过共享状态（CopilotState）通信，Agent 之间不直接互调。

```
                          ┌─────────────────────┐
                          │    用户输入          │
                          └────────┬────────────┘
                                   │
                          ┌────────▼────────────┐
                          │   Planner Agent      │
                          │  ┌─────────────────┐ │
                          │  │ Intent Classifier│ │  意图分类
                          │  ├─────────────────┤ │  + 实体提取
                          │  │ Plan Builder    │ │  + 执行计划生成
                          │  └─────────────────┘ │
                          └────────┬────────────┘
                                   │
                    ┌──────────────┼──────────────┐
                    │              │              │
           ┌────────▼──────┐ ┌────▼───────┐ ┌───▼──────────┐
           │  JD Agent     │ │ Profile    │ │ Question     │
           │  ─────────── │ │ Agent      │ │ Agent        │
           │  解析JD文本   │ │ ────────── │ │ ──────────── │
           │  → 结构化Job  │ │ 事实抽取   │ │ 自由问答     │
           └────────┬──────┘ └────┬───────┘ └──────────────┘
                    │              │
           ┌────────▼──────┐       │
           │  Gap Analysis │       │
           │  Agent        │       │
           │  ──────────── │       │
           │  技能缺口分析  │       │
           │  追问问题生成  │       │
           └────────┬──────┘       │
                    │              │
                    └──────┬───────┘
                           │
                  ┌────────▼──────────┐
                  │  Resume Content   │
                  │  Agent            │
                  │  ─────────────── │
                  │  简历JSON生成     │
                  │  STAR改写        │
                  │  关键词对齐      │
                  └────────┬──────────┘
                           │
                  ┌────────▼──────────┐
                  │  Resume Render    │
                  │  Agent            │
                  │  ─────────────── │
                  │  渲染配置更新     │
                  │  HTML简历生成     │
                  └────────┬──────────┘
                           │
                  ┌────────▼──────────┐
                  │  Respond 节点     │
                  │  生成最终回复     │
                  └───────────────────┘
```

> **模拟面试不在 LangGraph 图内**：用户说"开始模拟面试"时，Planner 路由到
> `interview_trigger` 节点（仅引导），真正的交互式面试由独立的
> `POST /api/interview/*` 端点驱动（一问一答 + Judge 评分 + 语音输入）。

**Agent 契约机制**：每个 Agent 声明可读字段、可写字段和禁写字段，运行时由 Plan Validator 校验，防止误写状态。

> **双 Agent 系统说明（v2.0）**：项目存在两套独立的 Agent 范式，各司其职：
> - **LangGraph 状态图**：编排面试准备全流程（JD→Gap→Resume→Interview），状态机驱动，确定性路由
> - **AgentLoop（Boss Harness）**：驱动 Boss 直聘搜索/投递/沟通，LLM 规划→工具执行→重规划→总结，仅在用户显式触发时运行（`POST /api/boss/agent/run`）
> 
> 两套系统通过 `POST /api/boss/import-job` 桥接：Boss 搜到的岗位导入 `CopilotState.job`，随后触发 LangGraph 全链路分析。

### 4.2 Plan Mode 动态编排

传统方案中，执行链路硬编码在 `_INTENT_PLAN` 字典里（固定映射），扩展新 Agent 需要修改核心代码。升级后的 Plan Mode 将流程提升为**可解释、可验证、可执行的 Plan 对象**：

```
用户输入
    │
    ▼
┌──────────────┐     ┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│ Intent & Task │────>│ Plan Builder │────>│ Plan Validator│────>│Plan Executor │
│ Extraction   │     │              │     │              │     │              │
│              │     │ 意图→Plan    │     │ 依赖闭环检查 │     │ 按拓扑序执行 │
│ 主意图识别   │     │ Step生成     │     │ 写冲突检查   │     │ 失败重试     │
│ 次要意图列表 │     │ 依赖关系推导 │     │ 契约合规检查 │     │ 状态Patch    │
│ 实体提取     │     │ 前置条件填充 │     │ 降级策略     │     │ Step日志     │
└──────────────┘     └──────────────┘     └──────────────┘     └──────────────┘
```

**核心优势**：
- 支持**复合意图**：一轮对话可同时触发多个任务（如 "改项目经历 + 调排版"）
- Agent **插件化**：新增 Agent 只需实现接口 + 注册，不改核心编排器
- **可观测**：Plan/Step 级日志和 trace，精准定位每个步骤的输入、输出、耗时

### 4.3 RAG 知识库增强

系统内建 RAG（检索增强生成）能力，将**真实面试经验数据**注入 Agent 的决策过程，使生成的面试问答不再是"LLM 凭空编造"，而是有真实数据支撑：

```
知识入库流程                              检索增强流程
─────────────                            ─────────────

面试经验文件                              用户查询
(.docx/.pdf/扫描件)                        │
    │                                     ▼
    ▼                              ┌──────────────┐
┌──────────────┐                   │Query Embedding│  DashScope
│ FileClassifier│                  │  查询向量化    │  text-embedding-v4
│ 文件类型识别  │                  └──────┬───────┘
│ PDF/DOCX/图片│                         │
└──────┬───────┘                   ┌──────▼───────┐
       │                           │  ChromaDB    │  向量相似度
       ▼                           │  Top-K 召回  │  K=10
┌──────────────┐                   └──────┬───────┘
│ 智能解析      │                          │
│ 文本→直接提取 │                   ┌──────▼───────┐
│ 扫描件→OCR   │                   │  Rerank      │  DashScope
│ 表格→结构化  │                   │  精排 Top-N  │  gte-rerank-v2
└──────┬───────┘                   └──────┬───────┘
       │                                  │
       ▼                                  ▼
┌──────────────┐              ┌──────────────────────┐
│ Chunk & Embed │              │ 注入 Agent Prompt    │
│ 分块+向量化   │              │ + 结构化State        │
│ → ChromaDB   │              │ → LLM 生成增强回复    │
└──────────────┘              └──────────────────────┘
```

### 4.4 状态管理

采用 **Redis + MySQL 双层存储 + LangGraph 运行时状态** 的三层架构：

```
┌─────────────────────────────────────────┐
│            LangGraph CopilotState        │  ← 运行时（内存）
│   job | profile | gaps | resume_json    │     每轮请求创建/恢复
│   render_config | resume_html | qa      │     Agent 读写共享
│   workflow_trace（不持久化）             │
└─────────┬───────────────────────────────┘
          │ 序列化/反序列化
          ▼
┌─────────────────────────────────────────┐
│              Redis                       │  ← 会话缓存层
│   session:{id}:state  (24h TTL)         │     快速读写
│   session:{id}:events (事件流)           │     分布式锁
│   session:{id}:lock  (并发控制)          │
└─────────┬───────────────────────────────┘
          │ 异步持久化
          ▼
┌─────────────────────────────────────────┐
│              MySQL                       │  ← 持久化存储层
│   sessions | jobs | candidate_profiles   │     结构化 JSON 列
│   resume_contents | render_configs       │     版本追踪
│   resume_htmls | interview_qas          │     事件审计
│   conversation_events                   │
└─────────────────────────────────────────┘
```

**关键不变性（State Invariants）**：
1. `resume_content_json` 是简历事实的**唯一真值来源**，所有下游消费它
2. `resume_html` 只能由 `resume_content_json + render_config` 派生，**不可逆向修改内容**
3. 脏标记驱动增量更新：`content_dirty=true` → HTML 和面试问答视为过期，需重跑
4. Agent 永远不互相调用——只通过 LangGraph 状态通信

### 4.5 简历渲染引擎

简历不是"LLM 生成 HTML"，而是**"结构化 JSON → 模板引擎 → 渲染"**的确定性流水线：

```
resume_content_json (结构化数据)
        +
render_config (样式配置)
        │
        ▼
┌──────────────────┐
│ Template Renderer │
│  ─────────────── │
│  加载模板 HTML     │
│  变量替换         │
│   Section 组装    │
│   CSS 变量注入    │
└────────┬─────────┘
         │
         ▼
    resume_html
   (纯 HTML 字符串)
         │
    ┌────┴────┐
    ▼         ▼
  iframe    WeasyPrint
  预览       → PDF
```

这种设计保证了：同一份 `resume_content_json` 切换模板/主题/布局后，内容事实不变，只有呈现方式变化。

### 4.6 技术栈总览

| 层 | 核心技术 | 说明 |
|----|---------|------|
| 前端 | Vue 3 + Vite + Axios | 左右分栏（对话区 + 结果面板 Tabs） |
| 后端框架 | FastAPI (Python) | 异步 API，CORS 支持 |
| Agent 编排 | LangGraph 1.0+（主流程）| StateGraph，条件路由，多节点编排 |
| Agent 编排 | AgentLoop（Boss 自动化）| Plan-then-Execute，ToolRegistry + 17 工具，自定义主循环 |
| LLM Provider | DeepSeek (deepseek-chat) | 主力生成模型，JSON 结构化输出 |
| JD 搜索 | ChromaDB（岗位知识库）| 中文职位离线聚合 + 语义检索 |
| 浏览器自动化 | Playwright + Firefox | Boss 直聘搜索、投递、聊天监控 |
| 反爬机制 | ANTI_DETECT JS + 注入原生操作 | webdriver 隐藏、canvas/WebGL 指纹干扰、window.__bossApply |
| Embedding | DashScope (text-embedding-v4) | 知识库文档向量化 |
| Rerank | DashScope (gte-rerank-v2) | 检索结果精排，5→10 选最佳 |
| 向量数据库 | ChromaDB 1.4+ | 轻量本地向量存储，持久化 |
| OCR | PaddleOCR | 扫描件 PDF / 图片文字识别（中文最优） |
| Boss SQLite | SQLite (WAL 模式) | Boss 岗位/会话/消息/设置本地持久化 |
| 会话存储 | Redis 7.0+ | 状态缓存，24h TTL，分布式锁 |
| 持久化 | MySQL 8.0+ | JSON 列存储，版本追踪，事件审计 |
| 文件解析 | pdfplumber + python-docx + PyMuPDF | 多格式文档智能解析 |
| HTML→PDF | WeasyPrint | 服务端 PDF 导出 |
| CLI | Click + httpx + JSON 信封 | 18 条命令，stdout 纯 JSON |
| 可观测性 | LangSmith | LLM 调用链追踪，成本统计 |

---

## 5. 项目结构速览

```
interview-assistant/
├── backend/
│   ├── main.py                    # FastAPI 入口，启动检查
│   ├── config.yaml                # 全局 YAML 配置
│   ├── config_loader.py           # 配置统一访问层
│   ├── agents/                    # ⭐ 主流程 Agent（LangGraph 状态图编排）
│   │   ├── planner.py             #   意图分类 + 计划生成 + Boss 导入启发式
│   │   ├── jd_agent.py            #   JD 结构化解析（Boss 导入后跳过）
│   │   ├── profile_agent.py       #   候选人画像提取
│   │   ├── gap_agent.py           #   技能差距分析
│   │   ├── content_agent.py       #   简历内容生成
│   │   ├── render_agent.py        #   HTML 简历渲染
│   │   ├── interview_trigger.py   #   模拟面试引导节点
│   │   ├── interview/             #   ★ 交互式模拟面试模块
│   │   │   ├── engine.py          #     InterviewEngine（start→chat→end）
│   │   │   ├── judge.py           #     Judge Agent（四维度评分）
│   │   │   ├── audio.py           #     语音转文字（qwen-audio-turbo）
│   │   │   ├── contracts.py       #     面试数据模型
│   │   │   └── prompts.py         #     出题/面试官/Judge/总结 Prompt
│   │   ├── json_contracts.py      #   LLM 输出 Pydantic Schema
│   │   ├── registry.py            #   Agent 注册中心
│   │   └── contracts.py           #   Agent 契约声明
│   ├── workflow/
│   │   ├── graph.py               #   LangGraph 状态图定义
│   │   ├── state.py               #   CopilotState 完整模型
│   │   ├── trace.py               #   运行时追踪辅助
│   │   └── plan_mode/             #   Plan Mode 编排引擎
│   │       ├── plan_schema.py     #     Plan/Step/Patch 数据模型
│   │       ├── plan_builder.py    #     意图 → Plan 生成
│   │       ├── plan_validator.py  #     契约 & 冲突校验
│   │       └── plan_executor.py   #     Step 执行引擎
│   ├── boss/                      # ⭐ Boss 直聘自动化（Playwright + Firefox）
│   │   ├── boss_firefox.py        #   搜索 + 详情 XHR + ANTI_DETECT 反检测 JS
│   │   ├── boss_automation.py     #   投递（注入 JS + Playwright 混合）+ 聊天 + 风控退避
│   │   ├── boss_state.py          #   SQLite 持久化（applications/conversations/messages）
│   │   ├── boss_replier.py        #   AI 招呼语 + 回复生成
│   │   ├── boss_company.py        #   公司画像聚合 + 法人识别
│   │   ├── boss_geo.py            #   城市/区编码映射
│   │   ├── boss_rag.py            #   历史 JD RAG 检索
│   │   ├── manager.py             #   BossManager 单例（Playwright 单线程 + 监控循环）
│   │   ├── interview/             #   Boss 模块内嵌 LLM 适配器
│   │   │   └── llm_client.py      #   桥接 config_loader 统一配置
│   │   └── agent/                 #   ★ Harness 层（Plan-then-Execute）
│   │       ├── tools.py           #     ToolRegistry + 14 原子工具 + ToolContext
│   │       ├── skills.py          #     3 复合技能（smart_scan/prepare_application/smart_apply）
│   │       ├── loop.py            #     AgentLoop 主循环（规划→执行→重规划→总结）
│   │       └── prompts.py         #     规划/重规划/总结 Prompt
│   ├── cli/                       # ⭐ BOSS 命令行工具（python -m cli.cli）
│   │   ├── cli.py                 #   18 条 Click 命令
│   │   ├── client.py              #   httpx HTTP 客户端
│   │   ├── output.py              #   JSON 信封输出
│   │   └── schema.json            #   AI Agent 工具描述
│   ├── api/                       # API 路由
│   │   ├── chat.py                #   POST /api/chat 主对话端点
│   │   ├── chat_input.py          #   消息预处理（附件解析）
│   │   ├── resume.py              #   简历内容/渲染接口
│   │   ├── export.py              #   导出服务（HTML/PDF/JSON/MD）
│   │   └── boss.py                #   /api/boss/* 路由（系统控制/搜索/投递/Agent/桥接）
│   ├── services/                  # 业务服务层
│   │   ├── job_search.py          #   JD 搜索（ChromaDB + LLM fallback）
│   │   ├── file_classifier.py     #   文件类型智能识别
│   │   ├── ocr_processor.py       #   PaddleOCR 封装
│   │   ├── knowledge_base.py      #   知识库管理
│   │   └── rag_retriever.py       #   RAG 检索器（Embedding + Rerank）
│   ├── models/                    # 模型适配层
│   │   ├── llm.py                 #   LLM 工厂（多 provider）
│   │   ├── embedding.py           #   Embedding 工厂（5 种 provider）
│   │   └── rerank.py              #   Rerank 工厂（3 种 provider）
│   ├── storage/                   # 数据访问层
│   │   ├── redis_client.py        #   Redis 会话存储
│   │   └── mysql_client.py        #   MySQL 持久化
│   ├── prompts/                   # Prompt 模板
│   ├── tools/                     # 工具层
│   │   ├── file_parser.py         #   文件解析（PDF/DOCX/TXT）
│   │   └── template_renderer.py   #   HTML 简历模板渲染
│   ├── templates/                 # Jinja2 HTML 简历模板
│   ├── sql/init_schema.sql        # 数据库初始化
│   └── test/                      # 测试套件
├── frontend/                      # Vue 3 前端
│   └── src/
│       ├── App.vue                #   主布局（左对话 + 右结果）
│       ├── api/index.js           #   Axios API 客户端
│       └── components/
│           ├── ChatPanel.vue      #   聊天面板（输入 + 文件上传）
│           ├── ResultPanel.vue    #   结果展示（Tabs 容器）
│           └── tabs/              #   各 Tab 组件
├── data/knowledge_base/           # 知识库文件
├── scripts/                       # 运维脚本
│   └── ingest_knowledge.py        #   知识库入库
└── docs/                          # 设计文档
    ├── PRD.md
    ├── system_design.md
    ├── workflow_plan.md
    ├── agents_contract.md
    ├── state_schema.md
    └── agent_architecture_optimization_report.md
```

---

## 6. 关键设计原则

### 6.1 Agent 契约

每个 Agent 遵循统一的读写契约。契约以代码形式声明在 `agents/contracts.py`，运行时可自动校验：

- **不跨 Agent 调用**：Agent 只通过 LangGraph 共享状态通信，不知道"前一个 Agent 是谁"
- **不感知流程**：Agent 只知道自己的输入上下文和输出 patch，不关心自己在 Plan 中的位置
- **禁写字段保护**：Plan Validator 拦截不合规写入（如 Render Agent 试图修改 `resume_content_json`）

### 6.2 状态不变性

- `resume_content_json` 是简历内容的唯一真值来源，绝不允许 HTML 成为替代存储
- `resume_html` 是派生结果，可由 `content_json + render_config` 在任何时候重新生成
- 脏标记（dirty flags）驱动增量更新，避免不必要的全链路重跑

### 6.3 渐进式架构演进

本项目采用 **"先显式化 Plan、再模块化 Agent、最后策略化优化"** 的渐进路线：

1. **Phase 1**（已完成）：新增 Plan/Step Schema 和 Agent Registry，Planner 同时生成新旧两种计划，保持向后兼容
2. **Phase 2**（v2.0 已完成）：引入 Boss 直聘自动化模块 + AgentLoop Harness 层（Plan-then-Execute），实现 Boss 搜索→主工作流桥接，双 Agent 系统并存（LangGraph 主流程 + AgentLoop Boss 自动化）
3. **Phase 3**（远期）：主流程 Graph 切换为 `Planner → Executor → Respond`，Agent 完全插件化，支持并行 Step、策略化执行、自适应降级

### 6.4 数据来源标注

所有生成内容保留 `source_refs`，追溯事实来源（用户原始材料 ID / 消息 ID / 知识库文档 ID），保证可解释性——用户可以追问 AI 的每一段简历文字"为什么这么写"。

---

## 7. Boss 直聘集成（v2.0 新增）

Boss 相关能力集成自 `boss_ai_reference/boos-ai` 项目，包含自动搜索、筛选、投递、聊天管理与反爬机制。**核心原则：手动操作为默认，Agent 自动执行为可选高级功能。**

### 7.1 模块结构

```
backend/
├── boss/                        # Boss 直聘自动化（Playwright + Firefox）
│   ├── boss_firefox.py          #   搜索 + 详情 XHR + ANTI_DETECT 反检测 JS
│   ├── boss_automation.py       #   投递（注入 JS + Playwright 混合）+ 聊天 + 风控退避
│   ├── boss_state.py            #   SQLite 持久化（applications/conversations/messages/...）
│   ├── boss_replier.py          #   AI 招呼语 + 回复生成
│   ├── boss_company.py          #   公司画像聚合 + 法人识别
│   ├── boss_geo.py              #   城市/区编码映射
│   ├── boss_rag.py              #   历史 JD RAG 检索
│   ├── manager.py               #   BossManager 单例（Playwright 单线程 + 监控循环）
│   └── agent/                   #   ★ Harness 层（Plan-then-Execute）
│       ├── tools.py             #     ToolRegistry + 14 原子工具 + ToolContext
│       ├── skills.py            #     3 复合技能（smart_scan / prepare_application / smart_apply）
│       ├── loop.py              #     AgentLoop 主循环（规划→执行→重规划→总结）
│       └── prompts.py           #     规划/重规划/总结 Prompt
├── api/boss.py                  # /api/boss/* 路由（含 agent/tools、agent/run、import-job）
└── cli/                         # CLI 命令行工具（python -m cli.cli）
```

### 7.2 Agent Harness（可选高级功能）

- **ToolRegistry**：17 个工具（14 原子 + 3 复合技能），通过 `GET /api/boss/agent/tools` 查看
- **AgentLoop**：四阶段 Plan-then-Execute（规划→执行→重规划→总结），通过 `POST /api/boss/agent/run` 触发
- **ToolContext** 与 `BossManager` 单例绑定：automation 动态读取当前浏览器实例，run_pw 委托 Playwright 专属线程
- **风控约束**：AgentLoop 内置"不投递"约束检查、步间随机延迟（2-6s）、错误退避（5-45s）、冷却感知

### 7.3 Boss → 主工作流桥接

`POST /api/boss/import-job` 将 Boss 搜索到的岗位导入 `CopilotState.job`（id 以 `boss_` 开头、parsed_at 已设置），随后对话区输入"分析这个岗位"触发全链路：

```
Boss 搜索 → 点击"导入岗位" → import-job 写入 Redis state
    → 前端切换对话区 + 自动发送"分析这个岗位"
    → Planner 启发式识别 boss_ 前缀 → import_boss_job 意图
    → gap_agent → content_agent → render_agent（跳过 jd_agent，job 已预置解析）
```

### 7.4 反爬机制（已完整迁移）

- `ANTI_DETECT` JS 注入：隐藏 webdriver、canvas/WebGL 指纹干扰、时区/语言/硬件伪装
- `window.__bossApply` 注入 JS：原生 DOM 点击 + 逐字输入，绕过 CDP 检测
- 人类行为模拟：`_human_type` / `_human_scroll` / `_human_pace`
- 指数退避冷却：rate_limit=120s / captcha=600s / banned=3600s，翻倍封顶 2h

### 7.5 CLI

```bash
cd backend && python -m cli.cli --help
python -m cli.cli status                          # 系统状态
python -m cli.cli search "Python" --city 广州      # 搜索岗位
python -m cli.cli scan-apply                        # 扫描+投递
python -m cli.cli agent-run "搜索广州Python岗位，只搜不投"  # Agent 自动执行
python -m cli.cli import-job <job_url>              # 导入岗位到主工作流
```

后端地址通过环境变量 `INTERVIEW_BOSS_API` 覆盖（默认 `http://127.0.0.1:8000`）。所有命令 stdout 输出统一 JSON 信封。

---

## 8. 交互式模拟面试（v2.1 新增）

### 8.1 定位

替代原 `interview_agent`（批量题库生成）和 `question_agent`（自由问答），提供**一问一答的交互式模拟面试**：
基于岗位 + 简历 + 知识库生成 5 道题，逐个作答，Judge Agent 逐题评分，最后生成综合评价（强项/弱项/学习建议）。

**架构决策**：交互式面试走**独立 REST API**（`POST /api/interview/*`），不走 LangGraph 图。
原因是 LangGraph 是单次 DAG（从入口到 END 一次执行），而模拟面试是多轮会话（5 次 HTTP 请求，每轮积累状态）。
LangGraph 图中仅保留 `interview_trigger` 引导节点。

### 8.2 数据流

```
用户: "开始模拟面试"
  → POST /api/chat → Planner 分类 interview_start
  → interview_trigger 节点（返回引导语）
  → 前端切换到 POST /api/interview/start

POST /api/interview/start
  → 快照 job/resume/profile → RAG 检索 → LLM 生成 5 题
  → 返回第 1 题

POST /api/interview/chat { answer, audio_base64? }
  → 有语音则 qwen-audio-turbo 转文字
  → Judge Agent 四维度评分
  → 返回评分 + 下一题

... 重复 5 轮 ...

POST /api/interview/end
  → 综合评分 + 强项/弱项 + 学习建议
```

### 8.3 模块结构

```
backend/agents/interview/
├── engine.py       InterviewEngine（start→chat→end、出题、轮次管理）
├── judge.py        Judge Agent（准确性/深度/表达/匹配度 四维度 0-10 分）
├── audio.py        语音转文字（DashScope qwen-audio-turbo）
├── contracts.py    Pydantic 数据模型
└── prompts.py      出题 / 面试官 / Judge / 总结 Prompt
backend/api/interview.py   POST /api/interview/start|chat|end|transcribe
```

### 8.4 API 契约

| 端点 | 请求 | 响应要点 |
|------|------|---------|
| `POST /api/interview/start` | `{session_id}` | `{round, total_rounds, message, question}` |
| `POST /api/interview/chat` | `{session_id, answer, audio_base64?}` | `{judge, next_question, is_last}` |
| `POST /api/interview/end` | `{session_id}` | `{avg_score, strong_areas, weak_areas, summary, recommendations}` |
| `POST /api/interview/transcribe` | `{audio_base64}` | `{text}` |

面试状态存 Redis（`session:{session_id}:interview`，TTL 2 小时）。

### 8.5 前端交互

`frontend/src/components/tabs/InterviewPanel.vue` 提供完整模拟面试界面：

- **入口**：对话区说"开始模拟面试"→ App.vue 检测 `interview_trigger` → 自动切到「面试」标签页；或手动点「开始面试」
- **四阶段**：idle（未开始）→ starting（生成题目）→ active（逐题作答）→ done（综合评价）
- **作答方式**：文字输入或麦克风录音（`utils/recorder.js` 用 Web Audio API 采集 PCM 编码为 WAV base64，兼容 qwen-audio-turbo）
- **即时反馈**：每题提交后显示 Judge 四维度评分条形图 + 优点/不足/建议
- **结束**：5 题答完自动生成综合评价（强项/弱项标签 + 学习建议）

---

## 9. 运行与部署

### 9.1 环境依赖

| 依赖 | 版本要求 | 用途 |
|------|---------|------|
| Python | ≥ 3.10 | 后端 |
| Node.js / npm | ≥ 18 | 前端构建 |
| Redis | 7.0+ | 会话状态缓存（24h TTL） |
| MySQL | 8.0+ | 持久化存储 |
| Playwright + Firefox | `pip install playwright && playwright install firefox` | Boss 自动化（可选） |
| ChromaDB | 1.4+ | 知识库向量存储（首次启动自动建库） |

> 所有 Python 依赖见 `backend/requirements.txt`。建议使用虚拟环境：
> `cd backend && python -m venv .venv && .venv\Scripts\activate && pip install -r requirements.txt`

### 9.2 环境变量配置（backend/.env）

后端通过 `backend/.env` 读取密钥与连接配置（参考根目录 `.env.docker` 模板）。**必填项**：

```env
# LLM（必填）
DEEPSEEK_API_KEY=sk-xxx
# 向量化 / 重排（知识库 RAG 用）
DASHSCOPE_API_KEY=sk-xxx
# 可观测性（可选）
LANGCHAIN_API_KEY=lsv2_xxx

# 存储连接（本地开发指向 127.0.0.1，Docker 内指向服务名）
MYSQL_HOST=127.0.0.1
MYSQL_PORT=3306
MYSQL_USER=root
MYSQL_PASSWORD=xxx
MYSQL_DATABASE=interview_assistant
REDIS_HOST=127.0.0.1
REDIS_PORT=6379
REDIS_DB=0
```

各配置项的解析顺序：`.env` → 系统环境变量 → `config.yaml` 默认值（见 `backend/config_loader.py`）。

### 9.3 数据库初始化

**MySQL**（一次性）：
```bash
mysql -h 127.0.0.1 -u root -p < backend/sql/init_schema.sql
```
> 后端启动时会检查 MySQL 连通性（`SELECT 1`），MySQL 未就绪会直接退出。

**Redis**：需先启动（本地或 Docker），否则后端启动检查失败。

**Boss SQLite**（自动）：`backend/boss/.boss_profile/boss_state.db` 由 `init_db()` 在首次访问时自动建表，无需手动创建。

### 9.4 启动后端（端口 8000）

**方式一：完整启动（推荐，含依赖检查）**
```bash
cd backend
python main.py            # 启动前自动检查 MySQL/Redis/知识库，通过后起 uvicorn
```
启动参数来自 `config.yaml` 的 `fastapi` 段（host `0.0.0.0`，port `8000`，debug 时自动 reload）。

**方式二：uvicorn 直接启动（跳过依赖检查）**
```bash
cd backend
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

**健康检查**：`curl http://127.0.0.1:8000/health` → `{"status": "ok"}`

### 9.5 启动前端

**开发模式（端口 3000，代理 /api → localhost:8000）**
```bash
cd frontend
npm install
npm run dev              # 访问 http://localhost:3000
```

**生产构建（输出到 frontend/dist/，由 Nginx 托管）**
```bash
cd frontend
npm run build            # 产物：frontend/dist/
```
> 前端代理规则见 `frontend/vite.config.js`：`/api` 和 `/health` 转发到 `http://localhost:8000`。

### 9.6 Boss 直聘模块（可选）

启动 Boss 功能前需额外准备：
```bash
pip install playwright
playwright install firefox
```

使用流程：
1. 前端进入「BOSS」标签页 → 点击「启动浏览器」（**首次必须可见模式**，弹出 Firefox 扫码登录）
   - 前端交互流程始终以可见模式（headless=false）启动，不受 `config.yaml` 影响
   - `config.yaml` 中 `boss.headless` 仅作用于 CLI / Agent 等未显式指定 headless 的启动路径
   - **首次登录必须 headless=false**（可见浏览器扫码）；登录态保存后如需无人值守自动化，可改 true
2. 登录态持久化在 `backend/boss/.boss_profile/`（cookie + session，重启自动恢复）
3. 搜索岗位 → 点击「导入岗位」→ 自动切换到对话区分析该岗位
4. 可选：设置页填写简历摘要后，启用「自动回复」或使用「AI 自动求职」

> **运行环境要求**：运行后端的 Python 环境必须安装 Playwright + Firefox（`pip install playwright && playwright install firefox`）。
> 若点击「启动浏览器」无反应或报错，先确认后端的 Python 有 playwright，再确认 `python -m playwright install firefox` 已执行。
> 若 `backend/config.yaml` 中 `boss.enabled: false`，`/api/boss/*` 路由仍注册但浏览器不可用。

### 9.7 Docker 一键部署

根目录 `docker-compose.yml` 编排 backend + frontend + mysql + redis 四服务：
```bash
# 准备环境变量（复制模板并按需修改密钥）
cp .env.docker .env

docker-compose up -d --build
```
- 后端：`http://<host>:8000`（`BACKEND_PORT`）
- 前端：`http://<host>:3001`（`FRONTEND_PORT`，Nginx 托管 dist/）
- MySQL / Redis 由 compose 启动，健康检查通过后 backend 才就绪

> Docker 模式下 Boss 浏览器自动化不可用（容器内无 Firefox GUI），Boss 功能建议在本地开发环境使用。

### 9.8 CLI（命令行）

```bash
cd backend
python -m cli.cli status                        # 系统状态
python -m cli.cli search "Python" --city 广州    # 搜索岗位
python -m cli.cli agent-run "搜索岗位，只搜不投"  # Agent 自动执行
python -m cli.cli import-job <job_url>          # 岗位导入主工作流
```

---

**在线演示**: http://8.153.79.69:3001  
**GitHub**: https://github.com/Programmergyt/interview-assistant