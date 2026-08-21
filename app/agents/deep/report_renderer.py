"""Deep Diagnosis 最终报告的 Markdown 渲染 (纯函数, 无 LLM 无 IO).

从 ``nodes/report.py`` 搬出来: report 节点是 deep 图里唯一没有 LLM 调用的节点,
它整个就是"把结构化结论排版成文本"。渲染器独立后, 报告格式调整不再混在图节点里。

章节顺序是前端和审计的阅读约定, 保持稳定; Evidence 引用使用 ``ev_i``,
与 RCAJudge 的 prompt 和审计映射一致。
"""

from __future__ import annotations

from typing import Any

from app.agents.deep.nodes.evidence_reducer import evidence_ref


def is_error_evidence(evidence: dict[str, Any]) -> bool:
    """失败标记的判定口径: metadata.error_type 或 content.error 任一存在.

    evidence_reducer._is_error_evidence 是同一个口径的最早实现;
    渲染侧的判定集中在这里, 不再内联。
    """
    return bool(
        (evidence.get("metadata") or {}).get("error_type")
        or (evidence.get("content") or {}).get("error")
    )


def format_evidence(index: int, evidence: dict[str, Any]) -> str:
    """渲染一条 Evidence 链路，并保留失败标记。"""
    agent = (evidence.get("metadata") or {}).get("agent") or "-"
    marker = " **[ERROR]**" if is_error_evidence(evidence) else ""
    summary = str(evidence.get("summary") or "").strip().replace("\n", " ")
    return (
        f"- `[{evidence_ref(index)}]`{marker} "
        f"`{evidence.get('source', '')}/{evidence.get('type', '')}` "
        f"by `{agent}` — {summary[:240]}"
    )


def format_candidate(rank: int, candidate: dict[str, Any]) -> str:
    """渲染一个已排序候选根因。"""
    return (
        f"{rank}. `{candidate.get('type', '')}` "
        f"(score={candidate.get('support_score', 0):.2f}, "
        f"by {candidate.get('agent', '-')}, refs={candidate.get('evidence_ids', [])}): "
        f"{candidate.get('candidate', '')[:200]}"
    )


def format_remediation(remediation: dict[str, Any]) -> str:
    """渲染处置建议；写操作提示必须保留在最终报告中。"""
    if not remediation:
        return "_(RemediationPlanner 未运行或未产建议)_"
    steps = remediation.get("steps") or []
    need_human = remediation.get("requires_human_confirm", True)
    head = "⚠️ **以下处置含写操作, 需人工确认后执行**\n" if need_human else ""
    if not steps:
        return head + "_(暂无具体处置步骤)_"
    return head + "\n".join(
        f"{index + 1}. {step}" for index, step in enumerate(steps)
    )


def render_report(
    *,
    incident_text: str,
    task_id: str,
    incident_group_id: str,
    alert_signature: str,
    rca: dict[str, Any],
    candidates: list[dict[str, Any]],
    evidences: list[dict[str, Any]],
    remediation: dict[str, Any],
) -> tuple[str, list[str], list[str]]:
    """把 deep 图的全部结构化结论渲染成一份 Markdown 报告.

    返回:
        (报告文本, 产证成功的 agent 名单, 产证失败的 agent 名单)
    """
    # 统计哪些专业 Agent 产证成功/失败，作为报告元数据输出。
    agents_failed: list[str] = []
    agents_ok: list[str] = []
    for evidence in evidences:
        agent = (evidence.get("metadata") or {}).get("agent") or ""
        if not agent or agent == "rca_judge":
            continue
        target = agents_failed if is_error_evidence(evidence) else agents_ok
        if agent not in target:
            target.append(agent)

    via = str(rca.get("via") or "")
    confidence = float(rca.get("confidence") or 0.0)
    root_cause = str(rca.get("root_cause") or "(未判定)")
    reasoning = str(rca.get("reasoning") or "_(无判定理由)_")

    parts = [
        "# 深度诊断报告 (Deep Diagnosis Report)",
        "",
        "## 现象",
        incident_text or "_(未提供现象描述)_",
        "",
        "## 根因判定",
        f"- **最可能根因**: {root_cause}",
        f"- **置信度**: {confidence:.2f}",
        f"- **判定来源**: `{via or 'unknown'}`",
    ]
    supporting = rca.get("supporting_evidence_ids") or []
    if supporting:
        refs = ", ".join(f"`{item}`" for item in supporting)
        parts.append(f"- **关键支持证据**: {refs}")
    parts.extend(["", "**判定理由**: " + reasoning, ""])

    if candidates:
        # 候选已经由 EvidenceReducer 排序，这里只负责展示，不重新排序。
        parts.append("## 候选根因 (按可能性排序)")
        parts.extend(
            format_candidate(index + 1, candidate)
            for index, candidate in enumerate(candidates)
        )
        parts.append("")

    if evidences:
        parts.append(f"## 证据链 (共 {len(evidences)} 条)")
        parts.extend(
            format_evidence(index, evidence)
            for index, evidence in enumerate(evidences)
        )
        parts.append("")

    parts.extend(
        [
            "## 处置建议",
            format_remediation(remediation),
            "",
            "---",
            "### 元数据",
            f"- task_id: `{task_id or '-'}`",
            f"- incident_group_id: `{incident_group_id or '-'}`",
            f"- alert_signature: `{alert_signature or '-'}`",
            f"- 产证成功 Agent: "
            f"{', '.join(f'`{agent}`' for agent in agents_ok) or '_(无)_'}",
        ]
    )
    if agents_failed:
        failed = ", ".join(f"`{agent}`" for agent in agents_failed)
        parts.append(f"- 产证失败 Agent: {failed} ⚠️")
    parts.append("- 诊断模式: `deep` (独立诊断图)")

    return "\n".join(parts), agents_ok, agents_failed
