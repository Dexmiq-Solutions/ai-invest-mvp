"""Tests for Phase 1: Intelligent Investigation Planning System.

Verifies:
1. Problem input -> Intelligent investigation plan -> Plan validation -> Plan persistence -> Task decomposition.
2. Problem-specific plans across different software problem categories.
3. Explicit uncertainty-aware planning for ambiguous problems.
4. Independent plan storage and retrieval via state and API.
5. Plan strategy validation separate from task schema validation.
6. Validation passes without requiring pre-existing evidence.
7. Task decomposition preserving plan objectives, specialist strategy, and 10-field schema.
8. Bounded fallback when LLM plan generation or validation fails.
9. Compatibility with existing specialists and graph execution.
"""

import pytest
import time
from unittest.mock import MagicMock
from fastapi.testclient import TestClient

from app.agents.plan_validator import (
    InvestigationPlan,
    ProblemUnderstanding,
    InvestigationObjective,
    InvestigationApproach,
    SpecialistStrategy,
    validate_plan_strategy,
    validate_investigation_plan,
    PlanValidationResult,
)
from app.agents.investigation_orchestrator import (
    InvestigationPlanner,
    InvestigationOrchestrator,
)
from app.graph.state import InvestigationState
from app.graph.graph import (
    create_investigation_plan_node,
    validate_investigation_plan_node,
)
from app.api import app, sessions


@pytest.fixture
def planner():
    return InvestigationPlanner()


@pytest.fixture
def orchestrator():
    return InvestigationOrchestrator()


def test_different_problem_categories_produce_different_plans(planner):
    """Verify different categories of problems produce meaningfully different plans."""
    # 1. Calculation / Math defect
    calc_plan = planner.create_investigation_plan(
        problem="Pricing discount calculates 10% instead of 15% in checkout cart",
        repository_summary={"languages": ["python"], "frameworks": ["fastapi"]},
        gathered_context={"relevant_files": [{"path": "services/pricing.py", "relevance": "high"}]},
        current_state={},
    )
    assert isinstance(calc_plan, InvestigationPlan)
    assert calc_plan.validation_status == "validated"
    assert "code_investigator" in calc_plan.specialist_strategy.selected_specialists
    assert "runtime_error_investigator" not in calc_plan.specialist_strategy.selected_specialists
    assert any("calculation" in obj.title.lower() or "arithmetic" in obj.title.lower() for obj in calc_plan.objectives)

    # 2. Authentication defect
    auth_plan = planner.create_investigation_plan(
        problem="HTTP 401 unauthorized returned on protected user profile route when bearer token is provided",
        repository_summary={"languages": ["python"], "frameworks": ["fastapi"]},
        gathered_context={"relevant_files": [{"path": "middleware/auth.py", "relevance": "high"}]},
        current_state={},
    )
    assert isinstance(auth_plan, InvestigationPlan)
    assert auth_plan.validation_status == "validated"
    assert any("auth" in obj.title.lower() or "token" in obj.title.lower() for obj in auth_plan.objectives)

    # 3. Runtime crash
    crash_plan = planner.create_investigation_plan(
        problem="Server crashes with IndexError: list index out of range in parse_log_entry",
        repository_summary={"languages": ["python"]},
        gathered_context={"relevant_files": [{"path": "parsers/log_parser.py", "relevance": "high"}]},
        current_state={"stack_trace": "File parsers/log_parser.py, line 42, in parse_log_entry\nIndexError: list index out of range"},
    )
    assert isinstance(crash_plan, InvestigationPlan)
    assert crash_plan.validation_status == "validated"
    assert "runtime_error_investigator" in crash_plan.specialist_strategy.selected_specialists
    assert any("runtime" in obj.title.lower() or "stack" in obj.title.lower() or "crash" in obj.title.lower() for obj in crash_plan.objectives)

    # Ensure plans for distinct problems have distinct objective IDs and content
    assert calc_plan.objectives[0].title != auth_plan.objectives[0].title


