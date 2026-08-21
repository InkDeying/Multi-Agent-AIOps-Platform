"""Deep Diagnosis (群聊 / 多 Agent 组诊断) 的共享状态。

与 fast 的 PlanExecuteState 分开: fast 是单 Agent plan-execute-replan, deep 是
"确定性编排图 + 一组隔离的调查型 subagent"。沿用 LangGraph reducer 约定:
  - 普通字段 = 覆盖;
  - Annotated[List, operator.add] = 累加 (并行专业 Agent 并发写 evidences 靠它做并发安全归并)。

注意: 本文件**不要**加 `from __future__ import annotations` —— 否则 Annotated 元数据会被
字符串化, LangGraph 读不到 operator.add reducer。这与 app/agents/fast/state.py 的处理一致。

设计取舍:
  - 专业 Agent 是"一次性、隔离上下文、只回 Evidence(=summary)"的节点,
    不是持久互聊的 Agent。所以这里没有 inbox / request_id / 协议字段,
    只有一个并发安全的 evidences 累加器。
  - 各 Agent 不读彼此的中间推理，只通过 evidences 交换压缩证据。
"""

import operator
from typing import Annotated, Any, Dict, List, TypedDict

from app.harness.runtime.transitions import StateTransition


class DeepDiagnosisState(TypedDict, total=False):
    """群聊深度诊断图的共享状态。"""

    # —— 沿用 fast 的输入/可观测字段 ——
    # runner 会把手动请求、Worker 任务和 SSE 关联信息统一写入这些字段。
    input: str
    diagnosis_mode: str
    requested_diagnosis_mode: str
    alert_signature: str
    transition_history: Annotated[List[StateTransition], operator.add]

    # —— 诊断对象上下文 (IncidentManager 填) ——
    # 手动 SSE 可能没有 task/group；节点必须允许这些字段为空并安全降级。
    incident_group_id: str
    incident_id: str
    task_id: str
    # 编排层预加载的事实和经验；节点不得自行访问 Postgres 或 Wiki 文件。
    task_context: Dict[str, Any]
    task_context_status: str
    task_context_error_type: str
    incident_group_context: Dict[str, Any]
    incident_group_context_status: str
    incident_group_context_error_type: str
    wiki_context: str

    # —— ③ EvidencePlan: 派哪几个专业 Agent + 取证策略 ——
    # 图的四路 fan-out 固定存在，未入选的节点由 dispatch guard 跳过。
    evidence_plan: Dict[str, Any]

    # —— ④ 并行专业 Agent 往这里累加 (operator.add = 并发安全归并) ——
    #    每条 evidence 与 app/evidence/models.EvidenceCreate 对齐:
    #    {source, type, summary, content, score?, metadata?}
    evidences: Annotated[List[Dict[str, Any]], operator.add]

    # —— ④' EvidenceReducer + 未来 RootCauseLocalizer 的产物 ——
    #    [{candidate, paths, support_score, evidence_ids}]
    candidates: List[Dict[str, Any]]

    # —— ⑤ RCAJudge：只消费候选和 Evidence summary ——
    rca: Dict[str, Any]
    # —— ⑥ RemediationPlanner (写操作必须 requires_human_confirm=True) ——
    remediation: Dict[str, Any]
    # —— ⑦ ReportAgent: 填 response 即触发 END ——
    response: str
