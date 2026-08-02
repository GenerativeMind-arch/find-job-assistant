"""Plan Validator — Plan 静态校验器。

校验项：
1. 依赖闭环检测（无循环依赖）
2. 写冲突检测（两个无依赖 step 写同一字段）
3. Agent 契约合规（writes 在允许范围内）
"""

from __future__ import annotations

from collections import defaultdict

from workflow.plan_mode.plan_schema import Plan, PlanStep
from log import get_logger

logger = get_logger("agent")


class ValidationError:
    def __init__(self, code: str, message: str, step_id: str = ""):
        self.code = code
        self.message = message
        self.step_id = step_id

    def __str__(self) -> str:
        return f"[{self.code}] {self.message}" + (f" (step: {self.step_id})" if self.step_id else "")


class ValidationReport:
    def __init__(self):
        self.errors: list[ValidationError] = []
        self.warnings: list[ValidationError] = []

    @property
    def is_valid(self) -> bool:
        return len(self.errors) == 0

    def add_error(self, code: str, message: str, step_id: str = ""):
        self.errors.append(ValidationError(code, message, step_id))

    def add_warning(self, code: str, message: str, step_id: str = ""):
        self.warnings.append(ValidationError(code, message, step_id))


class PlanValidator:
    """Plan 静态校验器。"""

    @staticmethod
    def validate(plan: Plan) -> ValidationReport:
        """对 Plan 执行全部校验规则。"""
        report = ValidationReport()

        if plan.is_empty:
            return report  # 空计划无需校验

        PlanValidator._check_circular_deps(plan, report)
        PlanValidator._check_write_conflicts(plan, report)
        PlanValidator._check_contract_compliance(plan, report)

        if report.is_valid:
            logger.info("Plan %s validation passed (steps=%d)", plan.plan_id, len(plan.steps))
        else:
            logger.warning("Plan %s validation failed: %d errors, %d warnings",
                           plan.plan_id, len(report.errors), len(report.warnings))

        return report

    @staticmethod
    def _check_circular_deps(plan: Plan, report: ValidationReport) -> None:
        """检测依赖闭环。"""
        # 构建邻接表
        adj: dict[str, list[str]] = defaultdict(list)
        for step in plan.steps:
            for dep in step.depends_on:
                adj[step.step_id].append(dep)

        # DFS 检测环
        WHITE, GRAY, BLACK = 0, 1, 2
        color: dict[str, int] = {s.step_id: WHITE for s in plan.steps}

        def dfs(node: str, path: list[str]) -> bool:
            color[node] = GRAY
            for neighbor in adj.get(node, []):
                if color.get(neighbor) == GRAY:
                    cycle = " → ".join(path + [neighbor, node])
                    report.add_error("CIRCULAR_DEP", f"检测到循环依赖：{cycle}", node)
                    return True
                if color.get(neighbor) == WHITE:
                    if dfs(neighbor, path + [neighbor]):
                        return True
            color[node] = BLACK
            return False

        for step in plan.steps:
            if color[step.step_id] == WHITE:
                dfs(step.step_id, [step.step_id])

    @staticmethod
    def _check_write_conflicts(plan: Plan, report: ValidationReport) -> None:
        """检测写冲突：两个无依赖关系的 step 写同一字段。"""
        writes_map: dict[str, list[str]] = defaultdict(list)
        for step in plan.steps:
            for field in step.writes:
                writes_map[field].append(step.step_id)

        for field, step_ids in writes_map.items():
            if len(step_ids) > 1:
                # 检查这些 step 之间是否有依赖关系
                steps_by_id = {s.step_id: s for s in plan.steps}
                for i, sid_a in enumerate(step_ids):
                    for sid_b in step_ids[i + 1:]:
                        step_a = steps_by_id.get(sid_a)
                        step_b = steps_by_id.get(sid_b)
                        if step_a and step_b:
                            # A 不依赖 B，B 也不依赖 A → 冲突
                            a_depends_b = sid_b in step_a.depends_on
                            b_depends_a = sid_a in step_b.depends_on
                            if not a_depends_b and not b_depends_a:
                                report.add_warning(
                                    "WRITE_CONFLICT",
                                    f"两个无依赖步骤 ({sid_a}, {sid_b}) 同时写入 '{field}'",
                                )

    @staticmethod
    def _check_contract_compliance(plan: Plan, report: ValidationReport) -> None:
        """检查 Agent 契约合规性（writes 是否在允许范围内）。"""
        from agents.contracts import AgentContract

        for step in plan.steps:
            contract = AgentContract.get(step.agent_name)
            if contract is None:
                report.add_warning("UNKNOWN_AGENT", f"Agent '{step.agent_name}' 未注册契约", step.step_id)
                continue

            # 检查 writes 是否与 forbidden_writes 重叠
            forbidden = set(contract.forbidden_writes)
            for field in step.writes:
                if field in forbidden:
                    report.add_error(
                        "CONTRACT_VIOLATION",
                        f"Agent '{step.agent_name}' 试图写入禁止字段 '{field}'（契约禁止：{contract.forbidden_writes}）",
                        step.step_id,
                    )
