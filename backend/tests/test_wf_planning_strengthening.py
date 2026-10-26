"""Comprehensive test suite for Strengthen Investigation Planning.

Validates:
1. Understanding the problem correctly: decomposition of symptoms, expected behavior, known facts, and diagnostic gaps.
2. Problem-specific strategies across diverse software categories (geometric calc, runtime crash, auth/403, data contract).
3. Appropriate specialist selection (rejecting runtime specialists on pure calculation logic without runtime diagnostics).
4. Handling missing context and uncertainty without fabricating runtime errors.
5. Actionable tasks vs placeholder/vague tasks (semantic validation beyond schema).
6. Planning / investigation boundary enforcement (rejecting premature root cause assertions and claimed evidence).
7. Execution-path coverage across architectural layers (routes -> services -> data layer).
8. Bounded self-correction retry loop when LLM generates semantically weak/invalid plans.
9. Scope hygiene and broad discovery fallback for sparse search.
10. Backward compatibility with existing task schemas and aliases.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock
import pytest

from app.agents.investigation_orchestrator import (
    InvestigationPlanner,
    InvestigationOrchestrator,
    InvestigationPlanSchema,
    ProblemDecomposition,
    AVAILABLE_INVESTIGATORS,
)
from app.agents.plan_validator import (
    validate_investigation_plan,
    PlanValidationResult,
    AVAILABLE_INVESTIGATOR_AGENTS,
)


def _build_task(
    task_id: str = "TASK-CODE-001",
    assigned_agent: str = "code_investigator",
    scope: list | None = None,
    objective: str = "Verify polygon intersection area calculation against expected coordinates",
    reason: str = "Reported calculation error in bounding box clipping logic",
    instructions: str = "Inspect src/geometry/clip.py to verify arithmetic operators in Sutherland-Hodgman algorithm",
    expected_output: str = "Algorithm line calculating coordinate intersection and return values",
    completion_criteria: str = "Coordinate calculation verified against test polygon",
    status: str = "pending",
) -> dict:
    return {
        "task_id": task_id,
        "objective": objective,
        "assigned_agent": assigned_agent,
        "reason": reason,
        "input": {"target_files": scope or ["src/geometry/clip.py"]},
        "scope": scope if scope is not None else ["src/geometry/clip.py"],
        "instructions": instructions,
        "expected_output": expected_output,
        "completion_criteria": completion_criteria,
        "status": status,
    }


# =====================================================================
# 1. Understanding the problem correctly
# =====================================================================

def test_problem_decomposition_extracts_symptoms_and_expected_behavior():
    """Verify problem decomposition extracts symptoms, expected outcomes, and identifies missing diagnostics."""
    problem = "Polygon intersection area returns 120.5 instead of 60.25 in clip.py"
    gathered_context = {
        "relevant_files": [{"path": "src/geometry/clip.py"}],
    }
    current_state = {}

    decomp = InvestigationPlanner._decompose_problem_statement(
        problem=problem,
        gathered_context=gathered_context,
        current_state=current_state,
    )

    assert decomp["problem_category"] == "logic_calculation"
    assert "60.25" in decomp["expected_behavior"]
    assert any("clip.py" in s or "returns" in s.lower() for s in decomp["symptoms"])
    # Missing diagnostics correctly identified
    assert any("runtime stack trace" in u.lower() for u in decomp["uncertainties_and_gaps"])
    assert any("execution logs" in u.lower() for u in decomp["uncertainties_and_gaps"])


def test_problem_decomposition_identifies_runtime_crash_category():
    """Verify runtime crashes with stack traces are categorized as runtime_crash."""
    problem = "Unhandled ZeroDivisionError in matrix normalization during batch processing"
    current_state = {"stack_trace": "ZeroDivisionError: division by zero at matrix.py:88"}

    decomp = InvestigationPlanner._decompose_problem_statement(
        problem=problem,
        gathered_context={},
        current_state=current_state,
    )

    assert decomp["problem_category"] == "runtime_crash"
    assert "ZeroDivisionError" in decomp["symptoms"]


def test_problem_decomposition_identifies_auth_and_data_categories():
    """Verify authentication and data contract problems are categorized accurately."""
    decomp_auth = InvestigationPlanner._decompose_problem_statement(
        problem="User receives 403 Forbidden when accessing analytics dashboard",
        gathered_context={},
        current_state={},
    )
    assert decomp_auth["problem_category"] == "auth_access_control"
    assert "HTTP status 403" in decomp_auth["symptoms"]

    decomp_data = InvestigationPlanner._decompose_problem_statement(
        problem="Database query mismatch: SQL filter fails to match datetime timezone offset in records",
        gathered_context={},
        current_state={},
    )
    assert decomp_data["problem_category"] == "data_contract_interface"


# =====================================================================
# 2. Appropriate specialist selection (rejecting misaligned specialists)
# =====================================================================

def test_reject_runtime_investigator_on_pure_calculation_without_errors():
    """Verify validator rejects runtime_error_investigator for pure calculation bugs with no error logs/traces."""
    task = _build_task(
        task_id="TASK-RT-001",
        assigned_agent="runtime_error_investigator",
        scope=["src/geometry/clip.py"],
        objective="Inspect geometric bounding box formula",
        instructions="Analyze coordinate arithmetic in clip.py",
        expected_output="Formula lines",
        completion_criteria="Formula inspected",
    )
    problem = "Polygon intersection area returns 120.5 instead of 60.25"

    result = validate_investigation_plan(
        plan=[task],
        available_agents=AVAILABLE_INVESTIGATOR_AGENTS,
        problem=problem,
        gathered_context={"relevant_files": [{"path": "src/geometry/clip.py"}]},
    )

    assert result.is_valid is False
    assert any("inappropriate specialist assignment" in err.lower() for err in result.errors)
    assert any("runtime_error_investigator" in err for err in result.errors)


def test_accept_runtime_investigator_when_runtime_signals_present():
    """Verify validator accepts runtime_error_investigator when runtime errors/crashes are reported."""
    task = _build_task(
        task_id="TASK-RT-001",
        assigned_agent="runtime_error_investigator",
        scope=["src/matrix/norm.py"],
        objective="Parse ZeroDivisionError stack trace and locate crash line",
        instructions="Map ZeroDivisionError traceback frames to norm.py line 88",
        expected_output="Crash frame localized to norm.py:88",
        completion_criteria="Exact crash point located in source",
    )
    problem = "Crash with unhandled ZeroDivisionError in norm.py line 88"

    result = validate_investigation_plan(
        plan=[task],
        available_agents=AVAILABLE_INVESTIGATOR_AGENTS,
        problem=problem,
        gathered_context={
            "relevant_files": [{"path": "src/matrix/norm.py"}],
            "stack_trace": "ZeroDivisionError: division by zero at norm.py:88",
        },
    )

    assert result.is_valid is True
    assert len(result.errors) == 0


# =====================================================================
# 3. Actionable tasks vs placeholder/vague tasks
# =====================================================================

@pytest.mark.parametrize("placeholder", ["todo", "n/a", "none", "tbd", "placeholder"])
def test_placeholder_completion_criteria_rejected(placeholder: str):
    """Verify that placeholder completion criteria are rejected by semantic validation."""
    task = _build_task()
    task["completion_criteria"] = placeholder

    result = validate_investigation_plan(
        plan=[task],
        available_agents=AVAILABLE_INVESTIGATOR_AGENTS,
        problem="Polygon intersection area returns 120.5 instead of 60.25",
        gathered_context={"relevant_files": [{"path": "src/geometry/clip.py"}]},
    )

    assert result.is_valid is False
    assert any("placeholder completion_criteria" in err.lower() for err in result.errors)


@pytest.mark.parametrize("placeholder", ["inspect", "check", "fix", "todo", "n/a"])
def test_placeholder_instructions_rejected(placeholder: str):
    """Verify that trivial or placeholder instructions are rejected by semantic validation."""
    task = _build_task()
    task["instructions"] = placeholder

    result = validate_investigation_plan(
        plan=[task],
        available_agents=AVAILABLE_INVESTIGATOR_AGENTS,
        problem="Polygon intersection area returns 120.5 instead of 60.25",
        gathered_context={"relevant_files": [{"path": "src/geometry/clip.py"}]},
    )

    assert result.is_valid is False
    assert any("placeholder instructions" in err.lower() for err in result.errors)


# =====================================================================
# 4. Strict planning/investigation boundary enforcement
# =====================================================================

def test_premature_root_cause_assertion_rejected():
    """Verify that tasks asserting unproven root causes as settled facts fail semantic validation."""
    task = _build_task(
        objective="The root cause is missing division by 2 in polygon area formula. Confirm this root cause.",
        instructions="Confirm that line 45 has the bug and fix the defect in clip.py",
    )

    result = validate_investigation_plan(
        plan=[task],
        available_agents=AVAILABLE_INVESTIGATOR_AGENTS,
        problem="Polygon intersection area returns 120.5 instead of 60.25",
        gathered_context={"relevant_files": [{"path": "src/geometry/clip.py"}]},
    )

    assert result.is_valid is False
    assert any("planning/investigation boundary" in err.lower() for err in result.errors)


def test_claimed_discovered_evidence_rejected():
    """Verify that tasks claiming evidence already discovered rather than describing what to collect fail validation."""
    task = _build_task(
        expected_output="Evidence already discovered: we have already found line 45 is broken",
    )

    result = validate_investigation_plan(
        plan=[task],
        available_agents=AVAILABLE_INVESTIGATOR_AGENTS,
        problem="Polygon intersection area returns 120.5 instead of 60.25",
        gathered_context={"relevant_files": [{"path": "src/geometry/clip.py"}]},
    )

    assert result.is_valid is False
    assert any("planning/investigation boundary" in err.lower() for err in result.errors)


# =====================================================================
# 5. Execution path coverage across architectural layers
# =====================================================================

def test_context_preparation_partitions_files_by_architectural_layer():
    """Verify structured planning context partitions candidate files into entry, service, and data layers."""
    gathered_context = {
        "relevant_files": [
            {"path": "src/api/routes/order_controller.py"},
            {"path": "src/services/pricing_service.py"},
            {"path": "src/db/repositories/order_repo.py"},
            {"path": "src/utils/helpers.py"},
        ]
    }

    ctx = InvestigationPlanner._prepare_structured_planning_context(
        problem="Order total calculates incorrectly",
        repository_summary={"primary_language": "Python"},
        gathered_context=gathered_context,
        current_state={},
    )

    paths = ctx.get("execution_path_candidates", {})
    entry_paths = [f["path"] for f in paths.get("entry_points_and_routes", [])]
    service_paths = [f["path"] for f in paths.get("business_logic_and_services", [])]
    data_paths = [f["path"] for f in paths.get("data_and_infrastructure", [])]

    assert any("order_controller" in p for p in entry_paths)
    assert any("pricing_service" in p for p in service_paths)
    assert any("order_repo" in p for p in data_paths)


# =====================================================================
# 6. LLM Self-Correction Retry Loop
# =====================================================================

def test_planner_retries_and_self_corrects_invalid_llm_plan():
    """Verify planner detects invalid initial LLM plan, triggers bounded retry with errors, and returns corrected plan."""
    mock_client = MagicMock()
    mock_structured = MagicMock()
    mock_client.with_structured_output.return_value = mock_structured

    # 1st call returns invalid plan (placeholder completion_criteria)
    invalid_step = MagicMock()
    invalid_step.model_dump.return_value = {
        "task_id": "T1",
        "assigned_agent": "code_investigator",
        "objective": "Inspect polygon clip logic",
        "reason": "Verify coordinates",
        "input": {},
        "scope": ["src/geometry/clip.py"],
        "instructions": "Inspect Sutherland-Hodgman algorithm in clip.py",
        "expected_output": "Algorithm statements",
        "completion_criteria": "todo",  # Invalid placeholder
        "priority": "high",
        "status": "pending",
    }
    invalid_plan = MagicMock()
    invalid_plan.steps = [invalid_step]

    # 2nd call (retry) returns corrected plan
    valid_step = MagicMock()
    valid_step.model_dump.return_value = {
        "task_id": "T1",
        "assigned_agent": "code_investigator",
        "objective": "Inspect polygon clip logic",
        "reason": "Verify coordinates",
        "input": {},
        "scope": ["src/geometry/clip.py"],
        "instructions": "Inspect Sutherland-Hodgman algorithm in clip.py",
        "expected_output": "Algorithm statements",
        "completion_criteria": "Coordinate intersection arithmetic verified against test points",
        "priority": "high",
        "status": "pending",
    }
    valid_plan = MagicMock()
    valid_plan.steps = [valid_step]

    mock_structured.invoke.side_effect = [invalid_plan, valid_plan]

    planner = InvestigationPlanner(llm_factory=lambda: mock_client)
    tasks = planner.plan(
        problem="Polygon intersection area returns 120.5 instead of 60.25",
        repository_summary={"primary_language": "Python"},
        gathered_context={"relevant_files": [{"path": "src/geometry/clip.py"}]},
        current_state={},
    )

    # Invoked twice (initial + retry)
    assert mock_structured.invoke.call_count == 2
    assert len(tasks) == 1
    assert tasks[0]["completion_criteria"] == "Coordinate intersection arithmetic verified against test points"
    assert tasks[0]["assigned_agent"] == "code_investigator"


def test_planner_falls_back_to_heuristics_when_retry_fails():
    """Verify that if LLM retry also fails validation, planner safely falls back to heuristic planner."""
    mock_client = MagicMock()
    mock_structured = MagicMock()
    mock_client.with_structured_output.return_value = mock_structured

    # LLM always returns invalid plan (placeholder completion_criteria)
    bad_step = MagicMock()
    bad_step.model_dump.return_value = {
        "task_id": "T1",
        "assigned_agent": "code_investigator",
        "objective": "Inspect polygon clip logic",
        "reason": "Verify coordinates",
        "input": {},
        "scope": ["src/geometry/clip.py"],
        "instructions": "Inspect Sutherland-Hodgman algorithm in clip.py",
        "expected_output": "Algorithm statements",
        "completion_criteria": "todo",
        "priority": "high",
        "status": "pending",
    }
    bad_plan = MagicMock()
    bad_plan.steps = [bad_step]
    mock_structured.invoke.return_value = bad_plan

    planner = InvestigationPlanner(llm_factory=lambda: mock_client)
    tasks = planner.plan(
        problem="Polygon intersection area calculation error returns 120.5 instead of 60.25",
        repository_summary={"primary_language": "Python"},
        gathered_context={"relevant_files": [{"path": "src/geometry/clip.py"}]},
        current_state={},
    )

    # Heuristic fallback returned valid tasks
    assert len(tasks) >= 1
    assert tasks[0]["assigned_agent"] == "code_investigator"
    assert tasks[0]["completion_criteria"] != "todo"
    val = validate_investigation_plan(
        tasks,
        problem="Polygon intersection area calculation error returns 120.5 instead of 60.25",
        gathered_context={"relevant_files": [{"path": "src/geometry/clip.py"}]},
    )
    assert val.is_valid is True


# =====================================================================
# 7. Broad discovery and scope hygiene
# =====================================================================

def test_broad_discovery_assigned_when_no_files_found():
    """Verify that when context search has no file matches, broad discovery scope is assigned."""
    planner = InvestigationPlanner()
    tasks = planner.plan(
        problem="Memory leak observed in background worker processing stream events",
        repository_summary={"primary_language": "Go"},
        gathered_context={"relevant_files": []},
        current_state={},
    )

    assert len(tasks) > 0
    discovery_task = tasks[0]
    assert discovery_task["scope"] == ["*"]
    assert discovery_task["assigned_agent"] == "code_investigator"


def test_lockfile_only_code_investigation_warned():
    """Verify warning when code investigator is assigned solely to lockfiles."""
    task = _build_task(
        assigned_agent="code_investigator",
        scope=["package-lock.json"],
        objective="Inspect package lockfile for versions",
        instructions="Analyze lockfile hash integrity",
    )
    result = validate_investigation_plan(
        plan=[task],
        available_agents=AVAILABLE_INVESTIGATOR_AGENTS,
        problem="Application dependency version issue",
        gathered_context={"relevant_files": [{"path": "package-lock.json"}]},
    )

    assert any("lockfiles" in w.lower() for w in result.warnings)


# =====================================================================
# 8. Backward compatibility
# =====================================================================

def test_backward_compatibility_with_aliases_and_schema():
    """Verify all 10 required fields and legacy aliases exist on generated tasks."""
    planner = InvestigationPlanner()
    tasks = planner.plan(
        problem="Calculation bug in tax percentage calculation",
        repository_summary={"primary_language": "Python"},
        gathered_context={"relevant_files": [{"path": "src/billing/tax.py"}]},
        current_state={},
    )

    assert len(tasks) >= 1
    task = tasks[0]
    for required in (
        "task_id", "objective", "assigned_agent", "reason", "input",
        "scope", "instructions", "expected_output", "completion_criteria", "status"
    ):
        assert required in task

    # Compatibility aliases
    assert task.get("step_id") == task["task_id"]
    assert task.get("investigator") == task["assigned_agent"]
    assert task.get("target_files") == task["scope"]
    assert task.get("priority") is not None
