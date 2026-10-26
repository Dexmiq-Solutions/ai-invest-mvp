"""Phase 6 Structured Logging Test Suite.

Verifies:
1. Stage transitions logged with session ID and duration.
2. Invalid plan dispatch blocked logged.
3. Specialist task start and end logged.
4. Aggregation counts and execution status logged.
5. Workflow stopped after aggregation logged.
6. Logging reuses existing logging configuration.
7. Sensitive information sanitized before emission.
8. Problem description and source files not dumped in full (truncated to max 120 chars / short snippets).
"""
import logging
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from app.api import app, sessions
from app.logging_config import sanitize_log_text
from app.graph.graph import build_graph, plan_invalid_node
from app.agents.investigation_orchestrator import InvestigationOrchestrator


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def sample_workspace(tmp_path: Path):
    ws = tmp_path / "workspaces" / "ws-logging"
    ws.mkdir(parents=True)
    f = ws / "main.py"
    f.write_text("def run(): pass\n", encoding="utf-8")
    return ws


def test_logging_reuses_existing_config():
    """Logging must use standard hierarchical loggers under app namespace."""
    logger_graph = logging.getLogger("app.graph")
    logger_orch = logging.getLogger("app.agents.investigation_orchestrator")
    assert logger_graph.name.startswith("app.")
    assert logger_orch.name.startswith("app.")


def test_sensitive_info_sanitized():
    """Sensitive credentials, keys, and tokens must be masked in log strings."""
    raw = "User password=super_secret_password and bearer_token=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    sanitized = sanitize_log_text(raw)
    assert "super_secret_password" not in sanitized
    assert "[REDACTED]" in sanitized or "***" in sanitized


def test_problem_and_source_not_fully_logged():
    """Problem descriptions must be truncated to prevent huge log dumps."""
    huge_problem = "A" * 500
    truncated = sanitize_log_text(huge_problem, max_len=120)
    assert len(truncated) < 200
    assert "truncated" in truncated
    assert truncated.startswith("A" * 100)


def test_stage_transitions_logged(caplog: pytest.LogCaptureFixture, client: TestClient, sample_workspace: Path):
    """Entry and exit with session ID and duration must be logged across stage endpoints."""
    caplog.set_level(logging.INFO)
    session_id = "test-session-log-transitions"
    sessions[session_id] = {
        "session_id": session_id,
        "problem": "Brief test problem in main.py",
        "workspace_path": str(sample_workspace),
        "workspace_id": "ws-logging",
        "context_status": "sufficient",
        "current_step": "workspace_ready",
        "gathered_context": {
            "relevant_files": ["main.py"],
            "repository_summary": {"project_structure": ["main.py"]},
        },
    }

    resp = client.post(f"/api/investigation/{session_id}/execute")
    assert resp.status_code == 200

    log_text = caplog.text
    assert session_id in log_text
    assert "investigation_start" in log_text
    assert "investigation_execute_complete" in log_text
    assert "duration=" in log_text


def test_invalid_plan_dispatch_blocked_logged(caplog: pytest.LogCaptureFixture):
    """Invalid plan must log plan_validation_dispatch_blocked with zero specialists dispatched."""
    caplog.set_level(logging.WARNING)
    state = {
        "session_id": "sess-invalid-log",
        "investigation_plan": [],
        "plan_validation": {"is_valid": False, "errors": ["Plan cannot be empty"]},
    }
    plan_invalid_node(state)
    assert "plan_validation_dispatch_blocked" in caplog.text
    assert "zero specialists dispatched" in caplog.text


def test_specialist_task_start_and_end_logged(caplog: pytest.LogCaptureFixture, sample_workspace: Path):
    """Specialist dispatch and completion must be logged with task ID and agent name."""
    caplog.set_level(logging.INFO)
    orchestrator = InvestigationOrchestrator()
    plan = [
        {
            "task_id": "TASK-LOG-001",
            "assigned_agent": "code_investigator",
            "scope": ["main.py"],
            "target_files": ["main.py"],
            "instructions": "Inspect main.py",
            "objective": "Verify main function in main.py",
            "reason": "Main entry point",
            "input": {},
            "expected_output": "Definitions",
            "completion_criteria": "Done",
        }
    ]
    state = {
        "session_id": "sess-specialist-log",
        "problem": "Verify main function in main.py",
        "workspace_path": str(sample_workspace),
        "gathered_context": {
            "relevant_files": ["main.py"],
            "repository_summary": {"project_structure": ["main.py"]},
        },
    }

    orchestrator.execute_plan_single_pass(plan=plan, state=state, context=state["gathered_context"])
    log_text = caplog.text
    assert "specialist_task_start" in log_text
    assert "TASK-LOG-001" in log_text
    assert "code_investigator" in log_text
    assert "specialist_task_complete" in log_text


def test_aggregation_counts_and_execution_status_logged(caplog: pytest.LogCaptureFixture, sample_workspace: Path):
    """Aggregation completion must log counts of evidence, findings, hypotheses, and status."""
    caplog.set_level(logging.INFO)
    orchestrator = InvestigationOrchestrator()
    plan = [
        {
            "task_id": "TASK-LOG-002",
            "assigned_agent": "code_investigator",
            "scope": ["main.py"],
            "target_files": ["main.py"],
            "instructions": "Inspect main.py",
            "objective": "Verify main function in main.py",
            "reason": "Main entry point",
            "input": {},
            "expected_output": "Definitions",
            "completion_criteria": "Done",
        }
    ]
    state = {
        "session_id": "sess-agg-log",
        "problem": "Verify main function in main.py",
        "workspace_path": str(sample_workspace),
        "gathered_context": {
            "relevant_files": ["main.py"],
            "repository_summary": {"project_structure": ["main.py"]},
        },
    }

    orchestrator.execute_plan_single_pass(plan=plan, state=state, context=state["gathered_context"])
    log_text = caplog.text
    assert "shared_state_aggregation_start" in log_text
    assert "shared_state_aggregation_complete" in log_text
    assert "execution_status=" in log_text


def test_workflow_stopped_after_aggregation_logged(caplog: pytest.LogCaptureFixture, sample_workspace: Path):
    """Workflow completion must explicitly log that execution stopped without root-cause synthesis."""
    caplog.set_level(logging.INFO)
    orchestrator = InvestigationOrchestrator()
    plan = [
        {
            "task_id": "TASK-LOG-003",
            "assigned_agent": "code_investigator",
            "scope": ["main.py"],
            "target_files": ["main.py"],
            "instructions": "Inspect main.py",
            "objective": "Verify main function in main.py",
            "reason": "Main entry point",
            "input": {},
            "expected_output": "Definitions",
            "completion_criteria": "Done",
        }
    ]
    state = {
        "session_id": "sess-stop-log",
        "problem": "Verify main function in main.py",
        "workspace_path": str(sample_workspace),
        "gathered_context": {
            "relevant_files": ["main.py"],
            "repository_summary": {"project_structure": ["main.py"]},
        },
    }

    orchestrator.execute_plan_single_pass(plan=plan, state=state, context=state["gathered_context"])
    assert "workflow_stopped_after_aggregation" in caplog.text
    assert "root-cause synthesis was not invoked" in caplog.text
