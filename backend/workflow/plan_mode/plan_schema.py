"""Plan / Step / Policy / Result 数据模型。

将执行计划从 `list[str]`（agent 名称列表）升级为标准化的结构化 Plan 对象。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


# ── Policy ─────────────────────────────────────────────────────

class ConflictStrategy(str, Enum):
    FAIL_FAST = "fail_fast"          # 遇冲突立即中止
    SKIP_CONFLICT = "skip_conflict"  # 跳过冲突步骤继续
    BEST_EFFORT = "best_effort"      # 尽力执行，收集所有错误


class StepPolicy(BaseModel):
    """单个 Step 的执行策略。"""
    max_attempts: int = Field(default=2, ge=1, le=5, description="最大重试次数")
    backoff_seconds: float = Field(default=1.0, ge=0.1, description="重试间隔（秒）")
    timeout_seconds: float = Field(default=120.0, ge=10.0, description="单步超时（秒）")


class PlanPolicy(BaseModel):
    """整体 Plan 的执行策略。"""
    conflict_strategy: ConflictStrategy = Field(default=ConflictStrategy.FAIL_FAST)
    partial_success: bool = Field(default=True, description="是否允许部分成功")
    timeout_ms: int = Field(default=120000, ge=10000, description="整体超时（毫秒）")


# ── Plan / Step ─────────────────────────────────────────────────

class PlanStep(BaseModel):
    """单个执行步骤。"""
    step_id: str = Field(default_factory=lambda: f"st_{uuid.uuid4().hex[:8]}", description="步骤唯一标识")
    agent_name: str = Field(..., description="Agent 名称（如 jd_agent）")
    action: str = Field(default="execute", description="执行动作")
    reads: list[str] = Field(default_factory=list, description="读取的状态字段")
    writes: list[str] = Field(default_factory=list, description="写入的状态字段")
    preconditions: list[str] = Field(default_factory=list, description="前置条件描述列表")
    depends_on: list[str] = Field(default_factory=list, description="依赖的 step_id 列表")
    retry: StepPolicy = Field(default_factory=StepPolicy)


class Plan(BaseModel):
    """完整的执行计划。"""
    plan_id: str = Field(default_factory=lambda: f"pl_{uuid.uuid4().hex[:12]}")
    session_id: str = ""
    intent_bundle: dict = Field(default_factory=dict, description="意图包 {primary, secondary, entities}")
    steps: list[PlanStep] = Field(default_factory=list)
    policy: PlanPolicy = Field(default_factory=PlanPolicy)
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @property
    def step_names(self) -> list[str]:
        """兼容旧 execution_plan: list[str]，返回 agent 名称列表。"""
        return [s.agent_name for s in self.steps]

    @property
    def is_empty(self) -> bool:
        return len(self.steps) == 0


# ── Execution Result ────────────────────────────────────────────

class StepResult(BaseModel):
    """单个 Step 的执行结果。"""
    step_id: str
    agent_name: str
    status: str = Field(default="pending", description="pending | running | success | failed | skipped")
    attempt: int = Field(default=0)
    latency_ms: float = Field(default=0.0)
    state_patch: Optional[dict] = Field(default=None, description="Agent 返回的状态 patch")
    error: Optional[str] = Field(default=None)
    warnings: list[str] = Field(default_factory=list)


class PlanResult(BaseModel):
    """整个 Plan 的执行结果。"""
    plan_id: str
    status: str = Field(default="running", description="running | success | partial | failed")
    step_results: list[StepResult] = Field(default_factory=list)
    total_latency_ms: float = Field(default=0.0)
    errors: list[str] = Field(default_factory=list)

    @property
    def success_count(self) -> int:
        return sum(1 for r in self.step_results if r.status == "success")

    @property
    def failed_count(self) -> int:
        return sum(1 for r in self.step_results if r.status == "failed")

    @property
    def skipped_count(self) -> int:
        return sum(1 for r in self.step_results if r.status == "skipped")


# ── State Patch ─────────────────────────────────────────────────

class PatchOp(str, Enum):
    SET = "set"           # 完全替换
    MERGE = "merge"       # 合并 dict
    APPEND = "append"     # 追加到 list
    REMOVE = "remove"     # 从 list 移除


class StatePatch(BaseModel):
    """Agent 返回的结构化状态变更。"""
    ops: list[dict] = Field(default_factory=list, description="[{op, field, value}, ...]")
    version_bumps: dict[str, int] = Field(default_factory=dict, description="{field: new_version}")
    agent_name: str = ""
    step_id: str = ""
