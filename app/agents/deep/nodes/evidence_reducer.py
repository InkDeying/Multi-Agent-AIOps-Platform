"""EvidenceReducer：把专业 Agent 的 Evidence 归并为候选根因。

第一版坚持确定性评分，不在归并阶段增加 LLM 不确定性。error Evidence
不参与根因候选评分，但仍保留引用，供 RCAJudge 和最终报告说明取证缺口。
"""

from typing import Any

from loguru import logger

from app.agents.deep.state import DeepDiagnosisState
from app.harness.runtime.transitions import DEEP_EVIDENCE_REDUCED, make_transition


# 现场指标信息密度最高；运行环境其次；知识检索和历史经验作为辅助。
_EVIDENCE_BASE_SCORE: dict[str, float] = {
    "metric_snapshot": 1.0,
    "infra_snapshot": 0.90,
    "log_excerpt": 0.85,
    "runbook_match": 0.75,
    "incident_history": 0.60,
    "_default": 0.50,
}
# 限制候选文本和候选数量，避免下游 RCA prompt 无界增长。
_CANDIDATE_TEXT_LIMIT = 240
_CANDIDATES_TOP_K = 5


def _is_error_evidence(evidence: dict[str, Any]) -> bool:
    """判断专业 Agent 是否以 error metadata/content 返回。"""
    metadata = evidence.get("metadata") or {}
    if metadata.get("error_type"):
        return True
    content = evidence.get("content") or {}
    return bool(content.get("error"))


def _score_evidence(evidence: dict[str, Any]) -> float:
    """按 Evidence type 评分；失败证据归零但不从证据链删除。"""
    if _is_error_evidence(evidence):
        return 0.0
    evidence_type = str(evidence.get("type") or "")
    return _EVIDENCE_BASE_SCORE.get(
        evidence_type, _EVIDENCE_BASE_SCORE["_default"]
    )


def evidence_ref(index: int) -> str:
    """Return the in-memory evidence reference used throughout the deep graph."""
    return f"ev_{index}"


async def evidence_reducer_node(state: DeepDiagnosisState) -> DeepDiagnosisState:
    """归并 Evidence，生成稳定排序的候选根因列表。

    每条非 error Evidence 产生一个候选，排序键为 score 降序和 type 字典序，
    这样相同输入下结果稳定，便于审计、测试和下游引用。
    """
    evidences = state.get("evidences") or []
    if not evidences:
        logger.warning("[deep] EvidenceReducer: 无 Evidence, 产空候选")
        return {
            "candidates": [],
            "transition_history": [
                make_transition(
                    "evidence_reducer", DEEP_EVIDENCE_REDUCED, "no evidence"
                )
            ],
        }

    # 先为每条 Evidence 分配稳定的内存引用，再统一计算失败引用。
    scored: list[tuple[int, float, dict[str, Any]]] = []
    error_refs: list[str] = []
    for index, evidence in enumerate(evidences):
        score = _score_evidence(evidence)
        if _is_error_evidence(evidence):
            error_refs.append(evidence_ref(index))
        scored.append((index, score, evidence))

    # error_refs 会附加到正常候选，提醒 RCAJudge 哪些数据源缺失。
    candidates_all: list[dict[str, Any]] = []
    for index, score, evidence in scored:
        if score <= 0:
            continue
        summary = str(evidence.get("summary") or "").strip()
        if not summary:
            continue
        candidate = {
            "candidate": summary[:_CANDIDATE_TEXT_LIMIT],
            "support_score": round(score, 3),
            "evidence_ids": [evidence_ref(index), *error_refs],
            "source": str(evidence.get("source") or ""),
            "type": str(evidence.get("type") or ""),
        }
        agent = (evidence.get("metadata") or {}).get("agent")
        if agent:
            candidate["agent"] = agent
        candidates_all.append(candidate)

    # score 优先，type 作为稳定的第二排序键。
    candidates_all.sort(key=lambda item: (-item["support_score"], item["type"]))
    candidates = candidates_all[:_CANDIDATES_TOP_K]
    logger.info(
        f"[deep] EvidenceReducer: 收到 {len(evidences)} 条 Evidence "
        f"(error={len(error_refs)}), 得 {len(candidates)} 候选"
    )
    return {
        "candidates": candidates,
        "transition_history": [
            make_transition(
                "evidence_reducer",
                DEEP_EVIDENCE_REDUCED,
                f"evidences={len(evidences)} errors={len(error_refs)} "
                f"candidates={len(candidates)}",
            )
        ],
    }
