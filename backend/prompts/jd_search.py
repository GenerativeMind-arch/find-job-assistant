"""JD 搜索 Prompt — LLM 基于职位名称生成标准 JD（MCP fallback 时使用）。"""

JD_SEARCH_PROMPT = """你是一位资深 HR 和招聘专家，请根据以下职位名称生成一份专业、真实的岗位描述（JD）。

职位名称：{job_title}

请生成完整的 JD，必须包含以下所有字段：

- industry: 该职位通常所属的行业（如"互联网/AI"、"金融科技"、"智能制造"等）
- title: 职位的完整正式名称
- tech_stack: 该职位通常需要的技术栈（列出 5-10 项，尽量具体，如 "Python 3.x, LangChain, Docker, Redis" 而非泛泛写 "编程语言"）
- keywords: 核心关键词（5-10 个，如 "Agent开发"、"RAG"、"多模态"等）
- hard_skills: 硬技能要求（3-5 项，具体到工具/框架/方法论）
- soft_skills: 软技能要求（3-5 项，如 "逻辑思维清晰"、"跨部门沟通能力"）
- responsibilities: 岗位职责（5-8 条，每一条具体可量化）
- education_requirement: 学历要求（如 "本科及以上，计算机相关专业"）
- experience_requirement: 经验要求（如 "3-5 年相关工作经验，有大规模系统设计经验"）
- implicit_preferences: 隐性偏好（如 "有大厂背景优先"、"有开源项目经验者优先"、"985/211 优先" 等用人单位不会明说但实际存在的偏好）
- bonus_items: 加分项（3-5 项，如 "有 AI Agent 相关论文发表"、"有从 0 到 1 项目经验"）

要求：
- 内容要真实、具体、专业，不要泛泛而谈
- 技术栈和关键词要反映 2025-2026 年的行业现状
- 岗位职责要可量化、可验证
- 隐性偏好要符合国内招聘实际情况

机器协议：
- 返回且仅返回一个合法 JSON 对象
- 不要输出 Markdown、代码块、注释或额外说明
- 所有 key 必须使用双引号

返回格式如下：
{{
  "industry": "<行业>",
  "title": "<职位全称>",
  "tech_stack": ["<技术1>", "<技术2>", ...],
  "keywords": ["<关键词1>", "<关键词2>", ...],
  "hard_skills": ["<技能1>", "<技能2>", ...],
  "soft_skills": ["<技能1>", "<技能2>", ...],
  "responsibilities": ["<职责1>", "<职责2>", ...],
  "education_requirement": "<学历要求>",
  "experience_requirement": "<经验要求>",
  "implicit_preferences": ["<偏好1>", "<偏好2>", ...],
  "bonus_items": ["<加分项1>", "<加分项2>", ...]
}}
"""
