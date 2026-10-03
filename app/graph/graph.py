from langgraph.graph import StateGraph, START, END

from app.graph.state import InvestigationState


def initial_node(state: InvestigationState) -> InvestigationState:
    """Initial node for the investigation workflow."""

    return {
        **state,
        "current_step": "initialized",
    }


def build_graph():
    """Build the initial investigation graph."""

    graph = StateGraph(InvestigationState)

    graph.add_node("initial", initial_node)

    graph.add_edge(START, "initial")
    graph.add_edge("initial", END)

    return graph.compile()