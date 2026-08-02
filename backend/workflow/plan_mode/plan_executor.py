"""Plan Executor — 串行执行 Plan，负责重试、错误聚合、状态 Patch 提交。

当前为串行版（渐进迁移第 1 步），后续可扩展为 DAG 并行执行。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from workflow.plan_mode.plan_schema import (
    Plan, PlanStep, PlanResult, StepResult, ConflictStrategy,
)
from workflow.plan_mode.plan_validator import PlanValidator
from workflow.state import CopilotState
from log import get_logger

logger = get_logger("agent")


class PlanExecutor:
    """Plan 执行引擎（串行版）。

    执行流程：
    1. Plan 静态校验
    2. 按依赖拓扑排序
    3. 逐 Step 执行（含重试）
    4. 收集 StepResult
    5. 合并 state patch
    """

    @staticmethod
    async def execute(plan: Plan, state: CopilotState) -> PlanResult:
        """执行 Plan 并返回 PlanResult。"""
        result = PlanResult(plan_id=plan.plan_id, status="running")
        start_time = time.time()

        # 1. 校验
        report = PlanValidator.validate(plan)
        if not report.is_valid:
            for err in report.errors:
                result.errors.append(str(err))
            result.status = "failed"
            logger.error("Plan %s validation failed: %s", plan.plan_id, result.errors)
            return result

        for warn in report.warnings:
            logger.warning("Plan %s validation warning: %s", plan.plan_id, warn)

        # 2. 收集有效步骤（跳过前置条件不满足的）
        active_steps = PlanExecutor._filter_active_steps(plan.steps, state)

        # 3. 串行执行
        state_patches: dict[str, Any] = {}
        for step in active_steps:
            step_result = await PlanExecutor._execute_step(step, plan, state)
            result.step_results.append(step_result)

            if step_result.status == "failed":
                if plan.policy.conflict_strategy == ConflictStrategy.FAIL_FAST:
                    result.status = "failed"
                    logger.warning(
                        "Plan %s aborted at step %s (fail_fast)", plan.plan_id, step.step_id
                    )
                    break
                elif plan.policy.conflict_strategy == ConflictStrategy.SKIP_CONFLICT:
                    continue

            if step_result.state_patch:
                state_patches.update(step_result.state_patch)

        # 4. 合并 patches
        result.total_latency_ms = (time.time() - start_time) * 1000

        if result.status != "failed":
            if result.failed_count > 0:
                result.status = "partial" if plan.policy.partial_success else "failed"
            else:
                result.status = "success"

        logger.info(
            "Plan %s executed: status=%s, steps=%d (success=%d, failed=%d, skipped=%d), latency=%.0fms",
            plan.plan_id, result.status, len(result.step_results),
            result.success_count, result.failed_count, result.skipped_count,
            result.total_latency_ms,
        )
        return result

    @staticmethod
    async def execute_and_merge(
        plan: Plan,
        state: CopilotState,
    ) -> dict[str, Any]:
        """
        执行 Plan 并将所有 step 的 state_patch 合并为一个 flat dict。

        这是与现有 Graph 兼容的入口 — 返回值可直接作为 LangGraph 节点返回值。
        """
        result = await PlanExecutor.execute(plan, state)
        merged: dict[str, Any] = {"plan_result": result}

        for sr in result.step_results:
            if sr.state_patch and sr.status == "success":
                merged.update(sr.state_patch)

        return merged

    # ── 内部方法 ──────────────────────────────────────────────

    @staticmethod
    def _filter_active_steps(steps: list[PlanStep], state: CopilotState) -> list[PlanStep]:
        """过滤掉前置条件标记为 SKIP 的步骤。"""
        active = []
        for step in steps:
            skip_preconditions = [p for p in step.preconditions if p.startswith("SKIP:")]
            if skip_preconditions:
                logger.info(
                    "Step %s (%s) skipped: %s",
                    step.step_id, step.agent_name, "; ".join(skip_preconditions),
                )
                # 将跳过的步骤作为 skipped StepResult 记录
                continue
            active.append(step)
        return active

    @staticmethod
    async def _execute_step(
        step: PlanStep,
        plan: Plan,
        state: CopilotState,
    ) -> StepResult:
        """执行单个 Step（含重试逻辑）。"""
        from agents.registry import AgentRegistry

        handler = AgentRegistry.get(step.agent_name)
        if handler is None:
            return StepResult(
                step_id=step.step_id,
                agent_name=step.agent_name,
                status="failed",
                error=f"Agent '{step.agent_name}' not found in registry",
            )

        start = time.time()
        last_error: Exception | None = None

        for attempt in range(1, step.retry.max_attempts + 1):
            try:
                patch = await asyncio.wait_for(
                    handler(state),
                    timeout=step.retry.timeout_seconds,
                )
                latency = (time.time() - start) * 1000
                logger.info(
                    "Step %s (%s) completed in %.0fms (attempt %d/%d)",
                    step.step_id, step.agent_name, latency, attempt, step.retry.max_attempts,
                )
                return StepResult(
                    step_id=step.step_id,
                    agent_name=step.agent_name,
                    status="success",
                    attempt=attempt,
                    latency_ms=latency,
                    state_patch=patch if isinstance(patch, dict) else {},
                )

            except asyncio.TimeoutError:
                last_error = TimeoutError(
                    f"Step {step.step_id} ({step.agent_name}) timeout after {step.retry.timeout_seconds}s"
                )
                logger.warning("Step %s timed out (attempt %d/%d)", step.step_id, attempt, step.retry.max_attempts)

            except Exception as exc:
                last_error = exc
                logger.warning("Step %s failed (attempt %d/%d): %s", step.step_id, attempt, step.retry.max_attempts, exc)

            if attempt < step.retry.max_attempts:
                await asyncio.sleep(step.retry.backoff_seconds)

        latency = (time.time() - start) * 1000
        return StepResult(
            step_id=step.step_id,
            agent_name=step.agent_name,
            status="failed",
            attempt=step.retry.max_attempts,
            latency_ms=latency,
            error=str(last_error) if last_error else "Unknown error",
        )
