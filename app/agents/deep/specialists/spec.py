"""专业 Agent 的声明式规格.

Deep Diagnosis 的四个专业 Agent (log / metric / infra / runbook) 原先是四个独立
模块, 共 616 行, 其中 330 行是同样的四个函数各抄了一遍 (``_build_user_prompt`` /
``_summarize_messages`` / ``_evidence`` / ``run_*_agent`` 的 AST 相似度 99%-100%)。

真正不同的只有: system prompt、工具装载方式, 以及 EvidenceSource / Evidence type /
往返上限这几个标量。这个 dataclass 就是把"不同的部分"写成数据, 相同的部分交给
``runner.run_specialist``。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List

from app.evidence.models import EvidenceSource


@dataclass(frozen=True)
class SpecialistSpec:
    """一个专业 Agent 的全部差异点."""

    name: str
    source: EvidenceSource
    evidence_type: str
    max_iters: int
    max_parallel: int
    system_prompt: str
    empty_incident_hint: str
    task_instruction: str
    load_tools: Callable[[], List[Any]]

    def build_user_prompt(self, incident_text: str) -> str:
        """现象 + 本 Agent 的任务说明; 现象为空时用各自的兜底提示."""
        text = (incident_text or "").strip() or self.empty_incident_hint
        return f"故障现象:\n{text}\n\n{self.task_instruction}"

    def summarize_messages(
        self, messages: List[Any]
    ) -> tuple[str, List[Dict[str, Any]]]:
        """从 run_parallel_agent 输出取最后 AI 消息 + 中间 tool 调用摘要."""
        last = messages[-1] if messages else None
        raw = getattr(last, "content", "") if last is not None else ""
        summary = (raw if isinstance(raw, str) else str(raw)).strip() or (
            f"({self.name} 无输出)"
        )

        tool_calls: List[Dict[str, Any]] = []
        for msg in messages or []:
            # langchain ToolMessage 的 type == "tool"
            if getattr(msg, "type", None) == "tool":
                tool_calls.append(
                    {
                        "name": getattr(msg, "name", ""),
                        "preview": str(getattr(msg, "content", ""))[:500],
                    }
                )
        return summary, tool_calls

    def build_evidence(
        self,
        summary: str,
        content: Dict[str, Any],
        *,
        tool_call_count: int,
        error: str = "",
    ) -> Dict[str, Any]:
        """构造一条 Evidence (与 EvidenceCreate 字段对齐, dict 形式)."""
        metadata: Dict[str, Any] = {
            "agent": self.name,
            "tool_call_count": tool_call_count,
        }
        if error:
            metadata["error_type"] = error
        return {
            "source": str(self.source),
            "type": self.evidence_type,
            "summary": summary[:2000],
            "content": content,
            "metadata": metadata,
        }
