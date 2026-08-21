"""fast 图最终报告的合成与兜底渲染.

从 ``nodes/replanner.py`` 搬出来: 那个文件 427 行里只有约 60 行是真正的 replan
控制流, 另外 87 行是"怎么把报告写出来"。两件事的变更理由不同 —— 改报告格式不该
碰决策逻辑。

分层策略: Replanner 用便宜模型 (flash) 做决策, 这里用 report_model (默认 pro)
专职把草稿 polish 成 5 段结构; 两者同模型时跳过二次调用省一次费用。
任何一步失败都往下降级, 最差也能用 ``force_summary`` 出一份可读报告。
"""

from __future__ import annotations

import re
from datetime import datetime

from loguru import logger

from app.harness.core.llm import get_chat_llm
from app.harness.core.llm_parse import content_to_text
from app.harness.runtime.agent_harness import get_agent_harness


def current_report_time() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")


def ensure_report_time(report: str, current_time: str) -> str:
    """保证报告里有且只有一行正确的"生成时间".

    LLM 经常自己编一个时间, 或者干脆不写。这里做三种情况的归一:
    已有该行则替换, 有标题无该行则插到标题后, 都没有则补一个标题。
    """
    line = f"**生成时间**: {current_time}"
    if re.search(r"^\*\*生成时间\*\*:.*$", report, flags=re.MULTILINE):
        return re.sub(r"^\*\*生成时间\*\*:.*$", line, report, count=1, flags=re.MULTILINE)
    if report.startswith("# 故障诊断报告"):
        return report.replace("# 故障诊断报告", f"# 故障诊断报告\n{line}", 1)
    return f"# 故障诊断报告\n{line}\n\n{report}"


async def synthesize_final_report(
    user_input: str,
    past_steps: list[tuple[str, str]],
    current_time: str,
    draft: str = "",
) -> str:
    """用 report_model (默认 pro) 基于 past_steps 写高质量最终报告.

    只在 Replanner 决定 is_finished=true 时调一次, 给最终报告质量兜底.
    Replanner 自己用 flash 做决策, 这里用 pro 专职 polish, 质量/速度两头兼顾.
    失败时返回 draft (或 force_summary 做进一步兜底).
    """
    harness = get_agent_harness()
    report_model = harness.report_model()
    decide_model = harness.report_decision_model()
    # 如果 report_model 和 decide 用的是同一个模型, 说明用户没分层, 直接用 draft 省一次调用.
    if not draft.strip() and not past_steps:
        return ""
    if report_model == decide_model and draft.strip():
        logger.debug(
            f"[Report] report_model={report_model} 与 replanner 同模型且草稿非空, 跳过二次合成"
        )
        return draft
    try:
        llm = get_chat_llm(
            model=report_model,
            temperature=0.2,
            timeout=45,
            max_retries=1,
        )
        resp = await llm.ainvoke(
            harness.build_report_messages(
                user_input=user_input,
                past_steps=past_steps,
                current_time=current_time,
                draft=draft,
            )
        )
        content = getattr(resp, "content", str(resp))
        if isinstance(content, list):
            content = content_to_text(content)
        text = (content or "").strip()
        if not text:
            logger.warning("[Report] pro 返回空文本, 回退 draft")
            return draft
        logger.info(
            f"[Report] 用 {report_model} 合成最终报告, len={len(text)} (草稿 len={len(draft)})"
        )
        return text
    except Exception as e:
        logger.warning(
            f"[Report] 用 {report_model} 合成报告失败 ({type(e).__name__}: {e}), 回退 draft"
        )
        return draft


def force_summary(
    user_input: str,
    past_steps: list[tuple[str, str]],
    current_time: str,
) -> str:
    """硬兜底: 当 LLM 决策失败或超步数, 用模板生成简单报告."""
    if not past_steps:
        return f"# 故障诊断报告\n**生成时间**: {current_time}\n\n## 问题\n{user_input}\n\n## 结论\n诊断流程异常终止, 未能收集到有效信息, 请人工介入。"

    sections = [
        "# 故障诊断报告\n",
        f"**生成时间**: {current_time}\n",
        f"## 问题\n{user_input}\n",
        "## 收集到的信息\n",
    ]
    for i, (step, result) in enumerate(past_steps, 1):
        snippet = result[:300].replace("\n", " ")
        sections.append(f"**{i}. {step}**\n{snippet}\n")
    sections.append("## 结论\n基于以上信息, 建议进一步人工确认根因和处置方案。")
    return "\n".join(sections)
