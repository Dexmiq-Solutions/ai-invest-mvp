"""Tests for Phase 3: Context-Aware Plan Validation Engine.

Covers:
1. Valid plan acceptance (10 mandatory fields, valid agents, relevant scopes).
2. Context-aware task relevance (accepted when relevant to problem/context).
3. Broad-discovery scope acceptance (scope=["*"] or broad directories accepted when search is sparse).
4. Runtime and dependency scope acceptance (manifests, package.json, runtime logs, stack traces).
5. Missing task fields rejection (flags specific missing fields).
6. Empty plans and empty task lists rejection.
7. Duplicate task IDs rejection.
8. Unknown and unassigned agents rejection.
9. Invalid plans blocked before dispatch (orchestrator execution halts with failed status).
10. Zero specialist calls when validation fails (verified via mocks).
11. Revalidation immediately before dispatch (mutated plans caught right before dispatch).
12. Structured validation logging (sanitized, structured log events).
13. Backward compatibility with Phase 2 task schema and aliases (step_id, investigator, target_files, etc.).
14. Empty and whitespace-only field validation.
15. Duplicate task detection (warning on duplicate instructions and scope).
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock
import pytest

from app.agents.plan_validator import (
    validate_investigation_plan,
    PlanValidationResult,
    MANDATORY_TASK_FIELDS,
    AVAILABLE_INVESTIGATOR_AGENTS,
)
from app.agents.investigation_orchestrator import (
    InvestigationOrchestrator,
    InvestigationPlanner,
    AVAILABLE_INVESTIGATORS,
)


def _sample_valid_task(
    task_id: str = "TASK-CODE-001",
    assigned_agent: str = "code_investigator",
    scope: list | None = None,
    objective: str = "Inspect checkout endpoint handler and payment processor",
    reason: str = "500 error reported during checkout flow",
    instructions: str = "Analyze src/routes/checkout.js for unhandled TypeError",
    expected_output: str = "Identified null pointer or undefined property access",
    completion_criteria: str = "Trace unhandled exception to source line",
    status: str = "pending",
) -> Dict[str, Any]:
    return {
        "task_id": task_id,
        "objective": objective,
        "assigned_agent": assigned_agent,
        "reason": reason,
        "input": {"problem": "500 error on checkout", "target_files": scope or ["src/routes/checkout.js"]},
        "scope": scope if scope is not None else ["src/routes/checkout.js"],
        "instructions": instructions,
        "expected_output": expected_output,
        "completion_criteria": completion_criteria,
        "status": status,
    }


# =====================================================================
# 1. Valid plan acceptance
# =====================================================================

def test_valid_plan_acceptance():
    """Verify that a well-formed plan conforming to the 10-field schema passes validation."""
    plan = [
        _sample_valid_task("TASK-CODE-001", "code_investigator", ["src/routes/checkout.js"]),
        _sample_valid_task(
            "TASK-DEP-002",
            "dependency_impact_investigator",
            ["package.json"],
            objective="Inspect payment gateway dependency versions and middleware chain",
            instructions="Check package.json for stripe version mismatches",
        ),
    ]
    problem = "500 error on POST /api/checkout due to TypeError in paymentProcessor"
    gathered_context = {
        "problem_statement": problem,
        "relevant_files": [{"path": "src/routes/checkout.js"}, {"path": "package.json"}],
    }

    result = validate_investigation_plan(
        plan=plan,
        available_agents=AVAILABLE_INVESTIGATOR_AGENTS,
        problem=problem,
        gathered_context=gathered_context,
    )

    assert result.is_valid is True
    assert len(result.errors) == 0
    assert result.validated_task_count == 2
    assert bool(result) is True
    assert result["is_valid"] is True


# =====================================================================
# 2. Context-aware task relevance
# =====================================================================

def test_context_aware_task_relevance():
    """Verify that relevant tasks pass and completely irrelevant tasks are rejected."""
    problem = "Authentication fails with 401 Unauthorized on /api/user/profile"
    gathered_context = {
        "problem_statement": problem,
        "relevant_files": [{"path": "src/auth/jwt.py"}, {"path": "src/routes/user.py"}],
        "keywords": ["auth", "token", "401", "profile"],
    }

    # Relevant task
    relevant_task = _sample_valid_task(
        "TASK-CODE-001",
        "code_investigator",
        scope=["src/auth/jwt.py"],
        objective="Verify JWT token header parsing and 401 response",
        instructions="Inspect jwt.py token verification logic",
        reason="401 error returned on valid token",
    )
    res_rel = validate_investigation_plan(
        plan=[relevant_task],
        available_agents=AVAILABLE_INVESTIGATOR_AGENTS,
        problem=problem,
        gathered_context=gathered_context,
    )
    assert res_rel.is_valid is True

    # Wholly irrelevant task
    irrelevant_task = _sample_valid_task(
        "TASK-CODE-002",
        "code_investigator",
        scope=["blockchain/crypto_miner.cu"],
        objective="Optimize GPU cryptocurrency mining algorithm hashes",
        instructions="Refactor cuda kernel for bitcoin mining performance",
        reason="Maximize hash rate per kilowatt hour",
    )
    res_irrel = validate_investigation_plan(
        plan=[irrelevant_task],
        available_agents=AVAILABLE_INVESTIGATOR_AGENTS,
        problem=problem,
        gathered_context=gathered_context,
    )
    assert res_irrel.is_valid is False
    assert any("relevance" in err.lower() or "unrelated" in err.lower() for err in res_irrel.errors)


# =====================================================================
# 3. Broad-discovery scope acceptance
# =====================================================================

def test_broad_discovery_scope_acceptance():
    """Verify that broad discovery scopes are accepted when search results are sparse or exploratory."""
    problem = "Intermittent network timeout error in distributed order service"
    gathered_context = {
        "problem_statement": problem,
        "relevant_files": [],  # Initial search returned 0 files!
    }

    broad_tasks = [
        _sample_valid_task(
            "TASK-CODE-001",
            "code_investigator",
            scope=["*"],
            objective="Scan entire workspace for timeout and socket configuration",
            instructions="Search codebase for connectTimeout, socketTimeout, and network retry logic",
            reason="Initial targeted search returned no hits; perform broad repository scan",
        ),
        _sample_valid_task(
            "TASK-CODE-002",
            "code_investigator",
            scope=["src/services/"],
            objective="Search services directory for network client instantiation",
            instructions="Scan src/services/ for http client configuration",
            reason="Search services directory broadly for timeout configurations",
        ),
    ]

    res = validate_investigation_plan(
        plan=broad_tasks,
        available_agents=AVAILABLE_INVESTIGATOR_AGENTS,
        problem=problem,
        gathered_context=gathered_context,
    )
    assert res.is_valid is True
    assert res.validated_task_count == 2
    assert len(res.errors) == 0


# =====================================================================
# 4. Runtime and dependency scope acceptance
# =====================================================================

def test_runtime_and_dependency_scope_acceptance():
    """Verify that dependency-inspection and runtime-error scopes are legitimate and accepted."""
    problem = "Application crashes on boot with ModuleNotFoundError: No module named 'pydantic_core'"
    gathered_context = {
        "problem_statement": problem,
        "relevant_files": [],
    }

    dep_task = _sample_valid_task(
        "TASK-DEP-001",
        "dependency_impact_investigator",
        scope=["requirements.txt", "pyproject.toml"],
        objective="Inspect manifest dependencies for missing or incompatible pydantic_core",
        instructions="Check pyproject.toml and installed packages for pydantic version conflicts",
        reason="ModuleNotFoundError on application startup",
    )
    runtime_task = _sample_valid_task(
        "TASK-RUNTIME-002",
        "runtime_error_investigator",
        scope=["logs/app.log", "stderr"],
        objective="Inspect startup stack trace and crash log",
        instructions="Trace crash stack frame to import statement",
        reason="Crash occurs at runtime during startup initialization",
    )

    res = validate_investigation_plan(
        plan=[dep_task, runtime_task],
        available_agents=AVAILABLE_INVESTIGATOR_AGENTS,
        problem=problem,
        gathered_context=gathered_context,
    )
    assert res.is_valid is True
    assert res.validated_task_count == 2
    assert len(res.errors) == 0


# =====================================================================
# 5. Missing task fields
# =====================================================================

@pytest.mark.parametrize("missing_field", [
    "task_id",
    "objective",
    "assigned_agent",
    "reason",
    "input",
    "scope",
    "instructions",
    "expected_output",
    "completion_criteria",
    "status",
])
def test_missing_task_fields(missing_field: str):
    """Verify that omitting any of the 10 mandatory fields causes validation failure with clear error."""
    task = _sample_valid_task()
    del task[missing_field]

    res = validate_investigation_plan(
        plan=[task],
        available_agents=AVAILABLE_INVESTIGATOR_AGENTS,
        problem="500 error on checkout",
        gathered_context={"relevant_files": [{"path": "src/routes/checkout.js"}]},
    )
    assert res.is_valid is False
    assert any(missing_field in err for err in res.errors), f"Expected '{missing_field}' mentioned in errors: {res.errors}"


# =====================================================================
# 6. Empty plans and empty task lists
# =====================================================================

def test_empty_plans_and_empty_task_lists():
    """Verify that empty plans, empty task lists, and non-list plans are rejected."""
    problem = "500 error on checkout"
    gathered_context = {}

    # Empty list
    res_empty_list = validate_investigation_plan(
        plan=[],
        available_agents=AVAILABLE_INVESTIGATOR_AGENTS,
        problem=problem,
        gathered_context=gathered_context,
    )
    assert res_empty_list.is_valid is False
    assert any("at least one task" in err.lower() for err in res_empty_list.errors)

    # None / non-list
    res_none = validate_investigation_plan(
        plan=None,
        available_agents=AVAILABLE_INVESTIGATOR_AGENTS,
        problem=problem,
        gathered_context=gathered_context,
    )
    assert res_none.is_valid is False
    assert any("must be a list" in err.lower() for err in res_none.errors)

    res_dict = validate_investigation_plan(
        plan={"task": "do something"},
        available_agents=AVAILABLE_INVESTIGATOR_AGENTS,
        problem=problem,
        gathered_context=gathered_context,
    )
    assert res_dict.is_valid is False


# =====================================================================
# 7. Duplicate task IDs
# =====================================================================

def test_duplicate_task_ids():
    """Verify that multiple tasks with identical task_ids are rejected."""
    task1 = _sample_valid_task("TASK-001", "code_investigator")
    task2 = _sample_valid_task(
        "TASK-001",  # duplicate ID
        "dependency_impact_investigator",
        scope=["package.json"],
        objective="Inspect package.json dependencies",
        instructions="Check dependency tree",
    )

    res = validate_investigation_plan(
        plan=[task1, task2],
        available_agents=AVAILABLE_INVESTIGATOR_AGENTS,
        problem="500 error on checkout",
        gathered_context={"relevant_files": [{"path": "src/routes/checkout.js"}]},
    )
    assert res.is_valid is False
    assert any("duplicate task id" in err.lower() for err in res.errors)


# =====================================================================
# 8. Unknown and unassigned agents
# =====================================================================

def test_unknown_and_unassigned_agents():
    """Verify that unassigned, empty, or unknown specialist agents are rejected."""
    problem = "500 error on checkout"
    ctx = {"relevant_files": [{"path": "src/routes/checkout.js"}]}

    # Unknown agent
    task_unknown = _sample_valid_task("TASK-001", assigned_agent="quantum_optimizer_agent")
    res_unk = validate_investigation_plan([task_unknown], AVAILABLE_INVESTIGATOR_AGENTS, problem, ctx)
    assert res_unk.is_valid is False
    assert any("unknown or unassigned specialist agent" in err.lower() for err in res_unk.errors)

    # Empty agent
    task_empty = _sample_valid_task("TASK-002", assigned_agent="")
    res_empty = validate_investigation_plan([task_empty], AVAILABLE_INVESTIGATOR_AGENTS, problem, ctx)
    assert res_empty.is_valid is False

    # Agent not in available_agents set
    restricted_agents = {"code_investigator"}
    task_dep = _sample_valid_task("TASK-003", assigned_agent="dependency_impact_investigator")
    res_restr = validate_investigation_plan([task_dep], restricted_agents, problem, ctx)
    assert res_restr.is_valid is False


# =====================================================================
# 9 & 10. Invalid plans blocked before dispatch & zero specialist calls
# =====================================================================

def test_invalid_plans_blocked_before_dispatch_and_zero_specialist_calls(tmp_path: Path):
    """Verify that an invalid plan is stopped immediately before dispatch and dispatches zero specialist tasks."""
    mock_code_agent = MagicMock()
    mock_dep_agent = MagicMock()
    mock_runtime_agent = MagicMock()

    # Workspace directory
    ws_dir = tmp_path / "workspaces" / "ws-test"
    ws_dir.mkdir(parents=True)
    (ws_dir / "app.py").write_text("print('hello')")

    orchestrator = InvestigationOrchestrator(
        code_investigator=mock_code_agent,
        dependency_investigator=mock_dep_agent,
        runtime_investigator=mock_runtime_agent,
        workspaces_root=tmp_path / "workspaces",
    )

    invalid_plan = [
        {
            # Missing 9 fields, invalid agent
            "task_id": "TASK-BAD",
            "assigned_agent": "non_existent_agent",
        }
    ]

    result = orchestrator.investigate(
        investigation_task="Fix broken authentication token handler",
        workspace_path=str(ws_dir),
        workspace_id="ws-test",
        current_state={
            "investigation_plan": invalid_plan,
            "session_id": "sess-test-validation-block",
            "gathered_context": {"relevant_files": [{"path": "app.py"}]},
        },
    )

    # 1. Orchestration failed
    assert result["orchestration_status"] == "failed"
    assert result["execution_status"] == "failed"
    assert result["investigation_status"] == "failed"

    # 2. Plan validation recorded in result
    assert "plan_validation" in result
    assert result["plan_validation"]["is_valid"] is False
    assert len(result["plan_validation"]["errors"]) > 0

    # 3. ZERO specialist calls were made!
    assert mock_code_agent.call_count == 0, "code_investigator must not be dispatched"
    assert mock_dep_agent.call_count == 0, "dependency_investigator must not be dispatched"
    assert mock_runtime_agent.call_count == 0, "runtime_investigator must not be dispatched"
    assert len(result["completed_tasks"]) == 0


def test_empty_plan_blocked_before_dispatch(tmp_path: Path):
    """Verify that an explicitly empty plan is blocked before dispatch with zero specialist calls."""
    mock_code_agent = MagicMock()
    mock_dep_agent = MagicMock()
    mock_runtime_agent = MagicMock()

    ws_dir = tmp_path / "workspaces" / "ws-empty"
    ws_dir.mkdir(parents=True)

    orchestrator = InvestigationOrchestrator(
        code_investigator=mock_code_agent,
        dependency_investigator=mock_dep_agent,
        runtime_investigator=mock_runtime_agent,
        workspaces_root=tmp_path / "workspaces",
    )

    result = orchestrator.investigate(
        investigation_task="Investigate database deadlock",
        workspace_path=str(ws_dir),
        workspace_id="ws-empty",
        current_state={
            "investigation_plan": [],  # Explicit empty plan!
            "session_id": "sess-test-empty-plan",
        },
    )

    assert result["orchestration_status"] == "failed"
    assert result["execution_status"] == "failed"
    assert result["plan_validation"]["is_valid"] is False
    assert any("at least one task" in err.lower() for err in result["plan_validation"]["errors"])
    assert mock_code_agent.call_count == 0
    assert mock_dep_agent.call_count == 0
    assert mock_runtime_agent.call_count == 0


# =====================================================================
# 11. Revalidation immediately before dispatch
# =====================================================================

def test_revalidation_immediately_before_dispatch(tmp_path: Path):
    """Verify that a plan validated earlier, but tampered with before dispatch, is re-validated and blocked."""
    mock_code_agent = MagicMock()
    mock_dep_agent = MagicMock()
    mock_runtime_agent = MagicMock()

    ws_dir = tmp_path / "workspaces" / "ws-reval"
    ws_dir.mkdir(parents=True)

    orchestrator = InvestigationOrchestrator(
        code_investigator=mock_code_agent,
        dependency_investigator=mock_dep_agent,
        runtime_investigator=mock_runtime_agent,
        workspaces_root=tmp_path / "workspaces",
    )

    # A previously valid plan that had a field cleared/corrupted before execution
    corrupted_plan = [
        _sample_valid_task("TASK-001", "code_investigator", ["src/routes/checkout.js"]),
    ]
    # Corrupt the plan right before dispatch
    corrupted_plan[0]["instructions"] = "   "  # Empty whitespace instructions!

    result = orchestrator.investigate(
        investigation_task="500 error on checkout",
        workspace_path=str(ws_dir),
        workspace_id="ws-reval",
        current_state={
            "investigation_plan": corrupted_plan,
            "session_id": "sess-test-tampered-plan",
            "gathered_context": {"relevant_files": [{"path": "src/routes/checkout.js"}]},
        },
    )

    assert result["orchestration_status"] == "failed"
    assert result["plan_validation"]["is_valid"] is False
    assert any("instructions" in err.lower() for err in result["plan_validation"]["errors"])
    assert mock_code_agent.call_count == 0


# =====================================================================
# 12. Structured validation logging
# =====================================================================

def test_validation_logging(caplog):
    """Verify structured logging emitted during plan validation and that sensitive info is not leaked."""
    caplog.set_level(logging.INFO)

    plan = [_sample_valid_task()]
    problem = "Secret API key sk_live_12345 leaked in POST /api/v1/auth"
    gathered_context = {
        "problem_statement": problem,
        "relevant_files": [{"path": "src/routes/checkout.js"}],
    }

    validate_investigation_plan(
        plan=plan,
        available_agents=AVAILABLE_INVESTIGATOR_AGENTS,
        problem=problem,
        gathered_context=gathered_context,
        session_id="session-log-test",
    )

    log_text = caplog.text
    assert "plan_validation_start" in log_text
    assert "session-log-test" in log_text
    assert "plan_validation_complete" in log_text
    assert "is_valid=True" in log_text

    # Verify sensitive raw prompt details are sanitized / not logged raw
    assert "sk_live_12345 leaked in POST" not in log_text


# =====================================================================
# 13. Backward compatibility with Phase 2 task schema and aliases
# =====================================================================

def test_backward_compatibility_with_phase2_schema_and_aliases():
    """Verify that tasks defined using Phase 2 aliases pass validation and retain normalized fields."""
    legacy_task = {
        "step_id": "TASK-CODE-LEGACY-001",
        "investigator": "code_investigator",
        "task": "Inspect checkout route handler",
        "target_files": ["src/routes/checkout.js"],
        "rationale": "500 error reported during checkout flow",
        "input": {"problem": "500 checkout error"},
        "expected_evidence": "Found unhandled null property access",
        "completion_criteria": "Exception mapped to line number",
        "status": "pending",
    }

    result = validate_investigation_plan(
        plan=[legacy_task],
        available_agents=AVAILABLE_INVESTIGATOR_AGENTS,
        problem="500 checkout error",
        gathered_context={"relevant_files": [{"path": "src/routes/checkout.js"}]},
    )

    assert result.is_valid is True
    assert result.validated_task_count == 1
    assert len(result.errors) == 0


# =====================================================================
# 14. Empty and whitespace-only field validation
# =====================================================================

@pytest.mark.parametrize("empty_field", [
    "task_id",
    "objective",
    "reason",
    "instructions",
    "expected_output",
    "completion_criteria",
])
def test_empty_string_fields_rejected(empty_field: str):
    """Verify that whitespace-only string values in mandatory fields are rejected."""
    task = _sample_valid_task()
    task[empty_field] = "   \n\t  "

    res = validate_investigation_plan(
        plan=[task],
        available_agents=AVAILABLE_INVESTIGATOR_AGENTS,
        problem="500 error on checkout",
        gathered_context={"relevant_files": [{"path": "src/routes/checkout.js"}]},
    )
    assert res.is_valid is False
    assert any(empty_field in err.lower() and "non-empty" in err.lower() for err in res.errors)


# =====================================================================
# 15. Duplicate task detection
# =====================================================================

def test_duplicate_tasks_detection():
    """Verify that identical tasks produce warnings about duplicate work."""
    task1 = _sample_valid_task("TASK-001", "code_investigator", ["src/routes/checkout.js"])
    task2 = _sample_valid_task("TASK-002", "code_investigator", ["src/routes/checkout.js"])  # same instructions & scope

    res = validate_investigation_plan(
        plan=[task1, task2],
        available_agents=AVAILABLE_INVESTIGATOR_AGENTS,
        problem="500 error on checkout",
        gathered_context={"relevant_files": [{"path": "src/routes/checkout.js"}]},
    )
    # Warnings should indicate duplicate task assignment
    assert any("duplicate task" in w.lower() for w in res.warnings)


# =====================================================================
# 16. API Endpoint Enforcement Gates (Plan, Execute, Run-Full)
# =====================================================================

def test_api_plan_endpoint_validation():
    """Verify that /api/investigation/{session_id}/plan returns plan_validation result."""
    from fastapi.testclient import TestClient
    from app.api import app, sessions

    client = TestClient(app)
    sid = "test-plan-api-session"
    sessions[sid] = {
        "session_id": sid,
        "problem": "500 error on checkout",
        "investigation_plan": [_sample_valid_task("TASK-001", "code_investigator", ["src/routes/checkout.js"])],
        "gathered_context": {"relevant_files": [{"path": "src/routes/checkout.js"}]},
    }

    res = client.get(f"/api/investigation/{sid}/plan")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "PLAN_READY"
    assert "plan_validation" in data
    assert data["plan_validation"]["is_valid"] is True
    assert data["plan_validation"]["validated_task_count"] == 1


def test_api_execute_blocks_invalid_plan():
    """Verify that /api/investigation/{session_id}/execute blocks execution before dispatch on invalid plan."""
    from fastapi.testclient import TestClient
    from app.api import app, sessions

    client = TestClient(app)
    sid = "test-execute-invalid-block"
    sessions[sid] = {
        "session_id": sid,
        "problem": "500 error on checkout",
        # Corrupted / invalid plan in state
        "investigation_plan": [
            {
                "task_id": "TASK-BAD",
                "assigned_agent": "unknown_agent",
            }
        ],
        "gathered_context": {"relevant_files": [{"path": "src/routes/checkout.js"}]},
    }

    res = client.post(f"/api/investigation/{sid}/execute")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "INVESTIGATION_FAILED"
    assert data["execution_status"] == "failed"
    assert data["is_conclusive"] is False
    assert "plan_validation" in data
    assert data["plan_validation"]["is_valid"] is False
    assert len(data["plan_validation"]["errors"]) > 0


def test_api_run_full_blocks_invalid_plan():
    """Verify that /api/investigation/{session_id}/run-full blocks dispatch on invalid plan."""
    from fastapi.testclient import TestClient
    from app.api import app, sessions

    client = TestClient(app)
    sid = "test-runfull-invalid-block"
    sessions[sid] = {
        "session_id": sid,
        "problem": "500 error on checkout",
        "current_step": "context_gathering_complete",
        "investigation_plan": [],  # Explicit empty plan
        "gathered_context": {"relevant_files": [{"path": "src/routes/checkout.js"}]},
    }

    res = client.post(f"/api/investigation/{sid}/run-full")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "INVESTIGATION_FAILED"
    assert data["execution_status"] == "failed"
    assert data["plan_validation"]["is_valid"] is False

