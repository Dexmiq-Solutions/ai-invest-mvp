from langgraph.graph import StateGraph, START, END

from app.graph.state import InvestigationState
from app.graph.context import validate_input, check_context
from app.graph.workspace import setup_workspace
from app.repository.analyzer import analyze_repository


def initial_node(
    state: InvestigationState,
) -> InvestigationState:
    """Initial node for the investigation workflow."""

    return {
        **state,
        "current_step": "initialized",
    }


def route_after_validation(
    state: InvestigationState,
) -> str:
    """
    Decide whether input validation succeeded.
    """

    if state.get("validation_errors"):
        return "validation_failed"

    return "context_check"


def route_after_context(
    state: InvestigationState,
) -> str:
    """
    Decide whether enough context exists to continue.
    """

    if state.get("context_status") == "sufficient":
        return "workspace"

    return "context_insufficient"

def route_after_workspace(
    state: InvestigationState,
) -> str:
    """
    Decide whether workspace setup succeeded.

    Repository analysis should only start when a valid
    isolated workspace has been created.
    """

    if state.get("current_step") == "workspace_ready":
        return "repository_analysis"

    return "workspace_setup_failed"


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


def workspace_setup_failed_node(
    state: InvestigationState,
) -> InvestigationState:
    """
    Stop the workflow when workspace creation fails.
    """

    return {
        **state,
        "current_step": "workspace_setup_failed",
    }


def repository_analysis_node(
    state: InvestigationState,
) -> InvestigationState:
    """
    Analyze the isolated repository workspace.

    This is the Phase 3 node.
    """

    workspace_path = state.get("workspace_path")

    if not workspace_path:
        return {
            **state,
            "repository_summary": {},
            "current_step": "repository_analysis_failed",
        }

    try:
        summary = analyze_repository(workspace_path)

        return {
            **state,
            "repository_summary": summary,
            "current_step": "repository_analyzed",
        }

    except Exception as error:
        return {
            **state,
            "repository_summary": {},
            "validation_errors": [
                *state.get("validation_errors", []),
                f"Repository analysis failed: {error}",
            ],
            "current_step": "repository_analysis_failed",
        }


def build_graph():
    """
    Build the Phase 3 investigation graph.
    """

    graph = StateGraph(InvestigationState)

    # ---------------------------------------------------------
    # Nodes
    # ---------------------------------------------------------

    graph.add_node(
        "initial",
        initial_node,
    )

    graph.add_node(
        "validate_input",
        validate_input,
    )

    graph.add_node(
        "context_check",
        check_context,
    )

    graph.add_node(
        "validation_failed",
        validation_failed_node,
    )

    graph.add_node(
        "context_insufficient",
        context_insufficient_node,
    )

    graph.add_node(
        "workspace",
        setup_workspace,
    )

    graph.add_node(
        "workspace_setup_failed",
        workspace_setup_failed_node,
    )

    graph.add_node(
        "repository_analysis",
        repository_analysis_node,
    )

    # ---------------------------------------------------------
    # Initial flow
    # ---------------------------------------------------------

    graph.add_edge(
        START,
        "initial",
    )

    graph.add_edge(
        "initial",
        "validate_input",
    )

    # ---------------------------------------------------------
    # Input validation routing
    # ---------------------------------------------------------

    graph.add_conditional_edges(
        "validate_input",
        route_after_validation,
        {
            "context_check": "context_check",
            "validation_failed": "validation_failed",
        },
    )

    # ---------------------------------------------------------
    # Context routing
    # ---------------------------------------------------------

    graph.add_conditional_edges(
        "context_check",
        route_after_context,
        {
            "workspace": "workspace",
            "context_insufficient": "context_insufficient",
        },
    )

    # ---------------------------------------------------------
    # Workspace routing
    # ---------------------------------------------------------

    graph.add_conditional_edges(
        "workspace",
        route_after_workspace,
        {
            "repository_analysis": "repository_analysis",
            "workspace_setup_failed": "workspace_setup_failed",
        },
    )

    # ---------------------------------------------------------
    # End states
    # ---------------------------------------------------------

    graph.add_edge(
        "validation_failed",
        END,
    )

    graph.add_edge(
        "context_insufficient",
        END,
    )

    graph.add_edge(
        "workspace_setup_failed",
        END,
    )

    graph.add_edge(
        "repository_analysis",
        END,
    )

    return graph.compile()