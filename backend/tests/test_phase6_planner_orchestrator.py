"""Phase 6 Tests: Investigation Planner and Orchestrator.

Tests dynamic planning, task assignment, investigator execution,
result aggregation, error handling, bounded execution, and state propagation.
"""

from pathlib import Path
import pytest

from app.agents.investigation_orchestrator import (
    InvestigationPlanner,
    InvestigationOrchestrator,
)
from app.agents.deep_agent import deep_agent_node


@pytest.fixture
def mock_repo_summary():
    return {
        "project_structure": ["src/api/auth.py", "src/services/user_service.py", "src/models/user.py"],
        "languages": ["Python"],
        "frameworks": ["FastAPI"],
        "important_directories": ["src/api", "src/services", "src/models"],
        "entry_points": ["src/main.py"],
        "configuration_files": ["requirements.txt"],
        "test_directories": ["tests"],
    }


@pytest.fixture
def test_workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "orchestrator_project"
    ws.mkdir(parents=True)
    (ws / "auth.py").write_text("def authenticate():\n    return False\n", encoding="utf-8")
    (ws / "service.py").write_text("import auth\ndef run():\n    return auth.authenticate()\n", encoding="utf-8")
    return ws


# ============================================================
# 1. Planner: Dynamic Investigator Selection & Task Generation
# ============================================================

def test_planner_selects_auth_investigators(mock_repo_summary):
    """Planner should select code and dependency/runtime investigators for auth failures."""
    planner = InvestigationPlanner(llm_factory=None)  # Use heuristic planning

    plan = planner.plan(
        problem="My profile page returns 401 after login.",
        repository_summary=mock_repo_summary,
        gathered_context={
            "relevant_files": ["src/api/auth.py"],
            "investigation_concepts": ["authentication", "token", "login"],
            "http_status_codes": [401],
        },
        current_state={},
    )

    assert isinstance(plan, list)
    assert len(plan) >= 1

    selected_agents = {step["investigator"] for step in plan}
    # For an auth 401 problem, code or runtime/dependency investigators should be selected
    assert any(a in selected_agents for a in ("code_investigator", "runtime_error_investigator", "dependency_impact_investigator"))

    # Each step must have a task, rationale, and investigator
    for step in plan:
        assert step.get("investigator")
        assert step.get("task")
        assert step.get("rationale") or step.get("objective")


def test_planner_selects_runtime_investigator_for_stack_trace(mock_repo_summary):
    """Planner should select runtime_error_investigator when stack trace is present."""
    planner = InvestigationPlanner(llm_factory=None)

    plan = planner.plan(
        problem="Application crashed with unhandled exception",
        repository_summary=mock_repo_summary,
        gathered_context={
            "stack_trace": 'File "src/api/auth.py", line 12, in login\nTypeError: NoneType',
        },
        current_state={
            "errors": ["TypeError: NoneType has no attribute 'id'"],
        },
    )

    selected_agents = {step["investigator"] for step in plan}
    assert "runtime_error_investigator" in selected_agents


def test_planner_omits_irrelevant_investigators(mock_repo_summary):
    """Planner for a simple calculation bug should not unconditionally run all investigators."""
    planner = InvestigationPlanner(llm_factory=None)

    plan = planner.plan(
        problem="The discount calculator applies 10% instead of 15% discount on checkout.",
        repository_summary=mock_repo_summary,
        gathered_context={
            "relevant_files": ["src/services/discount.py"],
        },
        current_state={},
    )

    selected_agents = {step["investigator"] for step in plan}
    # For pure logic calculation, runtime_error_investigator is unnecessary
    assert "runtime_error_investigator" not in selected_agents or len(plan) < 3


# ============================================================
# 2. Orchestrator: Coordination and Finding Collection
# ============================================================

def test_orchestrator_executes_investigation(test_workspace: Path, mock_repo_summary):
    """Orchestrator runs planned specialists and aggregates findings."""
    orchestrator = InvestigationOrchestrator(
        max_iterations=3,
        llm_factory=None,
    )

    result = orchestrator.investigate(
        investigation_task="Investigate auth failure in service.py",
        workspace_path=str(test_workspace),
        shared_context={
            "repository_summary": mock_repo_summary,
            "relevant_files": ["auth.py", "service.py"],
        },
        current_state={
            "workspace_path": str(test_workspace),
            "problem": "Auth fails",
        },
    )

    assert result["orchestration_status"] in ("completed", "partial", "sufficient")
    assert result["iterations"] >= 1
    assert result["iterations"] <= 3

    # Investigators tracked
    assert len(result["completed_investigators"]) + len(result["failed_investigators"]) >= 1

    # Aggregated findings/evidence present
    assert isinstance(result["aggregated_findings"], list)
    assert isinstance(result["evidence_pool"], list)
    assert isinstance(result["execution_history"], list)


def test_orchestrator_bounded_iterations(test_workspace: Path, mock_repo_summary):
    """Orchestrator must not loop indefinitely and respect max_iterations."""
    max_iter = 2
    orchestrator = InvestigationOrchestrator(
        max_iterations=max_iter,
        llm_factory=None,
    )

    result = orchestrator.investigate(
        investigation_task="Deep complex investigation across modules",
        workspace_path=str(test_workspace),
        shared_context={"repository_summary": mock_repo_summary},
        current_state={"workspace_path": str(test_workspace)},
    )

    assert result["iterations"] <= max_iter


def test_orchestrator_handles_investigator_failure(test_workspace: Path, mock_repo_summary):
    """Orchestrator records investigator failure honestly without crashing."""
    def failing_code_agent(*args, **kwargs):
        raise RuntimeError("Simulated specialist failure")

    orchestrator = InvestigationOrchestrator(
        code_investigator=failing_code_agent,
        max_iterations=2,
        llm_factory=None,
    )

    result = orchestrator.investigate(
        investigation_task="Check authentication bug",
        workspace_path=str(test_workspace),
        shared_context={"repository_summary": mock_repo_summary},
        current_state={"workspace_path": str(test_workspace)},
    )

    # Failed investigator should be recorded
    assert "code_investigator" in result["failed_investigators"]


# ============================================================
# 3. Deep Agent LangGraph Node Integration
# ============================================================

def test_deep_agent_node_integration(test_workspace: Path, mock_repo_summary):
    """deep_agent_node populates all required Phase 6 shared state fields."""
    state = {
        "problem": "Authentication fails during login",
        "workspace_path": str(test_workspace),
        "repository_summary": mock_repo_summary,
        "context_status": "sufficient",
        "gathered_context": {
            "relevant_files": ["auth.py"],
        },
    }

    result = deep_agent_node(state)

    # Required state fields after Phase 6 execution
    assert "investigation_plan" in result
    assert "findings" in result
    assert "evidence" in result
    assert "selected_investigators" in result
    assert "completed_investigators" in result
    assert "failed_investigators" in result
    assert "investigation_status" in result
    assert "current_step" in result
    assert result["current_step"] in ("investigation_completed", "investigation_failed")


def test_deep_agent_node_blocks_on_incomplete_context():
    """deep_agent_node must not proceed if upstream context is insufficient."""
    state = {
        "problem": "App broken",
        "context_status": "insufficient",
    }

    result = deep_agent_node(state)
    assert result["investigation_status"] == "incomplete"
    assert "cannot start" in result["investigation_stop_reason"].lower()
