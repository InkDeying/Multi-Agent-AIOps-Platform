"""AIOps Agent 图的兼容入口。

新代码应显式从 :mod:`app.agents.fast` 或 :mod:`app.agents.deep` 导入。
"""

from app.agents.fast.state import PlanExecuteState


def build_aiops_graph():
    """懒加载 + 构建 fast 诊断图 (避免顶层 import 把 langchain 拖到模块加载期)。"""
    from app.agents.fast import build_aiops_graph as _build_aiops_graph

    return _build_aiops_graph()


def build_deep_graph():
    """懒加载 + 构建 deep 诊断图。"""
    from app.agents.deep import build_deep_graph as _build_deep_graph

    return _build_deep_graph()


__all__ = ["build_aiops_graph", "build_deep_graph", "PlanExecuteState"]
