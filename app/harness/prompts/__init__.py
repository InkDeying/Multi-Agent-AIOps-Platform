"""Prompt 模板集中地.

这里只放"文本"和格式化占位符, 不放策略也不读 settings —— 模型档位、预算、
reroute 名额这些决策留在 ``harness/runtime/``。

为什么单独成包: 这些模板原先内嵌在 ``runtime/agent_harness.py`` 里, 占了那个
文件 826 行中的 300 多行, 使"Harness 策略"和"prompt 文案"混在同一个类里,
改一句话要翻过一堆 settings 访问器。

按调用方分文件:
  - ``fast.py``: fast 诊断图 (skill_router / planner / executor / replanner / report)
  - ``rag.py``:  RAG Chat (系统提示 / 用户提示 / 查询改写 / 历史压缩)

不做 re-export, 与 ``harness/core`` 和 ``harness/runtime`` 的约定保持一致:
调用方显式写 ``from app.harness.prompts.fast import ...``。
"""
