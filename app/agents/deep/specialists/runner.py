"""专业 Agent 的共享执行体.

四个 Agent 的执行流程原先一字不差地写了四遍。共同契约:
  - 一次性、隔离上下文, 自己跑最小 LLM+工具循环 (run_parallel_agent);
  - 不读共享 state 的中间过程, 只读 scoped 输入 (input + task 元信息);
  - 中间推理 (内部 messages[]) 不进共享 state, 只把结论压成一条 Evidence 返回;
  - 失败必降级 (返回带 error metadata 的占位 Evidence), 不抛, 不拖垮 deep graph。

TODO (四个 Agent 共有): 接入 PermissionMode。现在 ``decisions=None`` 走
run_parallel_agent 的兼容路径; 当前工具集合都是已登记的 read-only 工具, 骨架阶段安全。
"""

from __future__ import annotations

from loguru import logger

from app.agents.deep.specialists.spec import SpecialistSpec
from app.agents.deep.state import DeepDiagnosisState
from app.harness.runtime.transitions import DEEP_AGENT_DONE, make_transition


async def run_specialist(
    spec: SpecialistSpec, state: DeepDiagnosisState
) -> DeepDiagnosisState:
    """跑一个专业 Agent, 输出 1 条 Evidence + 1 条 transition.

    与 PlanExecuteState 完全解耦, 不读 plan/past_steps; 只读 input 作为现象描述。
    """
    incident_text = state.get("input") or ""
    task_id = state.get("task_id") or ""

    try:
        # 延迟 import 全放 try 内: langchain/llm/RAG 任意缺失都直接走降级路径,
        # 而不是把异常抛回 LangGraph 顶层导致整图崩。
        from app.harness.core.llm import get_chat_llm
        from app.harness.runtime.agent_harness import get_agent_harness
        from app.harness.runtime.tool_runner import run_parallel_agent

        harness = get_agent_harness()
        llm = get_chat_llm(
            model=harness.executor_model(),  # 复用 executor 模型档位 (推荐 flash, 便宜快)
            temperature=0,
            streaming=False,  # subagent 内部循环, 不直接面向 SSE
        )
        result = await run_parallel_agent(
            llm=llm,
            tools=spec.load_tools(),
            system_prompt=spec.system_prompt,
            inputs={"messages": [("user", spec.build_user_prompt(incident_text))]},
            max_iters=spec.max_iters,
            max_parallel=spec.max_parallel,
            decisions=None,
        )
        summary, tool_calls = spec.summarize_messages(result.get("messages") or [])
        logger.info(
            f"[deep] {spec.name}: tools={len(tool_calls)} summary={summary[:80]!r}"
        )
        evidence = spec.build_evidence(
            summary,
            content={"tool_calls": tool_calls, "task_id": task_id},
            tool_call_count=len(tool_calls),
        )
        return {
            "evidences": [evidence],
            "transition_history": [
                make_transition(spec.name, DEEP_AGENT_DONE, f"tools={len(tool_calls)}")
            ],
        }
    except Exception as exc:
        # 降级: 不抛, 让 deep graph 走完; Evidence 标错误, 供 RCAJudge 识别。
        logger.exception(f"[deep] {spec.name} failed: {exc}")
        evidence = spec.build_evidence(
            summary=f"{spec.name} 执行失败: {type(exc).__name__}: {exc}",
            content={"error": True, "task_id": task_id},
            tool_call_count=0,
            error=type(exc).__name__,
        )
        return {
            "evidences": [evidence],
            "transition_history": [
                make_transition(
                    spec.name, DEEP_AGENT_DONE, f"error: {type(exc).__name__}"
                )
            ],
        }
