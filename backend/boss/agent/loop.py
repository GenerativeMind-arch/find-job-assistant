"""Agent 主循环 — Plan-then-Execute 模式。

核心流程:
1. LLM 接收目标 + 可用工具 → 输出 JSON 执行计划（一次性规划）
2. Python 执行器按计划顺序调用工具（0 次 LLM 调用）
3. 仅步骤失败/空结果时回调 LLM 修正计划（重规划，最多2次）
4. 全部完成后生成总结（简单任务直接拼接，复杂任务 1 次 LLM）

返回结果中 completion_status 区分三种情况:
  - "completed": 所有计划步骤执行完毕
  - "partial":   步数耗尽但部分工作已完成，可以继续
  - "aborted":   致命错误、连续失败或 LLM 无法生成有效计划
"""

import asyncio
import json
import random
import re
import sys
import time
from typing import Any, Callable, Dict, List, Optional

from .tools import summarize_result, ToolContext
from .prompts import (
    PLANNING_SYSTEM_PROMPT,
    USER_GOAL_PROMPT,
    REPLAN_PROMPT,
    SUMMARY_PROMPT,
)

# —— 打印辅助：Windows GBK/stdout 可能无法编码 emoji ——
def _p(msg: str):
    """安全 print，自动将 emoji 替换为纯文本标记，避免 Windows 控制台 UnicodeEncodeError。"""
    safe = msg.encode("ascii", errors="replace").decode("ascii")
    print(safe, flush=True)


# —— 里程碑工具映射 ——
MILESTONE_TOOLS = {
    "search_jobs": "searched",
    "preview_companies": "companies_previewed",
    "analyze_jd": "analyzed",
    "optimize_resume_for_jd": "resume_optimized",
    "get_chat_suggestion": "chat_suggestion_generated",
    "apply_job": "applied",
    "batch_apply": "batch_applied",
    "generate_reply": "reply_generated",
    "smart_scan": "searched",
    "smart_apply": "applied",
    "prepare_application": "analyzed",
}

APPLY_TOOLS = {"apply_job", "batch_apply", "smart_apply"}
SEARCH_TOOLS = {"search_jobs", "preview_companies", "smart_scan"}


