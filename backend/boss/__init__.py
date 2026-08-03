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

路径引导:
  模块内部大量使用 `from boss_state import ...` / `from llm_client import ...`，
  它们依赖 backend/boss 与 backend/interview 目录在 sys.path 中。
  此处用绝对路径（Path(__file__).resolve()）统一引导，确保无论从哪个工作目录
  或入口导入（`import boss`、`from backend.boss import ...`、直接运行脚本），
  都能正确找到兄弟模块。
"""

import sys
from pathlib import Path

# 确保 backend/boss 目录在 sys.path（使 from boss_state import ... 生效）
_BOSS_DIR = Path(__file__).resolve().parent
# 确保 backend 目录在 sys.path（使 from config_loader import ... 生效）
_BACKEND_DIR = _BOSS_DIR.parent

for _p in (str(_BOSS_DIR), str(_BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)