def test_ambiguous_problem_produces_uncertainty_aware_plan(planner):
    """Verify an ambiguous problem produces an explicit uncertainty-aware plan without fabricating facts."""
    ambiguous_problem = "The application sometimes behaves strangely after user logout"
    plan = planner.create_investigation_plan(
        problem=ambiguous_problem,
        repository_summary={"languages": ["python"]},
        gathered_context={},
        current_state={},
    )
    assert isinstance(plan, InvestigationPlan)
    assert plan.validation_status == "validated"
    # Key uncertainties must be documented
    uncertainties = plan.problem_understanding.uncertainties_and_unknowns
    assert len(uncertainties) > 0
    # Must have contingency approach
    assert plan.approach.contingency_plans != ""
    # Completion criteria must require evidence rather than assumptions
    assert any("evidence" in c.lower() for c in plan.completion_criteria)


def test_plan_stored_and_retrieved_independently_of_tasks(orchestrator):
    """Verify the plan is stored as a first-class artifact and retrievable independently of tasks."""
    state = {
        "problem": "Discount code returns 0% discount on cart items",
        "gathered_context": {"relevant_files": [{"path": "cart/pricing.py"}]},
        "repository_summary": {"languages": ["python"]},
        "session_id": "test_session_store_001",
    }
    tasks = orchestrator.create_initial_plan(state)
    assert isinstance(tasks, list)
    assert len(tasks) > 0

    # Verify first-class plan artifact exists in state
    assert "plan" in state
    plan_dict = state["plan"]
    assert isinstance(plan_dict, dict)
    assert plan_dict["plan_id"].startswith("PLAN-")
    assert "problem_understanding" in plan_dict
    assert "objectives" in plan_dict
    assert "specialist_strategy" in plan_dict
    assert "completion_criteria" in plan_dict
    assert state["plan_id"] == plan_dict["plan_id"]
    assert state["lifecycle_status"] == "validated"

    # Verify helper get_or_create_plan_artifact
    retrieved = orchestrator.get_or_create_plan_artifact(state)
    assert isinstance(retrieved, InvestigationPlan)
    assert retrieved.plan_id == plan_dict["plan_id"]


def test_plan_validation_rejects_premature_conclusions():
    """Verify plan validation rejects plans that assert unconfirmed root causes as facts."""
    invalid_plan = InvestigationPlan(
        plan_id="PLAN-BAD-001",
        session_id="sess_bad_001",
        problem_reference="Discount defect",
        problem_understanding=ProblemUnderstanding(
            original_problem="Discount defect",
            problem_summary="Discount defect",
            observed_symptoms=["Discount is 0%"],
        ),
        objectives=[
            InvestigationObjective(
                objective_id="OBJ-001",
                title="Root cause is confirmed to be line 15 in discount.py",
                relevance_to_symptoms="Symptoms are caused by this confirmed bug",
                possible_explanations=["Bug is proven to be the calculation operator"],
            )
        ],
        approach=InvestigationApproach(
            execution_paths_to_probe=["discount.py"],
        ),
        specialist_strategy=SpecialistStrategy(
            selected_specialists=["code_investigator"],
            specialist_responsibilities={"code_investigator": "Inspect code"},
        ),
        completion_criteria=["Code verified"],
    )

    result = validate_plan_strategy(invalid_plan, problem="Discount defect")
    assert not result.is_valid
    assert any("violates planning boundary" in e.lower() or "root cause" in e.lower() for e in result.errors)


def test_plan_validation_rejects_unjustified_runtime_specialist():
    """Verify plan validation rejects runtime_error_investigator when there are no runtime symptoms."""
    invalid_plan = InvestigationPlan(
        plan_id="PLAN-BAD-002",
        session_id="sess_bad_002",
        problem_reference="Static discount formula off by 5 percent",
        problem_understanding=ProblemUnderstanding(
            original_problem="Static discount formula off by 5 percent",
            problem_summary="Formula error",
            observed_symptoms=["Discount is 10% instead of 15%"],
        ),
        objectives=[
            InvestigationObjective(
                objective_id="OBJ-001",
                title="Verify formula calculation in pricing service",
                relevance_to_symptoms="Reported formula defect",
                possible_explanations=["Operator precedence mistake"],
            )
        ],
        approach=InvestigationApproach(
            execution_paths_to_probe=["pricing.py"],
        ),
        specialist_strategy=SpecialistStrategy(
            selected_specialists=["code_investigator", "runtime_error_investigator"],
            specialist_responsibilities={
                "code_investigator": "Inspect pricing",
                "runtime_error_investigator": "Analyze runtime crashes",
            },
        ),
        completion_criteria=["Formula verified"],
    )

    result = validate_plan_strategy(invalid_plan, problem="Static discount formula off by 5 percent")
    assert not result.is_valid
    assert any("runtime_error_investigator" in e.lower() for e in result.errors)


