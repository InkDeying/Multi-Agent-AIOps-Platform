"""四个专业 Agent 的注册表 —— 声明它们各自"哪里不一样".

顺序即 deep graph 的 fan-out 顺序, 也是 EvidencePlan 生成 broadcast 计划的顺序。

各 Agent 的职责边界 (以前分散在四个模块的 docstring 里):

  log_agent —— 日志/告警模式检索
    检索的是日志模板、告警规则和 SOP, **不直接读原始日志后端**。默认公开语料含
    Prometheus 告警规则和 OnCall SOP; 可用 scripts/convert_log_templates.py 额外
    导入 loghub 模板。
    TODO: 接 Loki / Elasticsearch 等真日志后端。

  metric_agent —— 资源指标
    配置了 Prometheus 时优先查真指标, 否则退化为 4 个本机只读指标工具。
    TODO: 在 Prometheus / VictoriaMetrics 之外扩展更多指标后端。

  infra_agent —— 运行环境 / 依赖健康
    与 metric_agent 错位: metric 关心 CPU/内存/磁盘/进程等资源指标, infra 关心容器
    状态、端口可达性、DNS/HTTP 健康。只用只读工具; Docker / Network MCP 未连接时
    退化成本机系统快照, 不影响整图出报告。

  runbook_agent —— 处置流程 / 运维规范
    与 log_agent 错位: 两者共用知识库工具, 但 log 关心"日志模式/告警规则",
    runbook 关心"处置流程/排查步骤", 靠 scoped prompt 保持不重复。
    只摘 SOP, 不做根因判定, 不执行处置命令。

为什么硬编码工具白名单而不走 fast 的 filter_tools_for_skill: Skill 过滤是 fast
Plan-Execute 的概念, 与"专业 Agent 自带工具白名单"是两种范式, 两者相互独立。
"""

from __future__ import annotations

from app.agents.deep.specialists.spec import SpecialistSpec
from app.agents.deep.specialists.tools import (
    load_infra_tools,
    load_knowledge_tools,
    load_metric_tools,
)
from app.harness.prompts.deep import (
    INFRA_SYSTEM_PROMPT,
    LOG_SYSTEM_PROMPT,
    METRIC_SYSTEM_PROMPT,
    RUNBOOK_SYSTEM_PROMPT,
)
from app.incidents.models import EvidenceSource

LOG_SPEC = SpecialistSpec(
    name="log_agent",
    source=EvidenceSource.LOG,
    evidence_type="log_excerpt",
    # RAG 查询 1-2 次够, 比 metric 严格; 只 1 个工具实际无并行
    max_iters=3,
    max_parallel=2,
    load_tools=load_knowledge_tools,
    empty_incident_hint="(未提供现象, 默认检索通用 OnCall 知识)",
    task_instruction=(
        "请按上述约束去知识库检索匹配的日志模板/告警规则/SOP, 输出一段 summary。"
    ),
    system_prompt=LOG_SYSTEM_PROMPT,
)

METRIC_SPEC = SpecialistSpec(
    name="metric_agent",
    source=EvidenceSource.METRIC,
    evidence_type="metric_snapshot",
    # 4 轮足以采完本机 4 个工具; 4 个 read-only 工具同批 gather
    max_iters=4,
    max_parallel=4,
    load_tools=load_metric_tools,
    empty_incident_hint="(未提供现象, 默认采全量本机指标快照)",
    task_instruction="请按上述约束采集本机指标, 找异常, 输出一段 summary。",
    system_prompt=METRIC_SYSTEM_PROMPT,
)

INFRA_SPEC = SpecialistSpec(
    name="infra_agent",
    source=EvidenceSource.MCP_TOOL_RESULT,
    evidence_type="infra_snapshot",
    max_iters=4,
    max_parallel=4,
    load_tools=load_infra_tools,
    empty_incident_hint="(未提供现象, 默认检查本机运行环境和依赖健康)",
    task_instruction="请按上述约束做基础设施/依赖健康取证, 输出一段 summary。",
    system_prompt=INFRA_SYSTEM_PROMPT,
)

RUNBOOK_SPEC = SpecialistSpec(
    name="runbook_agent",
    source=EvidenceSource.RUNBOOK,
    evidence_type="runbook_match",
    max_iters=3,
    max_parallel=2,
    load_tools=load_knowledge_tools,
    empty_incident_hint="(未提供现象, 检索通用 OnCall SOP)",
    task_instruction=(
        "请检索匹配的 SOP/Runbook, 摘要关键处置步骤 (≤400 字, 编号要点)。"
    ),
    system_prompt=RUNBOOK_SYSTEM_PROMPT,
)

# 顺序即 deep graph 的 fan-out 顺序, 不要随意调整。
SPECIALIST_SPECS: tuple[SpecialistSpec, ...] = (
    LOG_SPEC,
    METRIC_SPEC,
    INFRA_SPEC,
    RUNBOOK_SPEC,
)

_BY_NAME = {spec.name: spec for spec in SPECIALIST_SPECS}


def get_spec(name: str) -> SpecialistSpec | None:
    """按节点名取 spec; 未注册的名字返回 None (调用方回退到 stub)."""
    return _BY_NAME.get(name)
