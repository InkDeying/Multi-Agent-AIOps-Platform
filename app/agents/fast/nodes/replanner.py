"""Replanner 节点: 评估进度, 决定继续 or 出报告 or 换 Skill.

三层防死循环:
  1. Prompt 层: "尽快收尾, 控制在 6 步以内"
  2. Harness 层: 步数用尽 / 原地打转 / 计划还够长时, 不调 LLM 直接定
     (见 harness/runtime/replan_policy.py)
  3. 兜底: LLM 返回空 plan 或结构化输出失败, 也强制生成报告

这个文件只留控制流。报告怎么写在 ``fast/report_synthesis.py``,
reroute 允不允许在 ``fast/reroute_policy.py``。
"""

from loguru import logger

from app.agents.fast.report_synthesis import (
    current_report_time,
    ensure_report_time,
    force_summary,
    synthesize_final_report,
)
from app.agents.fast.reroute_policy import build_skill_context, validate_reroute
from app.agents.fast.state import Act, PlanExecuteState, TriedSkill
from app.harness.core.llm import get_chat_llm
from app.harness.core.structured import ainvoke_structured
from app.harness.runtime.agent_harness import get_agent_harness
from app.harness.runtime.transitions import (
    REPLANNER_CONTINUE,
    REPLANNER_FINISHED_EMPTY,
    REPLANNER_FINISHED_OK,
    REPLANNER_LLM_FAILED,
    REPLANNER_MAX_STEPS_FORCE,
    REPLANNER_NOT_FINISHED_EMPTY,
    REPLANNER_REROUTE,
    REPLANNER_REROUTE_BLOCKED,
    make_transition,
)