def test_valid_plan_passes_without_preexisting_evidence(planner):
    """Verify that a strategy plan passes validation when no evidence or findings exist yet."""
    plan = planner.create_investigation_plan(
        problem="User dashboard fails to render profile avatar after image upload",
        repository_summary={"languages": ["javascript", "python"]},
        gathered_context={"relevant_files": [{"path": "frontend/Avatar.jsx"}, {"path": "backend/upload.py"}]},
        current_state={},
    )
    val_res = validate_plan_strategy(plan, problem="User dashboard fails to render profile avatar after image upload")
    assert val_res.is_valid
    assert len(val_res.errors) == 0


def test_task_decomposition_preserves_objectives_and_specialists(planner):
    """Verify decomposed tasks preserve plan linkages (plan_id, objective_id) and 10-field schema."""
    plan = planner.create_investigation_plan(
        problem="HTTP 500 internal server error on checkout payment submission with traceback",
        repository_summary={"languages": ["python", "typescript"]},
        gathered_context={"relevant_files": [{"path": "backend/payment.py"}, {"path": "frontend/Checkout.tsx"}]},
        current_state={"stack_trace": "payment.py line 88: ZeroDivisionError: integer division by zero"},
    )
    tasks = planner.decompose_plan_to_tasks(
        plan=plan,
        problem="HTTP 500 internal server error on checkout payment submission with traceback",
        gathered_context={"relevant_files": [{"path": "backend/payment.py"}]},
        current_state={"stack_trace": "payment.py line 88"},
    )

    assert len(tasks) >= len(plan.objectives)
    for task in tasks:
        # 10 mandatory fields
        assert "task_id" in task
        assert "objective" in task
        assert "assigned_agent" in task
        assert "reason" in task
        assert "input" in task
        assert "scope" in task
        assert "instructions" in task
        assert "expected_output" in task
        assert "completion_criteria" in task
        assert "status" in task

        # Plan linkages
        assert task.get("plan_id") == plan.plan_id
        assert task.get("objective_id") is not None
        assert task.get("objective_id").startswith("OBJ-")

        # Aliases
        assert "step_id" in task
        assert "investigator" in task
        assert "target_files" in task
        assert "priority" in task


def test_graph_nodes_plan_first_execution():
    """Verify graph nodes create and validate the strategic plan artifact before specialist execution."""
    initial_state = {
        "problem": "API returns tasks but frontend tab shows empty list with blank status badge",
        "repository_summary": {"languages": ["typescript", "python"], "frameworks": ["fastapi", "react"]},
        "gathered_context": {
            "relevant_files": [{"path": "backend/routes/tasks.py"}, {"path": "frontend/TasksTab.tsx"}]
        },
        "session_id": "test_graph_session_001",
    }

    # Step 1: Create investigation plan node
    state_after_plan = create_investigation_plan_node(initial_state)
    assert "plan" in state_after_plan
    assert "plan_id" in state_after_plan
    assert state_after_plan["plan"]["plan_id"].startswith("PLAN-")
    assert state_after_plan["current_step"] == "create_investigation_plan"
    assert len(state_after_plan["investigation_plan"]) > 0

    # Step 2: Validate investigation plan node
    state_after_val = validate_investigation_plan_node(state_after_plan)
    assert "plan_validation" in state_after_val
    assert state_after_val["plan_validation"]["is_valid"] is True
    assert state_after_val["current_step"] == "validate_investigation_plan"


