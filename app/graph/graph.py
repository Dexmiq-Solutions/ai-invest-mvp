from langgraph.graph import StateGraph, START, END

from app.graph.state import InvestigationState
from app.graph.context import validate_input, check_context
from app.graph.workspace import setup_workspace


def initial_node(state: InvestigationState) -> InvestigationState:
    """Initial node for the investigation workflow."""

    return {
        **state,
        "current_step": "initialized",
    }


def route_after_validation(state: InvestigationState) -> str:
    """
    Decide whether input validation succeeded.
    """

    if state.get("validation_errors"):
        return "validation_failed"

    return "context_check"


def route_after_context(state: InvestigationState) -> str:
    """
    Decide whether enough context exists to continue.
    """

    if state.get("context_status") == "sufficient":
        return "workspace"

    return "context_insufficient"


def validation_failed_node(
    state: InvestigationState,
) -> InvestigationState:
    return {
        **state,
        "current_step": "validation_failed",
    }


def context_insufficient_node(
    state: InvestigationState,
) -> InvestigationState:
    return {
        **state,
        "current_step": "context_insufficient",
    }


def build_graph():
    """Build the Phase 2 investigation graph."""

    graph = StateGraph(InvestigationState)

    # Nodes
    graph.add_node("initial", initial_node)
    graph.add_node("validate_input", validate_input)
    graph.add_node("context_check", check_context)
    graph.add_node("validation_failed", validation_failed_node)
    graph.add_node(
        "context_insufficient",
        context_insufficient_node,
    )
    graph.add_node("workspace", setup_workspace)

    # Initial flow
    graph.add_edge(START, "initial")
    graph.add_edge("initial", "validate_input")

    # Input validation routing
    graph.add_conditional_edges(
        "validate_input",
        route_after_validation,
        {
            "context_check": "context_check",
            "validation_failed": "validation_failed",
        },
    )

    # Context routing
    graph.add_conditional_edges(
        "context_check",
        route_after_context,
        {
            "workspace": "workspace",
            "context_insufficient": "context_insufficient",
        },
    )

    # End states
    graph.add_edge("validation_failed", END)
    graph.add_edge("context_insufficient", END)
    graph.add_edge("workspace", END)

    return graph.compile()