"""
Boss 直聘自动化模块 —— 集成自 boos-ai 项目。

提供 BOSS 直聘的自动投递、自动沟通、AI 回复等功能。

核心组件:
  - boss_firefox.py:    Playwright 浏览器自动化基类 (BossScraper)
  - boss_automation.py: 自动投递 + 自动回复核心 (BossAutomation)
  - boss_replier.py:    AI 回复生成 (generate_reply, generate_greeting_ai)
  - boss_state.py:      SQLite 状态管理 (投递记录、会话、消息、设置)
  - boss_rag.py:        历史相似 JD 检索
  - boss_company.py:    公司画像
  - boss_geo.py:        城市/区编码映射
  - interview/llm_client.py: LLM 适配器 (桥接到面试助手配置)
  - manager.py:         BossAutomation 单例管理器
"""