def test_api_endpoints_plan_and_artifact():
    """Verify /api/investigation/{session_id}/plan and /api/investigation/{session_id}/plan/artifact endpoints."""
    client = TestClient(app)
    sid = "test_api_plan_session_999"

    sessions[sid] = {
        "problem": "Cart total calculation ignores active discount coupon",
        "repository_summary": {"languages": ["python"]},
        "gathered_context": {"relevant_files": [{"path": "cart/pricing.py"}]},
        "session_id": sid,
    }

    # 1. Get or create plan endpoint
    res = client.get(f"/api/investigation/{sid}/plan")
    assert res.status_code == 200
    data = res.json()
    assert data["session_id"] == sid
    assert data["plan_id"].startswith("PLAN-")
    assert "plan" in data
    assert data["status"] == "PLAN_READY"
    assert len(data["investigation_plan"]) > 0

    # 2. Standalone plan artifact endpoint
    res_art = client.get(f"/api/investigation/{sid}/plan/artifact")
    assert res_art.status_code == 200
    data_art = res_art.json()
    assert data_art["session_id"] == sid
    assert data_art["plan_id"] == data["plan_id"]
    assert "plan" in data_art
    assert data_art["plan"]["problem_understanding"]["problem_summary"] != ""


def test_ui_contract_problem_understanding_and_specialist_selection(planner):
    """Verify issue 1, 2, and 3: Accurate symptoms, expected behavior, and no runtime investigator."""
    problem = "API returns tasks but frontend tab displays no tasks and status badge is empty"
    repo_summary = {"languages": ["typescript", "python"], "frameworks": ["fastapi", "react"]}
    gathered = {
        "relevant_files": [
            {"path": "frontend/components/TasksTab.tsx", "relevance": "high"},
            {"path": "frontend/components/StatusBadge.tsx", "relevance": "high"},
            {"path": "backend/routes/tasks.py", "relevance": "high"},
            {"path": "backend/models/task.py", "relevance": "medium"},
        ]
    }

    plan = planner.create_investigation_plan(
        problem=problem,
        repository_summary=repo_summary,
        gathered_context=gathered,
        current_state={},
    )

    # 1. Problem Understanding
    prob_und = plan.problem_understanding
    assert "returns" not in prob_und.observed_symptoms  # Must NOT be a single-word token
    assert any("displays no tasks" in s.lower() or "badge is empty" in s.lower() for s in prob_und.observed_symptoms)
    assert prob_und.expected_behavior != ""
    assert "frontend" in prob_und.expected_behavior.lower() or "display" in prob_und.expected_behavior.lower() or "render" in prob_und.expected_behavior.lower()

    # 2. Specialist Selection strictly justified
    selected = plan.specialist_strategy.selected_specialists
    assert "runtime_error_investigator" not in selected  # NO runtime crashes/exceptions reported!
    assert "code_investigator" in selected
    assert "dependency_impact_investigator" in selected  # UI-contract boundary requires tracing data flow

    # 3. Objectives are specific and explain relevance, code areas, and completion criteria
    assert len(plan.objectives) >= 2
    for obj in plan.objectives:
        assert len(obj.title) > 10
        assert obj.relevance_to_symptoms != ""
        assert len(obj.possible_explanations) > 0
        assert obj.completion_outcome != ""
        assert "confirmed" not in obj.title.lower()  # Boundary enforcement

    # 4. Plan strategy validation passes
    val = validate_plan_strategy(plan, problem=problem, gathered_context=gathered)
    assert val.is_valid
    assert len(val.errors) == 0


def test_targeted_file_scoping_in_task_decomposition(planner):
    """Verify issue 4: Tasks receive targeted layer-scoped files rather than dumping all repo files."""
    problem = "API returns tasks but frontend tab displays no tasks and status badge is empty"
    gathered = {
        "relevant_files": [
            {"path": "frontend/components/TasksTab.tsx"},
            {"path": "frontend/components/StatusBadge.tsx"},
            {"path": "backend/routes/tasks.py"},
            {"path": "backend/models/task.py"},
            {"path": "backend/database/connection.py"},
            {"path": "backend/config/settings.py"},
        ]
    }

    plan = planner.create_investigation_plan(
        problem=problem,
        repository_summary={"languages": ["typescript", "python"]},
        gathered_context=gathered,
        current_state={},
    )

    tasks = planner.decompose_plan_to_tasks(
        plan=plan,
        problem=problem,
        gathered_context=gathered,
        current_state={},
    )

    assert len(tasks) >= 2
    # Check that UI task is scoped to frontend files and not the full dump
    ui_tasks = [t for t in tasks if "frontend" in t["objective"].lower() or "badge" in t["objective"].lower() or "display" in t["objective"].lower()]
    assert len(ui_tasks) > 0
    for ut in ui_tasks:
        scope = ut.get("scope") or []
        # Frontend task scope must include frontend files and not contain db/settings
        assert any("TasksTab.tsx" in f or "StatusBadge.tsx" in f for f in scope)
        assert not any("connection.py" in f for f in scope)


