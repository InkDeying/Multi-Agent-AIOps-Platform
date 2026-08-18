"""Deep Diagnosis Graph。

该图独立于 fast 的 Plan-Execute-Replan 链路。它负责把一次告警拆成事件上下文、
证据计划、专业 Agent 取证、证据归并、RCA 判断、处置建议和最终报告。

图结构:

    [START]
       │
       ▼
  IncidentManager      载入诊断对象 (task/incident)
       │
       ▼
  CorrelationContext   聚合同组告警、相邻事件和 Wiki 经验
       │
       ▼
  EvidencePlan         识别故障域 -> 决定派哪几个专业 subagent + 取证策略
       │
   ┌───┼───────┬───────────┐         ← fan-out (并行)
   ▼   ▼       ▼           ▼
 Log Metric  Infra      Runbook      各跑自己的隔离最小循环, 只回 Evidence (s06 subagent)
   └───┴───┬───┴───────────┘         ← fan-in (LangGraph 多入边 = join barrier)
           ▼
  EvidenceReducer      归并去重 -> 候选根因 + 证据路径
           ▼
  RCAJudge             只看结构化证据排序定根因
           ▼
  RemediationPlanner   出处置建议 (写操作 requires_human_confirm=True)
           ▼
  ReportAgent          报告, 结论引用 evidence_id; 填 response 触发 [END]

专业 Agent 是隔离的一次性取证节点，只把 Evidence 写回共享 state；中间推理不进入
共享上下文。
"""

from langgraph.graph import END, START, StateGraph
from loguru import logger

from app.agents.deep.nodes.correlation_context import correlation_context_node
from app.agents.deep.nodes.evidence_plan import evidence_plan_node
from app.agents.deep.nodes.evidence_reducer import evidence_reducer_node
from app.agents.deep.nodes.incident_manager import incident_manager_node
from app.agents.deep.nodes.rca_judge import rca_judge_node
from app.agents.deep.nodes.remediation_planner import remediation_planner_node
from app.agents.deep.nodes.report import report_node
from app.agents.deep.nodes.specialist_dispatch import (
    SPECIALISTS,
    resolve_specialist_node,
)
from app.agents.deep.state import DeepDiagnosisState


def build_deep_graph():
    """Build the incident-to-evidence-to-report deep diagnosis graph.

    图的 fan-out 在编译期固定为四个专业 Agent。EvidencePlan 不修改图结构，
    而是由 specialist dispatch guard 跳过未被派遣的节点；这样既保留固定拓扑，
    又避免未命中的专业 Agent 调用 LLM。
    """
    workflow = StateGraph(DeepDiagnosisState)

    # 串行前段：先补齐任务事实、关联上下文，再决定取证计划。
    workflow.add_node("incident_manager", incident_manager_node)
    workflow.add_node("correlation_context", correlation_context_node)
    workflow.add_node("evidence_plan", evidence_plan_node)
    for name, source, evidence_type in SPECIALISTS:
        workflow.add_node(
            name, resolve_specialist_node(name, source, evidence_type)
        )
    # 归并及后处理节点只接收结构化 Evidence，不直接读取专业 Agent 的对话。
    workflow.add_node("evidence_reducer", evidence_reducer_node)
    workflow.add_node("rca_judge", rca_judge_node)
    workflow.add_node("remediation_planner", remediation_planner_node)
    workflow.add_node("report", report_node)

    # 串行前段。
    workflow.add_edge(START, "incident_manager")
    workflow.add_edge("incident_manager", "correlation_context")
    workflow.add_edge("correlation_context", "evidence_plan")
    # fan-out / fan-in：多条入边让 LangGraph 在 reducer 前形成 join barrier。
    for name, _, _ in SPECIALISTS:
        workflow.add_edge("evidence_plan", name)
        workflow.add_edge(name, "evidence_reducer")
    # 串行后段：候选根因 -> 判定 -> 建议 -> 报告。
    workflow.add_edge("evidence_reducer", "rca_judge")
    workflow.add_edge("rca_judge", "remediation_planner")
    workflow.add_edge("remediation_planner", "report")
    workflow.add_edge("report", END)

    compiled = workflow.compile()
    logger.info(
        "[deep] Deep Diagnosis graph 已编译: "
        f"IncidentManager->CorrelationContext->EvidencePlan->"
        f"[{len(SPECIALISTS)} 专业 Agent 并行]"
        "->EvidenceReducer->RCAJudge->RemediationPlanner->Report"
    )
    return compiled
