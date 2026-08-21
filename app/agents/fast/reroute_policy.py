"""Skill reroute 的上下文准备与合法性校验.

从 ``nodes/replanner.py`` 搬出来: reroute 是"换一个 Skill 重来"的调度决策,
和 replan (在同一个 Skill 内改计划) 是两件事, 只是恰好由同一个 LLM 调用同时提议。

这里回答两个问题:
  1. 给 Replanner 的 prompt 里, 候选 Skill 菜单和黑名单长什么样;
  2. LLM 提议的 reroute 到底允不允许 —— 六道门槛全过才算。
"""

from __future__ import annotations

from app.agents.fast.state import Act, PlanExecuteState, TriedSkill
from app.harness.runtime.agent_harness import get_agent_harness
from app.harness.skills import get_skill_registry


def build_skill_context(
    selected_skill: str,
    tried_skills: list[TriedSkill],
) -> tuple[str, str, str]:
    """为 Replanner prompt 准备 reroute 相关的上下文文本.

    返回:
        (current_skill_line, candidate_skills_text, tried_skills_text)
    """
    registry = get_skill_registry()
    tried_names = {ts.get("skill", "") for ts in tried_skills}

    current = registry.get(selected_skill) if selected_skill else None
    if current is not None:
        current_skill_line = f"{current.name} — {current.display_name}\n适用场景: {current.description}"
    else:
        current_skill_line = f"{selected_skill or '(未选中)'}"

    # 候选菜单: 排除当前选中 + 已试过的
    excluded = tried_names | ({selected_skill} if selected_skill else set())
    candidates = [s for s in registry.all() if s.name not in excluded]
    if candidates:
        candidate_skills_text = "\n\n".join(s.to_router_card() for s in candidates)
    else:
        candidate_skills_text = "(无可选候选 Skill, 不允许 reroute)"

    if tried_skills:
        tried_skills_text = "\n".join(
            f"- {ts.get('skill', '?')}: {ts.get('reason', '(无原因)')}"
            for ts in tried_skills
        )
    else:
        tried_skills_text = "(无)"

    return (
        current_skill_line,
        candidate_skills_text,
        tried_skills_text,
    )


def validate_reroute(
    state: PlanExecuteState,
    act: Act,
) -> tuple[bool, str]:
    """代码层校验 LLM 提议的 reroute 是否合法.

    门槛 (任一不过都拒绝):
      1. should_reroute=true 且 new_skill 非空
      2. past_steps >= agent_reroute_min_past_steps (证据足够)
      3. reroute_count < agent_max_reroutes (名额未用完)
      4. new_skill 不等于当前 selected_skill (防自循环)
      5. new_skill 不在 tried_skills 黑名单 (防回环)
      6. new_skill 在 SkillRegistry 里真实存在

    返回:
        (allowed, deny_reason)
    """
    if not act.should_reroute or not act.new_skill:
        return False, "LLM 未提议 reroute"

    past_steps = state.get("past_steps", [])
    harness = get_agent_harness()
    if len(past_steps) < harness.min_reroute_past_steps():
        return False, (
            f"past_steps={len(past_steps)} < 门槛 {harness.min_reroute_past_steps()}, 证据不足"
        )

    reroute_count = state.get("reroute_count", 0)
    if reroute_count >= harness.max_reroutes():
        return False, (
            f"reroute_count={reroute_count} 已达上限 {harness.max_reroutes()}"
        )

    selected_skill = state.get("selected_skill", "")
    if act.new_skill == selected_skill:
        return False, f"new_skill ({act.new_skill}) 等于当前 selected_skill"

    tried_names = {ts.get("skill", "") for ts in state.get("tried_skills", [])}
    if act.new_skill in tried_names:
        return False, f"new_skill ({act.new_skill}) 在黑名单里"

    if get_skill_registry().get(act.new_skill) is None:
        return False, f"new_skill ({act.new_skill}) 在 SkillRegistry 中不存在"

    return True, ""
