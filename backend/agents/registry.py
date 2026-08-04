"""Agent 注册中心 — 管理所有 Agent 的注册与查找。

新增 Agent 只需三步：
1. 在 register_all() 中调用 register()
2. 在 contracts.py 中声明 AgentContract
3. 实现 Agent 节点函数（async, 输入 CopilotState, 返回 dict）

Planner 和 Executor 通过 registry 调用 Agent，不感知具体实现类。
"""

from __future__ import annotations

from typing import Callable, Any, Optional

from log import get_logger

logger = get_logger("agent")

_registry: dict[str, Callable] = {}


class AgentRegistry:
    """全局 Agent 注册中心。"""

    @classmethod
    def register(cls, name: str, handler: Callable) -> None:
        """注册一个 Agent。

        Args:
            name: Agent 唯一名称（如 jd_agent）
            handler: 异步节点函数 async (CopilotState) -> dict
        """
        if name in _registry:
            logger.warning("Agent '%s' is being re-registered (overwriting)", name)
        _registry[name] = handler
        logger.info("Agent registered: %s", name)

    @classmethod
    def get(cls, name: str) -> Optional[Callable]:
        """获取已注册的 Agent 处理函数。"""
        return _registry.get(name)

    @classmethod
    def list_all(cls) -> list[str]:
        """列出所有已注册的 Agent 名称。"""
        return sorted(_registry.keys())

    @classmethod
    def is_registered(cls, name: str) -> bool:
        """检查 Agent 是否已注册。"""
        return name in _registry


def register_all():
    """注册所有内置 Agent。

    在应用启动时调用一次。每新增 Agent 在此添加一行 register()。
    """
    from agents.jd_agent import jd_node_async
    from agents.profile_agent import profile_node_async
    from agents.gap_agent import gap_node_async
    from agents.content_agent import content_node_async
    from agents.render_agent import render_node_async
    from agents.interview_trigger import interview_trigger_node_async

    AgentRegistry.register("jd_agent", jd_node_async)
    AgentRegistry.register("profile_agent", profile_node_async)
    AgentRegistry.register("gap_agent", gap_node_async)
    AgentRegistry.register("content_agent", content_node_async)
    AgentRegistry.register("render_agent", render_node_async)
    AgentRegistry.register("interview_trigger", interview_trigger_node_async)

    logger.info("All agents registered: %s", AgentRegistry.list_all())
