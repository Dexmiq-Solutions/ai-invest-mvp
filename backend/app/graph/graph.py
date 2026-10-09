from langgraph.graph import StateGraph, START, END

from app.graph.state import InvestigationState
from app.graph.context import validate_input, check_context
from app.graph.workspace import setup_workspace
from app.graph.context_gathering import context_gathering_node

from app.repository.analyzer import analyze_repository
from app.tools.investigation_tools import (
    list_files,
    search_code,
    read_file,
)


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
    """Decide whether input validation succeeded."""

    if state.get("validation_errors"):
        return "validation_failed"

    return "context_check"


def route_after_context(
    state: InvestigationState,
) -> str:
    """Decide whether enough context exists to continue."""

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
    """Stop the workflow when workspace creation fails."""

    return {
        **state,
        "current_step": "workspace_setup_failed",
    }


def repository_analysis_node(
    state: InvestigationState,
) -> InvestigationState:
    """
    Analyze the isolated repository workspace.
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


def context_gathering_graph_node(
    state: InvestigationState,
) -> InvestigationState:
    """
    Run Phase 4 Context Gathering.

    Adapts the existing InvestigationState structure
    to the Context Gathering node.
    """

    workspace_path = state.get("workspace_path")

    if not workspace_path:
        return {
            **state,
            "gathered_context": {},
            "validation_errors": [
                *state.get("validation_errors", []),
                "Context gathering failed: workspace_path is missing.",
            ],
            "current_step": "context_gathering_failed",
        }

    try:
        result = context_gathering_node(
            {
                **state,

                # Context Gathering expects "workspace",
                # while InvestigationState stores "workspace_path".
                "workspace": workspace_path,
            },
            list_files_tool=list_files,
            search_code_tool=search_code,
            read_file_tool=read_file,
        )

        return {
            **state,
            **result,
        }

    except Exception as error:
        return {
            **state,
            "gathered_context": {},
            "validation_errors": [
                *state.get("validation_errors", []),
                f"Context gathering failed: {error}",
            ],
            "current_step": "context_gathering_failed",
        }


def build_graph():
    """
    Build the Phase 4 investigation graph.

    Flow:

    Input
        ↓
    Validate Input
        ↓
    Context Check
        ↓
    Workspace Setup
        ↓
    Repository Analysis
        ↓
    Context Gathering
        ↓
       END
    """

    graph = StateGraph(InvestigationState)

    # =========================================================
    # Nodes
    # =========================================================

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

    graph.add_node(
        "context_gathering",
        context_gathering_graph_node,
    )

    # =========================================================
    # Initial Flow
    # =========================================================

    graph.add_edge(
        START,
        "initial",
    )

    graph.add_edge(
        "initial",
        "validate_input",
    )

    # =========================================================
    # Input Validation Routing
    # =========================================================

    graph.add_conditional_edges(
        "validate_input",
        route_after_validation,
        {
            "context_check": "context_check",
            "validation_failed": "validation_failed",
        },
    )

    # =========================================================
    # Context Check Routing
    # =========================================================

    graph.add_conditional_edges(
        "context_check",
        route_after_context,
        {
            "workspace": "workspace",
            "context_insufficient": "context_insufficient",
        },
    )

    # =========================================================
    # Workspace Routing
    # =========================================================

    graph.add_conditional_edges(
        "workspace",
        route_after_workspace,
        {
            "repository_analysis": "repository_analysis",
            "workspace_setup_failed": "workspace_setup_failed",
        },
    )

    # =========================================================
    # Repository Analysis → Context Gathering
    # =========================================================

    graph.add_edge(
        "repository_analysis",
        "context_gathering",
    )

    # =========================================================
    # End States
    # =========================================================

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
        "context_gathering",
        END,
    )

    return graph.compile()