"""Deep diagnosis graph package."""

from app.agents.deep.state import DeepDiagnosisState


def build_deep_graph():
    """Lazily build the deep evidence diagnosis graph."""
    from app.agents.deep.graph import build_deep_graph as build

    return build()


__all__ = ["DeepDiagnosisState", "build_deep_graph"]
