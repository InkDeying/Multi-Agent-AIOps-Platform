"""Fast diagnosis graph package."""

from app.agents.fast.state import PlanExecuteState


def build_aiops_graph():
    """Lazily build the fast Skill-Plan-Execute-Replan graph."""
    from app.agents.fast.graph import build_aiops_graph as build

    return build()


__all__ = ["PlanExecuteState", "build_aiops_graph"]
