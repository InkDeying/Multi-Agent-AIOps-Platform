"""Agent Harness —— 诊断链路的策略中枢.

它回答的是"用哪个模型档位、允许跑多久、下一步交给谁"这类问题, 具体实现分散在:
  - ``harness/prompts/``          prompt 文本
  - ``runtime/budget.py``         用量统计与预算判定
  - ``runtime/replan_policy.py``  Replanner 的 pre-LLM 硬规则
  - ``runtime/errors.py``         异常归类

本文件保留的职责: 从 settings 读出各档位/阈值, 组装消息列表, 以及把上面几块
拼成调用方看到的单一入口。所有 settings 访问都收在这里, 下游模块因此都是纯函数。

历史: 这个文件曾经是 826 行, 同时装着 fast 图的 5 套 prompt、RAG Chat 的 prompt、
settings 透传、预算计算、SSE 事件构造、replan 策略和异常归类。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from app.config import settings
from app.harness.prompts import fast as fast_prompts
from app.harness.prompts import rag as rag_prompts
from app.harness.runtime.budget import (
    HarnessBudgetStatus,
    HarnessUsageStats,
    build_budget_event,
    build_usage_stats_event,
    evaluate_budget,
)
from app.harness.runtime.errors import ErrorKind, classify_error
from app.harness.runtime.replan_policy import (
    HarnessAction,
    HarnessDecision,
    evaluate_pre_llm,
)

# 这些名字历史上从本模块导出, 下游 (orchestration / services / 测试) 仍按原路径导入。
__all__ = [
    "AgentHarness",
    "ErrorKind",
    "HarnessAction",
    "HarnessBudgetStatus",
    "HarnessDecision",
    "HarnessUsageStats",
    "ToolRunnerPolicy",
    "get_agent_harness",
]


@dataclass(frozen=True)
class ToolRunnerPolicy:
    """交给 ``tool_runner.run_parallel_agent`` 的循环上限."""

    max_iters: int
    max_parallel: int


class AgentHarness:
    # ==================== 模型档位 ====================
    def router_model(self) -> str:
        return settings.dashscope_router_model

    def planner_model(self) -> str:
        return settings.agent_planner_model or settings.dashscope_router_model

    def executor_model(self) -> str:
        return settings.agent_executor_model or settings.dashscope_chat_model

    def replanner_model(self) -> str:
        return settings.agent_planner_model or settings.dashscope_router_model

    def report_model(self) -> str:
        return settings.agent_report_model or settings.dashscope_chat_model

    def report_decision_model(self) -> str:
        return settings.agent_planner_model or settings.dashscope_router_model

    def rag_chat_model(self) -> str:
        return settings.dashscope_chat_model

    # ==================== 运行上限 ====================
    def default_permission_mode(self) -> str:
        return settings.permission_mode

    def agent_max_concurrency(self) -> int:
        return settings.agent_max_concurrency

    def max_total_tokens(self) -> int:
        return max(0, settings.harness_max_total_tokens)

    def max_total_ms(self) -> int:
        return max(0, settings.harness_max_total_ms)

    def budget_warn_ratio(self) -> float:
        return min(1.0, max(0.0, settings.harness_budget_warn_ratio))

    def max_agent_steps(self) -> int:
        return settings.agent_max_steps

    def graph_recursion_limit(self) -> int:
        return self.max_agent_steps() * 3 + 5

    def max_reroutes(self) -> int:
        return settings.agent_max_reroutes

    def min_reroute_past_steps(self) -> int:
        return settings.agent_reroute_min_past_steps

    def replanner_past_step_chars(self) -> int:
        return max(200, settings.agent_replanner_past_step_chars)

    def executor_parallel_enabled(self) -> bool:
        return settings.executor_parallel_enabled

    def executor_policy(self) -> ToolRunnerPolicy:
        return ToolRunnerPolicy(
            max_iters=settings.executor_max_iters,
            max_parallel=settings.executor_max_parallel,
        )

    def rag_tool_policy(self) -> ToolRunnerPolicy:
        return ToolRunnerPolicy(
            max_iters=settings.rag_chat_max_tool_rounds,
            max_parallel=4,
        )

    # ==================== 预算 ====================
    def evaluate_budget(self, stats: HarnessUsageStats) -> HarnessBudgetStatus:
        return evaluate_budget(
            stats,
            token_limit=self.max_total_tokens(),
            time_limit=self.max_total_ms(),
            warn_ratio=self.budget_warn_ratio(),
        )

    def build_usage_stats_event(self, stats: HarnessUsageStats) -> dict[str, Any]:
        return build_usage_stats_event(stats)

    def build_budget_event(self, status: HarnessBudgetStatus) -> dict[str, Any] | None:
        return build_budget_event(status)

    # ==================== SkillRouter ====================
    def build_skill_router_messages(
        self,
        *,
        menu: str,
        user_input: str,
        generic: str,
        lessons: str = "",
    ) -> list[dict[str, str]]:
        lessons_section = (
            fast_prompts.SKILL_ROUTER_LESSONS_TEMPLATE.format(lessons=lessons)
            if lessons
            else ""
        )
        return [
            {"role": "system", "content": fast_prompts.SKILL_ROUTER_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": fast_prompts.SKILL_ROUTER_USER_TEMPLATE.format(
                    menu=menu,
                    input=user_input,
                    generic=generic,
                    lessons_section=lessons_section,
                ),
            },
        ]

    # ==================== Planner ====================
    def build_planner_messages(
        self,
        *,
        user_input: str,
        skill_display_name: str,
        skill_playbook: str,
    ) -> list[dict[str, str]]:
        return [
            {"role": "system", "content": fast_prompts.PLANNER_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": fast_prompts.PLANNER_USER_TEMPLATE.format(
                    input=user_input,
                    skill_display_name=skill_display_name,
                    skill_playbook=skill_playbook,
                ),
            },
        ]

    def planner_fallback_plan(self, reason: str) -> list[str]:
        if reason == "empty_plan":
            return ["汇总现有信息, 给出诊断结论"]
        return ["查询知识库, 寻找类似问题的处理经验", "汇总现有信息, 给出诊断结论"]

    # ==================== Executor ====================
    def executor_system_prompt(self) -> str:
        return fast_prompts.EXECUTOR_SYSTEM_PROMPT

    def build_executor_task_prompt(
        self,
        *,
        plan: list[str],
        step_index: int,
        total_steps: int,
        current_step: str,
    ) -> str:
        plan_text = "\n".join(f"  {i+1}. {s}" for i, s in enumerate(plan))
        return fast_prompts.EXECUTOR_TASK_TEMPLATE.format(
            plan_text=plan_text,
            step_index=step_index,
            total_steps=total_steps,
            current_step=current_step,
        )

    # ==================== Replanner ====================
    def build_replanner_messages(
        self,
        *,
        user_input: str,
        current_time: str,
        current_skill_line: str,
        candidate_skills_text: str,
        tried_skills_text: str,
        reroute_count: int,
        reroute_quota_hint: str,
        plan_text: str,
        past_steps_text: str,
    ) -> list[dict[str, str]]:
        return [
            {"role": "system", "content": fast_prompts.REPLANNER_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": fast_prompts.REPLANNER_USER_TEMPLATE.format(
                    input=user_input,
                    current_time=current_time,
                    current_skill_line=current_skill_line,
                    candidate_skills_text=candidate_skills_text,
                    tried_skills_text=tried_skills_text,
                    reroute_count=reroute_count,
                    max_reroutes=self.max_reroutes(),
                    reroute_quota_hint=reroute_quota_hint,
                    plan_text=plan_text,
                    past_steps_text=past_steps_text,
                ),
            },
        ]

    def build_reroute_quota_hint(self, *, reroute_count: int, past_steps_count: int) -> str:
        quota_remaining = max(0, self.max_reroutes() - reroute_count)
        min_steps = self.min_reroute_past_steps()
        if quota_remaining == 0:
            return "⚠ reroute 名额已用完, 不允许再切 Skill, 请继续或收尾。"
        if past_steps_count < min_steps:
            return f"⚠ 证据不足 (past_steps={past_steps_count} < {min_steps}), 还不允许 reroute。"
        return f"可以 reroute (剩余 {quota_remaining} 次), 但仅限当前 Skill 方向明确不成立时。"

    def format_past_steps(self, past_steps: list[tuple[str, str]]) -> str:
        if not past_steps:
            return "(暂无已完成的步骤)"
        limit = self.replanner_past_step_chars()
        lines = []
        for i, (step, result) in enumerate(past_steps, 1):
            body = result if len(result) <= limit else result[:limit] + f"\n[... 已截断, 原长 {len(result)} 字符]"
            lines.append(f"## 步骤 {i}: {step}\n{body}")
        return "\n\n".join(lines)

    def evaluate_replanner_pre_llm(self, state: Mapping[str, Any]) -> HarnessDecision:
        return evaluate_pre_llm(
            state,
            max_steps=self.max_agent_steps(),
            fast_path_threshold=settings.agent_replanner_fast_path_threshold,
        )

    # ==================== Report ====================
    def build_report_messages(
        self,
        *,
        user_input: str,
        past_steps: list[tuple[str, str]],
        current_time: str,
        draft: str,
    ) -> list[dict[str, str]]:
        return [
            {"role": "system", "content": fast_prompts.REPORT_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": fast_prompts.REPORT_USER_TEMPLATE.format(
                    user_input=user_input,
                    past_steps_text=self.format_past_steps(past_steps),
                    draft=draft or "(Replanner 未提供草稿)",
                    current_time=current_time,
                ),
            },
        ]

    # ==================== RAG Chat ====================
    def rag_system_prompt(self, *, tools_enabled: bool) -> str:
        return rag_prompts.RAG_SYSTEM_PROMPT + (
            rag_prompts.RAG_TOOL_APPENDIX if tools_enabled else ""
        )

    def build_rag_user_prompt(
        self,
        *,
        summary: str,
        diagnosis_context: str,
        context: str,
        web_context: str,
        question: str,
    ) -> str:
        return rag_prompts.RAG_USER_TEMPLATE.format(
            summary=summary,
            diagnosis_context=diagnosis_context,
            context=context,
            web_context=web_context,
            question=question,
        )

    # ==================== 降级 ====================
    def classify_error(self, exc: BaseException) -> ErrorKind:
        return classify_error(exc)

    def rag_fallback(self, *, stage: str, exc: BaseException) -> dict[str, Any]:
        """知识库不可用时的占位上下文 (RAG Chat 仍然要能回答)."""
        kind = classify_error(exc)
        detail = f"{type(exc).__name__}: {exc}"
        return {
            "context": "(知识库检索暂不可用，已降级为无知识库上下文回答。请基于已有会话、实时工具或通用运维知识回答，并明确说明知识库不可用。)",
            "sources": [],
            "hits_meta": [],
            "event_data": {
                "degraded": True,
                "stage": stage,
                "error_kind": kind,
                "error_type": type(exc).__name__,
                "error": detail[:500],
                "fallback": "no_rag_context",
            },
        }

    def web_fallback(self, *, stage: str, exc: BaseException) -> dict[str, Any]:
        """联网补充失败时跳过联网上下文."""
        kind = classify_error(exc)
        detail = f"{type(exc).__name__}: {exc}"
        return {
            "context": "(联网补充暂不可用，已跳过联网上下文。)",
            "sources": [],
            "hits": [],
            "skip_reason": f"{type(exc).__name__}: 联网补充失败",
            "event_data": {
                "degraded": True,
                "stage": stage,
                "error_kind": kind,
                "error_type": type(exc).__name__,
                "error": detail[:500],
                "fallback": "skip_web_context",
            },
        }


_agent_harness = AgentHarness()


def get_agent_harness() -> AgentHarness:
    return _agent_harness