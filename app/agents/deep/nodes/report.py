"""ReportAgent 节点 —— 渲染 deep 图的最终报告并写入 ``response`` 触发图 END。

报告不再调用 LLM；RCAJudge 已经完成判断，Report 只负责稳定格式化。
排版逻辑在 ``app/agents/deep/report_renderer.py``，本节点只做:
读 state → 渲染 → 打日志 → 返回更新。

``cache_reports`` 等副作用由 orchestration runner 控制，本节点不落库。
"""

from loguru import logger

from app.agents.deep.report_renderer import render_report
from app.agents.deep.state import DeepDiagnosisState
from app.harness.runtime.transitions import DEEP_REPORT_DONE, make_transition


def report_node(state: DeepDiagnosisState) -> DeepDiagnosisState:
    """生成最终 Markdown 报告并写入 ``response`` 触发图 END。"""
    rca = state.get("rca") or {}
    candidates = state.get("candidates") or []
    evidences = state.get("evidences") or []

    response, _agents_ok, agents_failed = render_report(
        incident_text=state.get("input") or "",
        task_id=state.get("task_id") or "",
        incident_group_id=state.get("incident_group_id") or "",
        alert_signature=state.get("alert_signature") or "",
        rca=rca,
        candidates=candidates,
        evidences=evidences,
        remediation=state.get("remediation") or {},
    )
    via = str(rca.get("via") or "")
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