def test_invalid_plan_strategy_blocks_tasks_with_unified_validation(orchestrator):
    """Verify issue 5 & 6: Invalid plan strategy blocks task creation and validation fields agree authoritatively."""
    state = {
        "problem": "Generic issue report",
        "gathered_context": {},
        "repository_summary": {},
        "session_id": "test_invalid_gate_session",
    }

    # Mock planner to return an invalid plan strategy (e.g., asserts confirmed root cause)
    invalid_plan = InvestigationPlan(
        plan_id="PLAN-INVALID-001",
        session_id="test_invalid_gate_session",
        problem_reference="Generic issue report",
        problem_understanding=ProblemUnderstanding(
            original_problem="Generic issue report",
            problem_summary="Generic issue report",
            observed_symptoms=["fails"],
        ),
        objectives=[
            InvestigationObjective(
                objective_id="OBJ-001",
                title="Root cause is confirmed as bug in logic",
                relevance_to_symptoms="Directly proven",
            )
        ],
        specialist_strategy=SpecialistStrategy(
            selected_specialists=["code_investigator"],
        ),
        completion_criteria=["Done"],
    )

    orchestrator.planner.create_investigation_plan = MagicMock(return_value=invalid_plan)

    tasks = orchestrator.create_initial_plan(state)

    # Tasks must NOT be created
    assert tasks == []
    assert state.get("investigation_plan") == []
    assert state.get("selected_investigators") == []
    assert state.get("lifecycle_status") == "invalid"

    # Both validation fields must agree authoritatively that the plan is invalid
    plan_val = state.get("plan_validation")
    strat_val = state.get("plan_strategy_validation")
    assert plan_val is not None
    assert strat_val is not None
    assert plan_val["is_valid"] is False
    assert strat_val["is_valid"] is False
    assert len(plan_val["errors"]) > 0


def test_api_blocks_execution_and_enforces_validation_consistency():
    """Verify issue 5, 6, and 7 via API: Both validations agree and execution is blocked when plan is invalid."""
    client = TestClient(app)
    sid = "test_api_invalid_block_session"

    # Create session with pre-existing invalid plan
    sessions[sid] = {
        "problem": "Calculation discrepancy",
        "lifecycle_status": "invalid",
        "plan_id": "PLAN-INVALID-999",
        "plan": {
            "plan_id": "PLAN-INVALID-999",
            "lifecycle_status": "invalid",
            "validation_status": "invalid",
            "problem_understanding": {"observed_symptoms": []},
            "objectives": [],
            "completion_criteria": [],
        },
        "investigation_plan": [],
        "session_id": sid,
    }

    # 1. Query plan endpoint: both plan_validation and plan_strategy_validation must report is_valid: False
    res = client.get(f"/api/investigation/{sid}/plan")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "PLAN_INVALID"
    assert data["lifecycle_status"] == "invalid"
    assert data["investigation_plan"] == []
    assert data["plan_validation"]["is_valid"] is False
    assert data["plan_strategy_validation"]["is_valid"] is False

    # 2. Attempt execution: dispatch must be blocked
    exec_res = client.post(f"/api/investigation/{sid}/execute")
    assert exec_res.status_code == 200
    exec_data = exec_res.json()
    assert exec_data["status"] == "INVESTIGATION_FAILED"
    assert exec_data["execution_status"] == "failed"
    assert exec_data["state"]["current_step"] == "plan_validation_failed"
    assert exec_data["state"]["investigation_plan"] == []

