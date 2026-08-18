"""RCAJudge：只根据候选和 Evidence summary 判定根因。

RCAJudge 不读取工具原文、不调用工具，只做一次 LLM 判断；LLM 不可用或输出
无法解析时，回退到 EvidenceReducer 已排序的第一候选。
"""

import json
from typing import Any

from loguru import logger

from app.agents.deep.state import DeepDiagnosisState
from app.incidents.models import EvidenceSource
from app.harness.runtime.transitions import DEEP_RCA_JUDGED, make_transition


# Prompt 明确禁止读取 content 原文，确保 RCA 的输入边界可审计。
_RCA_SYSTEM_PROMPT = (
    "你是 SRE 根因判定法官 (RCA Judge)。下面给你一组**候选根因** (已按确定性算法初排序) 和"
    "一组**关键证据 summary** (来自多个专业 Agent 的观察结论)。\n"
    "你的职责: ① 对候选**重新排序**, 把最可能的根因排第一; ② 写一段≤200 字的中文判定理由;"
    "③ 列出最关键的 3-5 个支持证据 (按 evidence_id, 取 evidence_ids 字段里的引用)。\n\n"
    "硬性约束:\n"
    "1. **只看本 prompt 给的 summary, 不要假设你看过原始日志/指标/调用链**;\n"
    "2. 优先看 metric 类证据 (现场实测), 次看 infra (运行环境/依赖), 再看 log/runbook 和 incident_history;\n"
    "3. 如果有标记 error 的证据, 说明对应 Agent 失败, 在 reasoning 里点明这部分信息缺失;\n"
    "4. 只输出一个 JSON 对象, 不要任何解释或 markdown 围栏。字段:\n"
    "   {\n"
    '     "root_cause": "<一句话最可能根因>",\n'
    '     "ranked_candidates": ["<按可能性降序的 candidate 文本列表>"],\n'
    '     "supporting_evidence_ids": ["ev_X", ...],\n'
    '     "reasoning": "<判定理由 (中文, ≤200 字)>",\n'
    '     "confidence": <0.0-1.0>\n'
    "   }"
)


def _build_rca_user_prompt(
    candidates: list[dict[str, Any]], evidences: list[dict[str, Any]]
) -> str:
    """只组装候选和 summary，不把原始工具输出带入 RCA prompt。"""
    lines: list[str] = ["候选根因 (确定性初排):"]
    for index, candidate in enumerate(candidates):
        lines.append(
            f"  C{index}: score={candidate.get('support_score', 0):.2f} "
            f"type={candidate.get('type', '')} agent={candidate.get('agent', '-')}\n"
            f"     candidate: {candidate.get('candidate', '')[:200]}\n"
            f"     evidence_ids: {candidate.get('evidence_ids', [])}"
        )
    lines.extend(["", "关键证据 summary (按 ev_i 引用; 不展示 content 原文):"])
    for index, evidence in enumerate(evidences):
        is_error = bool(
            (evidence.get("metadata") or {}).get("error_type")
            or (evidence.get("content") or {}).get("error")
        )
        marker = " [ERROR]" if is_error else ""
        lines.append(
            f"  ev_{index}{marker}: source={evidence.get('source', '')} "
            f"type={evidence.get('type', '')}\n"
            f"     summary: {str(evidence.get('summary') or '')[:200]}"
        )
    lines.extend(["", "请按系统约束输出 JSON。"])
    return "\n".join(lines)