class AgentLoop:
    """Plan-then-Execute Agent 主循环。"""

    def __init__(
        self,
        registry,
        llm_chat: Callable,
        goal: str,
        max_steps: int = 12,
        extra_context: str = "",
        on_step: Optional[Callable] = None,
        on_plan: Optional[Callable] = None,
    ):
        self.registry = registry
        self.llm_chat = llm_chat
        self.goal = goal
        self.max_steps = max_steps
        self.extra_context = extra_context
        self.on_step = on_step
        self.on_plan = on_plan
        self.steps: List[dict] = []
        self._milestones: Dict[str, bool] = {v: False for v in MILESTONE_TOOLS.values()}
        self._replan_count = 0
        self._max_replans = 2
        self._plan_data: dict = {}
        self._llm_calls = 0

    def set_llm_chat(self, fn: Callable):
        self.llm_chat = fn

    # -- 风控间隔 --

    async def _inter_step_delay(self) -> None:
        gap = random.uniform(2.0, 6.0)
        _p(f"[Agent]   interval {gap:.1f}s...")
        await asyncio.sleep(gap)

    async def _error_backoff(self, attempt: int) -> None:
        ranges = {1: (5.0, 10.0), 2: (15.0, 25.0)}
        min_s, max_s = ranges.get(attempt, (30.0, 45.0))
        gap = random.uniform(min_s, max_s)
        _p(f"[Agent]   backoff {gap:.1f}s (fail #{attempt})...")
        await asyncio.sleep(gap)

    async def _check_automation_cooldown(self) -> None:
        try:
            ctx = ToolContext.get()
            if ctx.automation and hasattr(ctx.automation, 'in_cooldown'):
                if ctx.automation.in_cooldown():
                    remaining = ctx.automation._cooldown_remaining()
                    _p(f"[Agent]   cooldown, wait {remaining:.0f}s...")
                    await asyncio.sleep(remaining + random.uniform(1.0, 3.0))
        except Exception:
            pass

    # -- 主入口 --

    async def run(self) -> dict:
        t_start = time.time()

        # Phase 1: Plan
        _p("[Agent] Phase 1: Planning...")
        try:
            plan_data = await self._plan()
        except Exception as e:
            _p(f"[Agent] Plan failed: {e}")
            return self._build_result("aborted", 0, hint=f"LLM plan failed: {e}")

        self._plan_data = plan_data
        constraints = plan_data.get("constraints", [])
        plan_steps = plan_data.get("plan", [])
        analysis = plan_data.get("analysis", "")

        _p(f"[Agent] Plan: {len(plan_steps)} steps -- {analysis}")
        for i, s in enumerate(plan_steps, 1):
            _p(f"[Agent]   {i}. {s.get('tool')} -- {s.get('reason', '')}")

        if self.on_plan:
            try:
                await self.on_plan(plan_data)
            except Exception:
                pass

        if not plan_steps:
            return self._build_result("aborted", 0, hint="LLM produced empty plan")

        # Phase 2: Execute
        _p("[Agent] Phase 2: Execute...")
        context: Dict[int, Any] = {}
        remaining = list(plan_steps)
        step_counter = 0
        consecutive_failures = 0

        while remaining and step_counter < self.max_steps:
            step = remaining.pop(0)
            step_counter += 1

            # constraint check
            if self._violates_constraints(step.get("tool", ""), constraints):
                step_record = {
                    "step": step_counter, "tool": step.get("tool", ""),
                    "args": step.get("args", {}), "thought": step.get("reason", ""),
                    "result": "SKIPPED (user constraint)", "skipped": True,
                }
                self.steps.append(step_record)
                await self._notify(step_record)
                _p(f"[Agent]   SKIP step {step_counter} {step.get('tool')}")
                continue

            try:
                resolved_args = self._resolve_args(step.get("args", {}), context)
            except Exception as e:
                resolved_args = step.get("args", {})
                _p(f"[Agent]   arg resolve failed: {e}")

            tool_name = step.get("tool", "")
            await self._check_automation_cooldown()
            _p(f"[Agent]   step {step_counter} {tool_name}...")
            t_tool = time.time()
            result = await self.registry.execute(tool_name, resolved_args)
            _p(f"[Agent]   done ({time.time() - t_tool:.1f}s)")

            is_error = isinstance(result, dict) and "error" in result
            is_empty = self._is_empty_result(tool_name, result)

            step_record = {
                "step": step_counter, "tool": tool_name,
                "args": resolved_args, "thought": step.get("reason", ""),
                "result": summarize_result(result)[:300],
            }

            if is_error:
                step_record["error"] = True
                consecutive_failures += 1
            else:
                consecutive_failures = 0
                milestone = MILESTONE_TOOLS.get(tool_name)
                if milestone:
                    self._milestones[milestone] = True

            self.steps.append(step_record)
            context[step_counter] = result
            await self._notify(step_record)

            if consecutive_failures >= 3:
                _p("[Agent] 3 consecutive failures, abort")
                return self._build_result("aborted", step_counter,
                    hint="3 consecutive steps failed; check browser and AI config")

            needs_replan = (is_error or is_empty) and bool(remaining)
            if needs_replan:
                if self._replan_count < self._max_replans:
                    await self._error_backoff(consecutive_failures)
                    _p(f"[Agent] Phase 3: Replan ({self._replan_count + 1}/{self._max_replans})...")
                    new_steps = await self._replan(
                        context, step_counter, step, result, constraints)
                    self._replan_count += 1
                    if new_steps:
                        remaining = new_steps
                        consecutive_failures = 0
                        _p(f"[Agent]   new plan: {len(new_steps)} steps")
                        continue
                    else:
                        _p("[Agent] LLM says cannot continue")
                        summary = await self._summarize(context, exhausted=True)
                        return self._build_result("aborted", step_counter, override_summary=summary)
                else:
                    _p(f"[Agent] max replans ({self._max_replans}) reached, continue remaining")

            if remaining and not is_error:
                await self._inter_step_delay()

        # Phase 4: Summarize
        _p("[Agent] Phase 4: Summarize...")

        if step_counter >= self.max_steps and remaining:
            status = "partial"
            hint = "Step budget exhausted. Use a more specific goal to continue."
        else:
            status = "completed"
            hint = None

        summary = await self._summarize(context, exhausted=(status == "partial"))
        elapsed = time.time() - t_start
        _p(f"[Agent] {status.upper()} -- {elapsed:.1f}s, {step_counter} steps, {self._llm_calls} LLM calls")

        kwargs = {"override_summary": summary}
        if hint:
            kwargs["hint"] = hint
        return self._build_result(status, step_counter, **kwargs)

    # -- Phase 1: Plan --

    async def _plan(self) -> dict:
        messages = [
            {"role": "system", "content": PLANNING_SYSTEM_PROMPT.format(
                tools=self.registry.get_text_description(),
                extra_context=self._build_extra_context())},
            {"role": "user", "content": USER_GOAL_PROMPT.format(
                goal=self.goal, context=self._build_context() or "none")},
        ]
        response = await self._call_llm_safe(messages, temperature=0.2)
        return self._parse_plan_json(response)

    def _parse_plan_json(self, response: str) -> dict:
        response = response.strip()
        try:
            return json.loads(response)
        except json.JSONDecodeError:
            pass
        cleaned = response
        for prefix in ("```json", "```"):
            if cleaned.lower().startswith(prefix):
                cleaned = cleaned[len(prefix):].strip()
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3].strip()
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            pass
        m = re.search(r'\{.*\}', cleaned, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
        raise ValueError(f"Cannot parse JSON plan: {response[:300]}")

    # -- Phase 3: Replan --

    async def _replan(self, context, failed_step_num, failed_step, failed_result, constraints):
        completed_steps = self._format_completed_steps()
        error_message = (
            failed_result.get("error", str(failed_result))
            if isinstance(failed_result, dict) else str(failed_result))
        context_summary = self._summarize_context(context)

        prompt = REPLAN_PROMPT.format(
            goal=self.goal,
            completed_steps=completed_steps or "(none)",
            failed_step=failed_step_num,
            failed_tool=failed_step.get("tool", "?"),
            failed_args=json.dumps(failed_step.get("args", {}), ensure_ascii=False),
            error_message=error_message[:500],
            tools=self.registry.get_text_description(),
            context_summary=context_summary,
            constraints=json.dumps(constraints, ensure_ascii=False),
        )

        try:
            response = await self._call_llm_safe(
                [{"role": "user", "content": prompt}], temperature=0.2)
            data = self._parse_plan_json(response)
            new_plan = data.get("plan", [])
            if not new_plan:
                _p(f"[Agent]   LLM cannot continue: {data.get('analysis', '')}")
                return None
            return new_plan
        except Exception as e:
            _p(f"[Agent]   replan failed: {e}")
            return None

    # -- Phase 4: Summarize --

    async def _summarize(self, context, exhausted=False):
        execution_log = self._build_execution_log()
        if len(self.steps) <= 3 and not exhausted:
            return self._simple_summary()
        messages = [{"role": "user", "content": SUMMARY_PROMPT.format(
            goal=self.goal, execution_log=execution_log)}]
        try:
            return await self._call_llm_safe(messages, temperature=0.3)
        except Exception:
            return self._simple_summary()

    def _simple_summary(self) -> str:
        parts = []
        for s in self.steps:
            tool = s.get("tool", "")
            result = s.get("result", "")
            if s.get("skipped"):
                parts.append(f"SKIP {tool}")
            elif s.get("error"):
                parts.append(f"FAIL {tool}: {result[:100]}")
            else:
                parts.append(f"OK {tool}: {result[:150]}")
        return "\n".join(parts) if parts else "No actions executed."

    # -- helpers --

    def _build_result(self, status: str, steps: int, **kwargs) -> dict:
        result = {
            "completion_status": status, "steps": steps,
            "summary": kwargs.get("override_summary", ""),
            "detail": self.steps, "milestones": self._milestones,
            "plan": self._plan_data.get("plan", []),
            "analysis": self._plan_data.get("analysis", ""),
            "replans": self._replan_count,
        }
        if hint := kwargs.get("hint"):
            result["hint"] = hint
        return result

    def _build_extra_context(self) -> str:
        try:
            from boss_state import get_setting, get_today_application_count
            parts = []
            resume = get_setting("resume_summary", "")
            if resume and len(resume) > 5:
                parts.append(f"- resume: {resume[:300]}")
            parts.append(f"- city: {get_setting('default_city', 'national')}")
            limit = get_setting("daily_apply_limit", "15")
            today = get_today_application_count()
            parts.append(f"- today: {today}/{limit}")
            return "\n".join(parts) if parts else "none"
        except Exception:
            return "none"

    def _build_context(self) -> str:
        parts = []
        try:
            from boss_state import get_setting
            resume = get_setting("resume_summary", "")
            if resume and len(resume) > 5:
                parts.append(f"resume: {resume[:500]}")
            wechat = get_setting("wechat_id", "")
            if wechat:
                parts.append(f"wechat: {wechat}")
            location = get_setting("user_location", "")
            if location:
                parts.append(f"location: {location}")
        except Exception:
            pass
        return "\n".join(parts) if parts else "none"

    def _format_completed_steps(self) -> str:
        lines = []
        for s in self.steps:
            status = "FAIL" if s.get("error") else ("SKIP" if s.get("skipped") else "OK")
            lines.append(f"{status} step{s['step']}: {s.get('tool','?')} -> {s.get('result','')[:120]}")
        return "\n".join(lines) if lines else "(none)"

    def _build_execution_log(self) -> str:
        lines = []
        for s in self.steps:
            tool = s.get("tool", "?")
            args = json.dumps(s.get("args", {}), ensure_ascii=False)
            result = s.get("result", "")
            thought = s.get("thought", "")
            status = "FAIL" if s.get("error") else ("SKIP" if s.get("skipped") else "OK")
            lines.append(f"step{s['step']}: {tool}({args}) [{status}]")
            if thought:
                lines.append(f"  reason: {thought}")
            lines.append(f"  result: {result[:200]}")
        return "\n".join(lines)

    def _summarize_context(self, context) -> str:
        parts = []
        for step_num, result in sorted(context.items()):
            if isinstance(result, dict):
                if "error" in result:
                    parts.append(f"step{step_num}: error - {result['error'][:100]}")
                elif "jobs" in result:
                    parts.append(f"step{step_num}: {result.get('count', len(result['jobs']))} jobs")
                elif "analyzed" in result:
                    parts.append(f"step{step_num}: analyzed {result.get('analyzed_count',0)}, avg {result.get('avg_match_score','?')}")
                elif "conversations" in result:
                    parts.append(f"step{step_num}: {result.get('count', 0)} conversations")
                elif "companies" in result:
                    parts.append(f"step{step_num}: {result.get('total_companies', 0)} companies")
                else:
                    parts.append(f"step{step_num}: {summarize_result(result)[:100]}")
            else:
                parts.append(f"step{step_num}: {str(result)[:100]}")
        return "\n".join(parts) if parts else "no context"

    async def _call_llm_safe(self, messages: list, temperature: float = 0.3) -> str:
        last_err = None
        for attempt in range(3):
            try:
                result = self.llm_chat(messages=messages, temperature=temperature)
                self._llm_calls += 1
                return result
            except Exception as e:
                last_err = e
                if attempt < 2:
                    time.sleep(2 ** attempt)
        raise RuntimeError(f"LLM failed (3 retries): {last_err}")

    def _violates_constraints(self, tool_name: str, constraints: List[str]) -> bool:
        if not constraints or tool_name not in APPLY_TOOLS:
            return False
        constraint_text = " ".join(constraints)
        no_apply_keywords = [
            "no apply", "don't apply", "skip apply",
            "不投递", "不要投递", "别投递", "不用投递", "不需投递",
            "不要投", "别投", "不投简历", "不提交简历", "不发送简历",
            "禁止投递", "不自动投递", "先不投", "暂时不投",
            "只找", "只看", "只搜", "仅搜索", "只查", "只浏览",
        ]
        for kw in no_apply_keywords:
            if kw in constraint_text:
                return True
        return False

    def _is_empty_result(self, tool_name: str, result: Any) -> bool:
        if tool_name not in SEARCH_TOOLS:
            return False
        if not isinstance(result, dict) or "error" in result:
            return False
        empty_checks = [
            result.get("jobs", []) == [] and result.get("count", 0) == 0,
            result.get("companies", []) == [] and result.get("total_companies", 0) == 0,
            result.get("analyzed", []) == [] and result.get("analyzed_count", 0) == 0,
        ]
        return any(empty_checks)

    def _resolve_args(self, args: dict, context: Dict[int, Any]) -> dict:
        resolved = {}
        for key, value in args.items():
            if isinstance(value, str) and value.startswith("$"):
                try:
                    resolved[key] = self._resolve_ref(value, context)
                except Exception:
                    resolved[key] = value
            else:
                resolved[key] = value
        return resolved

    def _resolve_ref(self, ref: str, context: Dict[int, Any]) -> Any:
        path = ref[1:].split(".")
        if not path:
            raise ValueError(f"invalid ref: {ref}")
        try:
            step_num = int(path[0])
        except ValueError:
            raise ValueError(f"first segment must be step number: {ref}")
        if step_num not in context:
            raise ValueError(f"step {step_num} not in context")
        current = context[step_num]
        for segment in path[1:]:
            if isinstance(current, dict):
                if segment not in current:
                    raise ValueError(f"key '{segment}' not in step {step_num}")
                current = current[segment]
            elif isinstance(current, list):
                try:
                    current = current[int(segment)]
                except (ValueError, IndexError):
                    raise ValueError(f"index '{segment}' invalid")
            else:
                raise ValueError(f"cannot index into {type(current).__name__}")
        return current

    async def _notify(self, step_record: dict):
        if self.on_step:
            try:
                await self.on_step(step_record)
            except Exception:
                pass
