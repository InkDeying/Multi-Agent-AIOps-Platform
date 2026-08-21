"""LLM 输出解析的小工具.

聚合点: 此前 ``_extract_json`` 散落多处各写一份, 容差细节不一致 (正则 ``\\{.*\\}`` vs
``find/rfind`` 切片), 抽到此处统一; ``content_to_text`` 同理。

设计选择: 用 ``find('{') + rfind('}')`` 切片版 (reflector / drafter 风格) 而非
原 ``structured`` 的正则版 ——
  1. 显式拿首尾大括号, 对 LLM 输出夹杂的解释性文本鲁棒;
  2. 不依赖 DOTALL 的回溯, 解析失败时报错更明确;
  3. 末尾追加 ``isinstance(..., dict)`` 校验, 避免 LLM 返回 JSON 数组顶层时静默通过.
"""

from __future__ import annotations

import json
from typing import Any


def extract_json(text: str, *, source: str = "llm output") -> dict[str, Any]:
    """从 LLM 输出里抠出第一个 JSON 对象.

    解析步骤:
      1. 去掉 ``\\`\\`\\`json ... \\`\\`\\``` 围栏;
      2. 取首个 ``{`` 到末个 ``}`` 的子串;
      3. ``json.loads`` 后校验为 dict.

    任一步失败抛出 ``ValueError`` —— 由调用方决定是兜底还是上抛.

    Args:
        text: LLM 原始输出字符串.
        source: 报错时附带的来源名 (例: ``"reflection output"``), 便于定位.
    """
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw[:4].lower() == "json":
            raw = raw[4:]
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError(f"no json object in {source}")
    obj = json.loads(raw[start : end + 1])
    if not isinstance(obj, dict):
        raise ValueError(f"{source} is not a json object")
    return obj


def content_to_text(content: Any) -> str:
    """LangChain 消息 content 可能是 str 或 list[dict|str], 统一抽成纯文本.

    聚合点: 此前 ``structured`` / ``tool_runner`` / ``replanner`` / RAG 服务各写了
    一遍同样的 ``"".join(...)``。这里是唯一实现, 其余位置只调用。

    注意: ``harness/wiki/text_utils.py:coerce_text`` 和 ``rca_judge`` 里的转换看着像,
    但语义不同 (前者还认 ``content`` 键, 后者对 list 取 repr), 故意没有合并进来。
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            item.get("text", "") if isinstance(item, dict) else str(item)
            for item in content
        )
    return str(content)
