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
| **面试问答准备** | 基于岗位 + 画像 + 简历，生成技术面 / 项目深挖 / 行为面试的问答集，并可接入 RAG 知识库增强（真实面经） |

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
                  │  Interview Agent  │
                  │  ─────────────── │
                  │  面试问答生成     │
                  │  ← RAG 知识库增强 │
                  └────────┬──────────┘
                           │
                  ┌────────▼──────────┐
                  │  Respond 节点     │
                  │  生成最终回复     │
                  └───────────────────┘
```

**Agent 契约机制**：每个 Agent 声明可读字段、可写字段和禁写字段，运行时由 Plan Validator 校验，防止误写状态。

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
| Agent 编排 | LangGraph 1.0+ | StateGraph，条件路由，多节点编排 |
| LLM Provider | DeepSeek (deepseek-chat) | 主力生成模型，JSON 结构化输出 |
| JD 搜索 | MCP (`mcp-jobs`) | BOSS直聘/猎聘/智联/51job 真实 JD 聚合 |
| Embedding | DashScope (text-embedding-v4) | 知识库文档向量化 |
| Rerank | DashScope (gte-rerank-v2) | 检索结果精排，5→10 选最佳 |
| 向量数据库 | ChromaDB 1.4+ | 轻量本地向量存储，持久化 |
| OCR | PaddleOCR | 扫描件 PDF / 图片文字识别（中文最优） |
| 会话存储 | Redis 7.0+ | 状态缓存，24h TTL，分布式锁 |
| 持久化 | MySQL 8.0+ | JSON 列存储，版本追踪，事件审计 |
| 文件解析 | pdfplumber + python-docx + PyMuPDF | 多格式文档智能解析 |
| HTML→PDF | WeasyPrint | 服务端 PDF 导出 |
| 可观测性 | LangSmith | LLM 调用链追踪，成本统计 |

---

## 5. 项目结构速览

```
interview-assistant/
├── backend/
│   ├── main.py                    # FastAPI 入口，启动检查
│   ├── config.yaml                # 全局 YAML 配置
│   ├── config_loader.py           # 配置统一访问层
│   ├── agents/                    # Agent 实现
│   │   ├── planner.py             #   意图分类 + 计划生成
│   │   ├── jd_agent.py            #   JD 结构化解析
│   │   ├── profile_agent.py       #   候选人画像提取
│   │   ├── gap_agent.py           #   技能差距分析
│   │   ├── content_agent.py       #   简历内容生成
│   │   ├── render_agent.py        #   HTML 简历渲染
│   │   ├── interview_agent.py     #   面试问答生成
│   │   ├── question_agent.py      #   自由问答
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
│   ├── services/                  # 业务服务层
│   │   ├── job_search.py          #   JD 搜索（MCP + LLM fallback）
│   │   ├── file_classifier.py     #   文件类型智能识别
│   │   ├── ocr_processor.py       #   PaddleOCR 封装
│   │   ├── knowledge_base.py      #   知识库管理
│   │   └── rag_retriever.py       #   RAG 检索器（Embedding + Rerank）
│   ├── models/                    # 模型适配层
│   │   ├── llm.py                 #   LLM 工厂（多 provider）
│   │   ├── embedding.py           #   Embedding 工厂（5 种 provider）
│   │   └── rerank.py              #   Rerank 工厂（3 种 provider）
│   ├── api/                       # API 路由
│   │   ├── chat.py                #   POST /api/chat 主对话端点
│   │   ├── chat_input.py          #   消息预处理（附件解析）
│   │   ├── resume.py              #   简历内容/渲染接口
│   │   └── export.py              #   导出服务（HTML/PDF/JSON/MD）
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

1. **Phase 1**（当前）：新增 Plan/Step Schema 和 Agent Registry，Planner 同时生成新旧两种计划，保持向后兼容
2. **Phase 2**（规划中）：Graph 切换到 `Planner → Executor → Respond`，Agent 完全插件化
3. **Phase 3**（远期）：支持并行 Step、策略化执行、自适应降级

### 6.4 数据来源标注

所有生成内容保留 `source_refs`，追溯事实来源（用户原始材料 ID / 消息 ID / 知识库文档 ID），保证可解释性——用户可以追问 AI 的每一段简历文字"为什么这么写"。

---

**在线演示**: http://8.153.79.69:3001  
**GitHub**: https://github.com/Programmergyt/interview-assistant