async def replan_node(state: PlanExecuteState) -> PlanExecuteState:
    """Replanner 节点: 决定继续执行下一步, 或终止并给出报告."""
    user_input = state.get("input", "")
    plan = state.get("plan", [])
    past_steps = state.get("past_steps", [])
    iteration = state.get("iteration", 0)
    selected_skill = state.get("selected_skill", "")
    tried_skills = state.get("tried_skills", [])
    reroute_count = state.get("reroute_count", 0)
    current_time = current_report_time()

    logger.info(
        f"[Replanner] 评估进度: 已执行 {len(past_steps)} 步, "
        f"iteration={iteration}, 剩余 {len(plan) - 1 if plan else 0} 步, "
        f"reroute_count={reroute_count}"
    )

    harness_decision = get_agent_harness().evaluate_replanner_pre_llm(state)
    if harness_decision.action == "continue_fast_path":
        next_plan = list(harness_decision.data.get("next_plan") or [])
        logger.info(
            f"[Replanner] Harness 快路径: {harness_decision.reason}, 剩余 {len(next_plan)} 步"
        )
        return {
            "plan": next_plan,
            "transition_history": [
                make_transition(
                    "replanner", REPLANNER_CONTINUE,
                    f"harness:{harness_decision.reason}: 剩余 {len(next_plan)} 步",
                ),
            ],
        }
    if harness_decision.action == "force_report":
        reason = harness_decision.reason
        transition_reason = (
            REPLANNER_MAX_STEPS_FORCE if reason == "max_steps_reached" else "harness_force_report"
        )
        logger.warning(f"[Replanner] Harness 强制收敛: {reason}")
        return {
            "response": force_summary(user_input, past_steps, current_time),
            "plan": [],
            "transition_history": [
                make_transition("replanner", transition_reason, str(harness_decision.data)),
            ],
        }

    # ===== 准备 reroute 相关上下文 =====
    current_skill_line, candidate_skills_text, tried_skills_text = build_skill_context(
        selected_skill, tried_skills
    )

    harness = get_agent_harness()
    reroute_quota_hint = harness.build_reroute_quota_hint(
        reroute_count=reroute_count,
        past_steps_count=len(past_steps),
    )

    replanner_model = harness.replanner_model()
    llm = get_chat_llm(model=replanner_model, temperature=0, timeout=30, max_retries=1)

    plan_text = "\n".join(f"  {i+1}. {s}" for i, s in enumerate(plan)) if plan else "(无)"
    past_text = harness.format_past_steps(past_steps)

    messages = harness.build_replanner_messages(
        user_input=user_input,
        current_time=current_time,
        current_skill_line=current_skill_line,
        candidate_skills_text=candidate_skills_text,
        tried_skills_text=tried_skills_text,
        reroute_count=reroute_count,
        reroute_quota_hint=reroute_quota_hint,
        plan_text=plan_text,
        past_steps_text=past_text,
    )

    try:
        act = await ainvoke_structured(
            llm=llm,
            schema_cls=Act,
            messages=messages,
            model_name=replanner_model,
        )
    except Exception as e:
        detail = f"{type(e).__name__}: {e}"
        logger.exception(f"[Replanner] 结构化输出失败, 兜底生成报告: {e}")
        logger.warning(f"[transition] node=replanner reason={REPLANNER_LLM_FAILED} detail={detail}")
        return {
            "response": force_summary(user_input, past_steps, current_time),
            "plan": [],
            "transition_history": [
                make_transition("replanner", REPLANNER_LLM_FAILED, detail),
            ],
        }

    # ===== Reroute: LLM 提议切换 Skill =====
    if act.should_reroute:
        allowed, deny_reason = validate_reroute(state, act)
        if allowed:
            new_skill = act.new_skill
            new_reason = act.reroute_reason or "(LLM 未给出原因)"
            tried_entry: TriedSkill = {
                "skill": selected_skill,
                "reason": new_reason,
            }
            logger.info(
                f"[Replanner] 决策: Skill reroute {selected_skill} -> {new_skill}, 原因: {new_reason}"
            )
            logger.info(
                f"[transition] node=replanner reason={REPLANNER_REROUTE} "
                f"from={selected_skill} to={new_skill}"
            )
            return {
                "selected_skill": new_skill,
                "skill_reason": f"reroute: {new_reason}",
                "plan": [],  # 清空旧计划, 回 Planner 重新生成
                "reroute_count": reroute_count + 1,
                "tried_skills": [tried_entry],  # operator.add 追加
                "pending_reroute": True,        # 让 graph 条件边路由回 planner
                "transition_history": [
                    make_transition(
                        "replanner",
                        REPLANNER_REROUTE,
                        f"{selected_skill} -> {new_skill}: {new_reason}",
                    ),
                ],
            }
        # 不合法: 记一条 BLOCKED transition, 继续按 is_finished/plan 逻辑走
        logger.warning(
            f"[Replanner] reroute 被拒: {deny_reason} (LLM 提议 new_skill={act.new_skill!r})"
        )
        logger.warning(f"[transition] node=replanner reason={REPLANNER_REROUTE_BLOCKED} detail={deny_reason}")
        # blocked 记录会与下面的正常出口 transition 一起串联追加 (operator.add)
        blocked_transition = make_transition(
            "replanner",
            REPLANNER_REROUTE_BLOCKED,
            f"new_skill={act.new_skill!r} 被拒: {deny_reason}",
        )
    else:
        blocked_transition = None

    # ===== 终止: LLM 决定生成报告 =====
    if act.is_finished:
        draft = act.response.strip() if act.response else ""
        # 用 report_model (默认 pro) 基于 past_steps 重写一份高质量报告.
        # draft 作为参考, 但 pro 会自己重新组织 5 段结构.
        report = await synthesize_final_report(
            user_input=user_input,
            past_steps=past_steps,
            current_time=current_time,
            draft=draft,
        )
        if not report:
            logger.warning("[Replanner] pro 合成为空且 draft 为空, 走 _force_summary 兜底")
            logger.warning(f"[transition] node=replanner reason={REPLANNER_FINISHED_EMPTY}")
            report = force_summary(user_input, past_steps, current_time)
            report = ensure_report_time(report, current_time)
            return {
                "response": report,
                "plan": [],
                "transition_history": [
                    make_transition("replanner", REPLANNER_FINISHED_EMPTY, "is_finished=True 但 response 为空"),
                ],
            }
        report = ensure_report_time(report, current_time)
        logger.info(f"[Replanner] 决策: 生成最终报告 (len={len(report)})")
        finished_transition = make_transition(
            "replanner", REPLANNER_FINISHED_OK, f"report_len={len(report)}"
        )
        history = [blocked_transition, finished_transition] if blocked_transition else [finished_transition]
        return {
            "response": report,
            "plan": [],
            "transition_history": history,
        }

    # ===== 继续: LLM 给出新计划 =====
    new_plan = [s for s in (act.plan or []) if s.strip()]
    if not new_plan:
        logger.warning("[Replanner] 标记 not finished 但新计划为空, 兜底生成报告")
        logger.warning(f"[transition] node=replanner reason={REPLANNER_NOT_FINISHED_EMPTY}")
        return {
            "response": force_summary(user_input, past_steps, current_time),
            "plan": [],
            "transition_history": [
                make_transition("replanner", REPLANNER_NOT_FINISHED_EMPTY, "is_finished=False 但 plan 为空"),
            ],
        }

    logger.info(f"[Replanner] 决策: 继续执行 {len(new_plan)} 步")
    for i, step in enumerate(new_plan, 1):
        logger.info(f"  剩余步骤 {i}: {step}")
    continue_transition = make_transition(
        "replanner", REPLANNER_CONTINUE, f"剩余 {len(new_plan)} 步"
    )
    history = [blocked_transition, continue_transition] if blocked_transition else [continue_transition]
    return {
        "plan": new_plan,
        "transition_history": history,
    }
