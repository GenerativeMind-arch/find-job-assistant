"""Boss Agent 模块 — Plan-then-Execute Agent 核心框架（Harness 层）。

提供:
- ToolRegistry: 工具注册表（14 原子工具 + 3 复合技能）
- ToolContext: 工具运行时上下文（与 BossManager 单例绑定）
- register_all: 一键注册所有工具
- AgentLoop: Plan-then-Execute 主循环（规划→执行→重规划→总结）
- 系统 Prompt 模板

定位：这是可选的"AI 自动求职"高级能力，与主项目 LangGraph 工作流并存。
默认交互仍为手动操作，AgentLoop 只在用户显式触发时运行。
"""

from .tools import ToolRegistry, ToolContext, register_all, summarize_result
from .loop import AgentLoop
from .prompts import AGENT_SYSTEM_PROMPT

__all__ = [
    "ToolRegistry",
    "ToolContext",
    "register_all",
    "summarize_result",
    "AgentLoop",
    "AGENT_SYSTEM_PROMPT",
]
