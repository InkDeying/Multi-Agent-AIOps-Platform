"""RemediationPlanner：生成处置建议，但不执行任何副作用。

建议分为只读验证和写操作两类；只要结果中存在写操作，就必须标记
``requires_human_confirm=True``，交由人工审批边界处理。
"""

from typing import Any

from loguru import logger

from app.agents.deep.state import DeepDiagnosisState
from app.harness.runtime.transitions import DEEP_REMEDIATION_PLANNED, make_transition


# 具体技术关键词必须排在通用症状之前，避免“慢查询”被 latency 模板截获。
_REMEDIATION_TEMPLATES: tuple[
    tuple[tuple[str, ...], list[str], list[str]], ...
] = (
    (
        ("redis", "缓存"),
        ["复核 Redis 命中率 + 大 key", "查看主从复制延迟"],
        ["清理大 key", "扩容 Redis / 读写分离"],
    ),
    (
        ("mysql", "数据库", "db", "慢查询"),
        ["查慢查询日志", "检查活跃连接数 + 锁等待"],
        ["kill 长事务", "评估读库扩容"],
    ),
    (
        ("cpu", "load", "进程", "process", "占用", "卡顿"),
        ["复核 top CPU 进程是否预期内", "对比历史基线确认是否阈值偏低"],
        ["限流/降级该服务的非关键请求", "评估扩容 worker / 实例"],
    ),
    (
        ("memory", "mem", "内存", "oom"),
        ["列出 top 内存进程 + RSS", "检查是否有内存泄漏迹象 (持续增长)"],
        ["重启占用最高的进程 (业务停机窗口内)", "评估扩容内存或开 swap"],
    ),
    (
        ("disk", "磁盘", "inode", "no space"),
        ["列出大文件 (du -sh)", "检查日志轮转 / 临时文件"],
        ["清理过期日志/临时文件", "扩容磁盘"],
    ),
    (
        ("latency", "timeout", "超时", "慢"),
        ["拉取 P95/P99 趋势确认", "检查依赖服务健康度"],
        ["熔断/降级慢依赖", "扩容并发能力"],
    ),
    (
        ("log", "5xx", "error", "exception", "异常"),
        ["按命中模板的关键字过滤近 1 小时日志样本", "确认是否新版本上线后开始 (变更关联)"],
        ["回滚最近变更", "联系上游服务确认依赖"],
    ),
)


def _match_remediation(
    rca_text: str, candidates: list[dict[str, Any]]
) -> tuple[list[str], list[str], str]:
    """按 RCA 文本和前三个候选匹配确定性处置模板。"""
    combined = (rca_text or "").lower()
    for candidate in candidates[:3]:
        combined += " " + str(candidate.get("candidate") or "").lower()
    for keywords, readonly_steps, write_steps in _REMEDIATION_TEMPLATES:
        if any(keyword in combined for keyword in keywords):
            return readonly_steps, write_steps, ",".join(keywords[:2])
    return (
        ["调取关键 metric 趋势 (CPU/Mem/Latency/QPS) 复核", "查最近 1 小时变更/发布记录"],
        ["如确认影响面, 优先回滚最近变更"],
        "default",
    )


def remediation_planner_node(state: DeepDiagnosisState) -> DeepDiagnosisState:
    """生成只读验证和需人工确认的写操作建议，不直接执行处置。"""
    rca = state.get("rca") or {}
    candidates = state.get("candidates") or []
    readonly_steps, write_steps, matched = _match_remediation(
        str(rca.get("root_cause") or ""), candidates
    )
    # 把步骤类型编码到报告文本中，便于前端和人工审批识别风险。
    steps = [f"[只读] {step}" for step in readonly_steps]
    steps.extend(f"[写操作·需人工] {step}" for step in write_steps)
    remediation = {
        "steps": steps,
        "requires_human_confirm": True,
        "matched_template": matched,
        "based_on_rca_via": str(rca.get("via") or ""),
    }
    logger.info(
        f"[deep] RemediationPlanner: matched={matched} steps={len(steps)} "
        f"(readonly={len(readonly_steps)} writeop={len(write_steps)})"
    )
    return {
        "remediation": remediation,
        "transition_history": [
            make_transition(
                "remediation_planner",
                DEEP_REMEDIATION_PLANNED,
                f"matched={matched} steps={len(steps)}",
            )
        ],
    }
