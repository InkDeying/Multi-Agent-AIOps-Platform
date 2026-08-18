"""ReportAgent：把结构化 RCA、候选、Evidence 和处置建议渲染为 Markdown。

报告不再调用 LLM；RCAJudge 已经完成判断，Report 只负责稳定格式化，
并用 ``ev_i`` 引用当前运行内存中的 Evidence。
"""

from typing import Any

from loguru import logger

from app.agents.deep.nodes.evidence_reducer import evidence_ref
from app.agents.deep.state import DeepDiagnosisState
from app.harness.runtime.transitions import DEEP_REPORT_DONE, make_transition


def _format_evidence(index: int, evidence: dict[str, Any]) -> str:
    """渲染一条 Evidence 链路，并保留失败标记。"""
    agent = (evidence.get("metadata") or {}).get("agent") or "-"
    is_error = bool(
        (evidence.get("metadata") or {}).get("error_type")
        or (evidence.get("content") or {}).get("error")
    )
    marker = " **[ERROR]**" if is_error else ""
    summary = str(evidence.get("summary") or "").strip().replace("\n", " ")
    return (
        f"- `[{evidence_ref(index)}]`{marker} "
        f"`{evidence.get('source', '')}/{evidence.get('type', '')}` "
        f"by `{agent}` — {summary[:240]}"
    )


def _format_candidate(rank: int, candidate: dict[str, Any]) -> str:
    """渲染一个已排序候选根因。"""
    return (
        f"{rank}. `{candidate.get('type', '')}` "
        f"(score={candidate.get('support_score', 0):.2f}, "
        f"by {candidate.get('agent', '-')}, refs={candidate.get('evidence_ids', [])}): "
        f"{candidate.get('candidate', '')[:200]}"
    )


def _format_remediation(remediation: dict[str, Any]) -> str:
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


def report_node(state: DeepDiagnosisState) -> DeepDiagnosisState:
    """生成最终 Markdown 报告并写入 ``response`` 触发图 END。

    ``cache_reports`` 等副作用由 orchestration runner 控制，本节点只负责渲染。
    """
    incident_text = state.get("input") or ""
    task_id = state.get("task_id") or ""
    incident_group_id = state.get("incident_group_id") or ""
    alert_signature = state.get("alert_signature") or ""
    rca = state.get("rca") or {}
    candidates = state.get("candidates") or []
    evidences = state.get("evidences") or []
    remediation = state.get("remediation") or {}

    # 统计哪些专业 Agent 产证成功/失败，作为报告元数据输出。
    agents_failed: list[str] = []
    agents_ok: list[str] = []
    for evidence in evidences:
        agent = (evidence.get("metadata") or {}).get("agent") or ""
        if not agent or agent == "rca_judge":
            continue
        is_error = bool(
            (evidence.get("metadata") or {}).get("error_type")
            or (evidence.get("content") or {}).get("error")
        )
        target = agents_failed if is_error else agents_ok
        if agent not in target:
            target.append(agent)

    via = str(rca.get("via") or "")
    confidence = float(rca.get("confidence") or 0.0)
    root_cause = str(rca.get("root_cause") or "(未判定)")
    reasoning = str(rca.get("reasoning") or "_(无判定理由)_")

    # 报告章节顺序是前端和审计阅读约定，保持稳定。
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
            _format_candidate(index + 1, candidate)
            for index, candidate in enumerate(candidates)
        )
        parts.append("")

    if evidences:
        # Evidence 引用使用 ev_i，与 RCAJudge 的 prompt 和审计映射保持一致。
        parts.append(f"## 证据链 (共 {len(evidences)} 条)")
        parts.extend(
            _format_evidence(index, evidence)
            for index, evidence in enumerate(evidences)
        )
        parts.append("")

    parts.extend(
        [
            "## 处置建议",
            _format_remediation(remediation),
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

    response = "\n".join(parts)
    logger.info(
        f"[deep] ReportAgent: rendered {len(response)} 字, "
        f"evidences={len(evidences)} candidates={len(candidates)} "
        f"rca.via={via} failed_agents={len(agents_failed)}"
    )
    return {
        "response": response,
        "transition_history": [
            make_transition(
                "report",
                DEEP_REPORT_DONE,
                f"len={len(response)} evidences={len(evidences)} via={via}",
            )
        ],
    }
