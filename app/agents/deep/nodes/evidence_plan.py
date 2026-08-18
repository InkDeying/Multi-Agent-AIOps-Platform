"""EvidencePlan：用确定性规则选择 deep 专业 Agent。

当前不在路由阶段调用 LLM。LangGraph 的 fan-out 在编译期固定，
EvidencePlan 只输出计划，真正的跳过逻辑由 specialist dispatch guard 执行。
"""

import re

from loguru import logger

from app.agents.deep.nodes.specialist_dispatch import SPECIALIST_NAMES
from app.agents.deep.state import DeepDiagnosisState
from app.harness.runtime.transitions import DEEP_EVIDENCE_PLANNED, make_transition


# 故障域关键词 -> 建议派遣的 Agent；命中多个域时取并集。
_PLAN_KEYWORDS: tuple[tuple[list[str], tuple[str, ...]], ...] = (
    (
        "cpu memory mem disk io load process 进程 内存 磁盘 负载 资源 cpu使用 卡顿 发热".split(),
        ("metric_agent",),
    ),
    (
        "log 日志 error exception 报错 错误 异常 告警 alert 5xx 4xx 失败 traceback".split(),
        ("log_agent",),
    ),
    (
        "docker container 容器 端口 port dns http 网络 network 依赖 服务不可达 "
        "connection refused timeout 超时 latency 慢请求 trace span 调用链 链路".split(),
        ("infra_agent", "log_agent"),
    ),
    (
        "sop runbook 手册 流程 规范 步骤 怎么处理 如何排查".split(),
        ("runbook_agent",),
    ),
)
# 强信号要求四个专业 Agent 全部取证。
_PLAN_BROADCAST_HINTS = (
    "全面诊断",
    "深度排查",
    "群聊",
    "全 agent",
    "全部 agent",
    "all agent",
    "broadcast",
)
# 未命中域时使用信息密度最高的 metric + log 组合。
_PLAN_DEFAULT_AGENTS = ("metric_agent", "log_agent")


def _route_by_keywords(text: str) -> tuple[list[str], str]:
    """把现象文本映射为 ``(agents, strategy)``，供 transition 观测。"""
    norm = (text or "").lower()

    def hit(keyword: str) -> bool:
        if keyword.isascii() and keyword.replace("_", "").replace("-", "").isalnum():
            return (
                re.search(
                    rf"(?<![a-z0-9_-]){re.escape(keyword)}(?![a-z0-9_-])", norm
                )
                is not None
            )
        return keyword in norm

    if not norm.strip():
        # 空输入也保持可执行的默认取证计划。
        return list(_PLAN_DEFAULT_AGENTS), "default_empty_input"
    if any(hint in norm for hint in _PLAN_BROADCAST_HINTS):
        # 广播策略优先于普通域规则，确保用户明确要求全面诊断时不漏 Agent。
        return list(SPECIALIST_NAMES), "broadcast"

    matched_agents: list[str] = []
    for words, agents in _PLAN_KEYWORDS:
        if any(hit(word) for word in words):
            for agent in agents:
                if agent not in matched_agents:
                    matched_agents.append(agent)
    if not matched_agents:
        return list(_PLAN_DEFAULT_AGENTS), "default_no_match"
    return matched_agents, "keyword_match"


def evidence_plan_node(state: DeepDiagnosisState) -> DeepDiagnosisState:
    """根据故障现象生成专业 Agent 取证计划，不修改图拓扑。"""
    agents, strategy = _route_by_keywords(state.get("input") or "")
    plan = {"agents": agents, "strategy": strategy}
    logger.info(f"[deep] EvidencePlan: strategy={strategy} -> agents={agents}")
    return {
        "evidence_plan": plan,
        "transition_history": [
            make_transition(
                "evidence_plan",
                DEEP_EVIDENCE_PLANNED,
                f"strategy={strategy} agents=[{','.join(agents)}]",
            )
        ],
    }