def _parse_rca_json(text: str) -> dict[str, Any]:
    """从可能带 markdown 围栏的 LLM 输出中提取 JSON 对象。"""
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw[:4].lower() == "json":
            raw = raw[4:]
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no json object in rca output")
    parsed = json.loads(raw[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("rca output is not a json object")
    return parsed


def _rca_fallback(
    candidates: list[dict[str, Any]], reason: str
) -> dict[str, Any]:
    """LLM 失败时取 Reducer 第一候选，保持 deep 图可收敛。"""
    top = candidates[0] if candidates else {}
    return {
        "root_cause": str(top.get("candidate") or "(无候选可定)"),
        "ranked_candidates": [item.get("candidate", "") for item in candidates],
        "supporting_evidence_ids": list(top.get("evidence_ids") or []),
        "reasoning": f"(确定性兜底: {reason}; 取 Reducer 评分最高候选)",
        "confidence": float(top.get("support_score") or 0.0),
        "via": "fallback",
    }


def _rca_evidence(rca: dict[str, Any]) -> dict[str, Any]:
    """把 RCA 判定再次压成 Evidence，供 Report 统一引用。"""
    return {
        "source": str(EvidenceSource.RCA),
        "type": "rca",
        "summary": str(rca.get("root_cause") or "")[:500],
        "content": {"rca": rca},
        "metadata": {"agent": "rca_judge", "via": rca.get("via", "")},
    }


async def rca_judge_node(state: DeepDiagnosisState) -> DeepDiagnosisState:
    """使用一次 LLM 判断，任何失败都走确定性 fallback。"""
    candidates = state.get("candidates") or []
    evidences = state.get("evidences") or []
    if not candidates:
        # 所有 Evidence 都失败或没有可用摘要时，仍产出可解释的空 RCA。
        logger.warning("[deep] RCAJudge: 无候选, 跳过")
        rca = {
            "root_cause": "(无候选)",
            "ranked_candidates": [],
            "supporting_evidence_ids": [],
            "reasoning": "EvidenceReducer 未产候选 (可能所有 Evidence 都标 error)",
            "confidence": 0.0,
            "via": "empty",
        }
        return {
            "rca": rca,
            "evidences": [_rca_evidence(rca)],
            "transition_history": [
                make_transition("rca_judge", DEEP_RCA_JUDGED, "no candidates")
            ],
        }

    via = "llm"
    try:
        # report_model 用于判断质量；该节点不创建工具循环。
        from app.harness.core.llm import get_chat_llm
        from app.harness.runtime.agent_harness import get_agent_harness

        harness = get_agent_harness()
        llm = get_chat_llm(
            model=harness.report_model(), temperature=0, streaming=False
        )
        response = await llm.ainvoke(
            [
                ("system", _RCA_SYSTEM_PROMPT),
                ("human", _build_rca_user_prompt(candidates, evidences)),
            ]
        )
        raw = getattr(response, "content", "") or ""
        parsed = _parse_rca_json(raw if isinstance(raw, str) else str(raw))
        rca = {
            "root_cause": str(parsed.get("root_cause") or "")[:500]
            or candidates[0].get("candidate", ""),
            "ranked_candidates": list(
                parsed.get("ranked_candidates")
                or [item.get("candidate", "") for item in candidates]
            ),
            "supporting_evidence_ids": list(
                parsed.get("supporting_evidence_ids")
                or candidates[0].get("evidence_ids")
                or []
            ),
            "reasoning": str(parsed.get("reasoning") or "")[:600],
            "confidence": float(
                parsed.get("confidence")
                or candidates[0].get("support_score")
                or 0.0
            ),
            "via": via,
        }
        logger.info(
            f"[deep] RCAJudge: root_cause={rca['root_cause'][:60]!r} "
            f"conf={rca['confidence']}"
        )
    except Exception as exc:
        # Provider、解析或配置任一失败都不能击穿整张 deep 图。
        logger.exception(f"[deep] RCAJudge LLM failed, fallback: {exc}")
        via = "fallback"
        rca = _rca_fallback(candidates, type(exc).__name__)

    return {
        "rca": rca,
        "evidences": [_rca_evidence(rca)],
        "transition_history": [
            make_transition(
                "rca_judge",
                DEEP_RCA_JUDGED,
                f"via={via} conf={rca['confidence']:.2f}",
            )
        ],
    }
