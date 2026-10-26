"""Tests for Phase 2: Initial Plan Creation, Task Schema & Test Logging.

Covers:
1. All ten required task fields and their types.
2. Initial plan generation from problem description and gathered context.
3. Planning independence from evidence candidates, producer-consumer pairs, and previous results.
4. Broad discovery task generation when initial keyword search results are sparse.
5. Correct specialist assignment and unique task IDs within a plan.
6. Empty initial hypotheses and removal of boilerplate hypothesis seeding.
7. Structured planning log events (plan_creation_start, plan_creation_complete, plan_creation_failed).
8. Test-result recording to session-output.json, preserving existing session data.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List
import pytest

from app.agents.investigation_orchestrator import (
    InvestigationOrchestrator,
    InvestigationPlanner,
    InvestigationTask,
    AVAILABLE_INVESTIGATORS,
)
from app.agents.hypothesis_manager import HypothesisManager
from app.agents.plan_validator import validate_investigation_plan
from app.test_session_recorder import record_test_run, get_default_session_output_path
from unittest.mock import MagicMock


REQUIRED_TASK_FIELDS = [
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
]


def test_ten_field_task_schema():
    """Verify that every generated investigation task strictly conforms to the 10-field schema."""
    planner = InvestigationPlanner()
    problem = "User receives 500 error on POST /api/checkout due to TypeError in paymentProcessor.js"
    gathered_context = {
        "problem_statement": problem,
        "relevant_files": [
            {"path": "src/services/paymentProcessor.js"},
            {"path": "src/routes/checkout.js"},
        ],
        "investigation_concepts": ["checkout", "paymentProcessor", "TypeError"],
    }
    repo_summary = {"primary_language": "JavaScript", "frameworks": ["express"]}

    tasks = planner.plan(
        problem=problem,
        repository_summary=repo_summary,
        gathered_context=gathered_context,
        current_state={},
    )

    assert len(tasks) > 0, "Planner should produce at least one task"
    seen_ids = set()

    for task in tasks:
        # Check all 10 fields exist
        for field in REQUIRED_TASK_FIELDS:
            assert field in task, f"Field '{field}' missing from task {task}"

        # Check types
        assert isinstance(task["task_id"], str) and task["task_id"], "task_id must be non-empty string"
        assert isinstance(task["objective"], str) and task["objective"], "objective must be non-empty string"
        assert isinstance(task["assigned_agent"], str) and task["assigned_agent"] in AVAILABLE_INVESTIGATORS, (
            f"assigned_agent must be one of {AVAILABLE_INVESTIGATORS}"
        )
        assert isinstance(task["reason"], str) and task["reason"], "reason must be non-empty string"
        assert isinstance(task["input"], dict), "input must be a dict"
        assert isinstance(task["scope"], list), "scope must be a list"
        assert isinstance(task["instructions"], str) and task["instructions"], "instructions must be non-empty string"
        assert isinstance(task["expected_output"], str) and task["expected_output"], "expected_output must be non-empty string"
        assert isinstance(task["completion_criteria"], str) and task["completion_criteria"], "completion_criteria must be non-empty string"
        assert task["status"] == "pending", f"initial status must be 'pending', got: {task['status']}"

        # Check task_id uniqueness
        assert task["task_id"] not in seen_ids, f"Duplicate task_id found: {task['task_id']}"
        seen_ids.add(task["task_id"])

        # Check backward compatibility aliases
        assert task.get("step_id") == task["task_id"]
        assert task.get("investigator") == task["assigned_agent"]
        assert task.get("target_files") == task["scope"]
        assert "priority" in task


def test_broad_discovery_task_when_search_sparse():
    """Verify that when initial keyword search finds no relevant files, broad discovery tasks (scope=['*']) are generated."""
    planner = InvestigationPlanner()
    problem = "Intermittent auth timeout when validating session cookies under heavy load"
    
    # Sparse context with no relevant files found
    gathered_context = {
        "problem_statement": problem,
        "relevant_files": [],
        "investigation_concepts": ["session", "cookies", "auth", "timeout"],
        "context_status": "sufficient",
    }
    repo_summary = {"primary_language": "Python"}

    tasks = planner.plan(
        problem=problem,
        repository_summary=repo_summary,
        gathered_context=gathered_context,
        current_state={},
    )

    assert len(tasks) > 0, "Broad discovery tasks should be produced when no files match"
    discovery_task = tasks[0]
    assert discovery_task["assigned_agent"] == "code_investigator"
    assert discovery_task["scope"] == ["*"], f"Broad discovery task scope must be ['*'], got {discovery_task['scope']}"
    assert "broad" in discovery_task["reason"].lower() or "exploration" in discovery_task["reason"].lower()
    assert discovery_task["status"] == "pending"


def test_planning_independence_from_prohibited_context():
    """Verify that planning does not depend on prohibited evidence candidates, producer-consumer pairs, or hypotheses."""
    planner = InvestigationPlanner()
    problem = "Discount calculation typo gives 50% discount instead of 5%"

    # Context containing prohibited legacy structures
    gathered_context = {
        "problem_statement": problem,
        "relevant_files": [{"path": "src/pricing/discount.py"}],
        "investigation_concepts": ["discount", "calculation", "typo"],
        # Prohibited items that must be ignored
        "evidence_candidates": [{"snippet": "fake_evidence"}],
        "producer_consumer_pairs": [{"consumer_file": "src/legacy/consumer.py"}],
        "write_read_pairs": [{"query_file": "src/legacy/db.py"}],
        "unresolved_questions": ["What is 5%?"],
        "hypotheses": [{"hypothesis": "pre_seeded"}],
    }

    tasks = planner.plan(
        problem=problem,
        repository_summary={"languages": ["Python"]},
        gathered_context=gathered_context,
        current_state={},
    )

    assert len(tasks) > 0
    # The generated task targets discount.py, completely ignoring producer_consumer_pairs query_file
    for task in tasks:
        assert "src/legacy" not in str(task.get("scope", []))
        assert "src/pricing/discount.py" in task.get("scope", [])


def test_specialist_assignment_and_unique_task_ids():
    """Verify correct specialist assignment based on signals and strictly unique task IDs."""
    planner = InvestigationPlanner()
    problem = (
        "Server crash with unhandled NullPointerException in UserService.java at line 42 "
        "when calling external AuthService API client"
    )
    gathered_context = {
        "problem_statement": problem,
        "relevant_files": [
            {"path": "src/main/java/UserService.java"},
            {"path": "src/main/java/AuthClient.java"},
        ],
        "investigation_concepts": ["NullPointerException", "UserService", "AuthService"],
    }
    repo_summary = {"languages": ["Java"], "frameworks": ["spring"]}

    tasks = planner.plan(
        problem=problem,
        repository_summary=repo_summary,
        gathered_context=gathered_context,
        current_state={"stack_trace": "java.lang.NullPointerException at UserService.java:42"},
    )

    assigned_agents = {t["assigned_agent"] for t in tasks}
    assert "runtime_error_investigator" in assigned_agents, "Runtime crash should trigger runtime_error_investigator"
    assert "code_investigator" in assigned_agents or "dependency_impact_investigator" in assigned_agents

    task_ids = [t["task_id"] for t in tasks]
    assert len(task_ids) == len(set(task_ids)), f"Task IDs must be unique: {task_ids}"


def test_empty_initial_hypotheses_and_no_boilerplate_seeding():
    """Verify that HypothesisManager boilerplate generation is removed and initial hypotheses are initialized to empty list."""
    # Verify HypothesisManager class has no generate_initial_hypotheses method
    manager = HypothesisManager()
    assert not hasattr(manager, "generate_initial_hypotheses"), (
        "generate_initial_hypotheses must be removed from HypothesisManager"
    )

    # In-memory tracking works cleanly
    assert manager.get_hypotheses() == []
    manager.add_hypothesis({"id": "H1", "statement": "Test hypothesis"})
    assert len(manager.get_hypotheses()) == 1
    manager.reset()
    assert manager.get_hypotheses() == []

    # Verify Orchestrator investigate() initializes hypotheses to empty list
    orchestrator = InvestigationOrchestrator(max_iterations=1)
    res = orchestrator.investigate(
        investigation_task="",
        workspace_path="/nonexistent/test",
    )
    assert res.get("hypotheses") == [], "Initial hypotheses must be an empty list"


def test_planning_structured_logging(caplog):
    """Verify that create_initial_plan logs plan_creation_start, plan_creation_complete, and plan_creation_failed."""
    caplog.set_level(logging.INFO)
    orchestrator = InvestigationOrchestrator(max_iterations=1)
    
    state = {
        "session_id": "test-session-1234",
        "problem": "Runtime crash on /checkout route",
        "gathered_context": {
            "relevant_files": [{"path": "routes/checkout.py"}],
        },
        "repository_summary": {"primary_language": "Python"},
    }

    plan = orchestrator.create_initial_plan(state)
    assert len(plan) > 0

    log_messages = [rec.message for rec in caplog.records]
    assert any("plan_creation_start" in m and "test-session-1234" in m for m in log_messages), (
        "Expected plan_creation_start with session_id in logs"
    )
    assert any("plan_creation_complete" in m and "task_count=" in m and "task_ids=" in m for m in log_messages), (
        "Expected plan_creation_complete with task counts and task IDs in logs"
    )

    # Verify failure logging
    class BrokenPlanner:
        def plan(self, *args, **kwargs):
            raise RuntimeError("Planner engine failure")

    orchestrator.planner = BrokenPlanner()
    with pytest.raises(RuntimeError):
        orchestrator.create_initial_plan(state)

    failure_messages = [rec.message for rec in caplog.records]
    assert any("plan_creation_failed" in m and "test-session-1234" in m for m in failure_messages), (
        "Expected plan_creation_failed with session_id in logs"
    )


def test_session_output_logging_structure(tmp_path: Path):
    """Verify test-result recording to session-output.json, preserving existing data and recording failed/passed runs."""
    dummy_session_file = tmp_path / "session-output.json"
    initial_content = {
        "session_id": "04c0edbf-b417-4c1c-9504-305048aae4d7",
        "state": {
            "problem": "Existing problem data",
            "current_step": "investigation_completed",
        },
        "status": "investigation_completed",
    }
    dummy_session_file.write_text(json.dumps(initial_content, indent=2), encoding="utf-8")

    # Record passing test run
    entry1 = record_test_run(
        session_file_path=dummy_session_file,
        phase="phase_2",
        phase_name="Initial Plan Creation, Task Schema & Test Logging",
        command="pytest backend/tests/test_wf_planning.py",
        test_files=["backend/tests/test_wf_planning.py"],
        total_tests=7,
        passed=7,
        failed=0,
        duration_seconds=0.85,
        exit_code=0,
    )
    assert entry1["status"] == "passed"
    assert entry1["session_id"] == "04c0edbf-b417-4c1c-9504-305048aae4d7"

    # Record failed test run
    entry2 = record_test_run(
        session_file_path=dummy_session_file,
        phase="phase_2",
        phase_name="Initial Plan Creation, Task Schema & Test Logging",
        command="pytest backend/tests/test_wf_planning.py -k test_failing",
        test_files=["backend/tests/test_wf_planning.py"],
        total_tests=1,
        passed=0,
        failed=1,
        duration_seconds=0.32,
        exit_code=1,
        failure_details=[{"name": "test_failing", "error": "AssertionError"}],
    )
    assert entry2["status"] == "failed"

    # Verify preserved original data
    with open(dummy_session_file, "r", encoding="utf-8") as f:
        saved_data = json.load(f)

    assert saved_data["session_id"] == "04c0edbf-b417-4c1c-9504-305048aae4d7"
    assert saved_data["state"]["problem"] == "Existing problem data"
    assert saved_data["status"] == "investigation_completed"
    assert len(saved_data["test_runs"]) == 2
    assert saved_data["test_runs"][0]["status"] == "passed"
    assert saved_data["test_runs"][1]["status"] == "failed"


def test_prepare_structured_planning_context_integrity():
    """Verify that _prepare_structured_planning_context preserves complete paths and symbols,
    excludes raw file_contents, and respects bounded limits without arbitrary string cuts.
    """
    planner = InvestigationPlanner()
    problem = "500 Internal Server Error in orderProcessing checkout pipeline"

    relevant_files = [
        {
            "path": f"src/modules/subsystem/checkout_processor_{i}.ts",
            "relevance": "High",
            "score": 0.95 - (i * 0.02),
            "matched_concepts": [f"concept_{i}", "checkout"],
        }
        for i in range(25)
    ]

    related_components = [f"CheckoutServiceComponent_{i}" for i in range(30)]
    technical_concepts = [f"order_processing_rule_{i}" for i in range(35)]
    stack_trace = "\n".join([f"  at OrderProcessor.step{i} (processor.ts:{10 + i})" for i in range(50)])

    gathered_context = {
        "problem_statement": problem,
        "relevant_files": relevant_files,
        "related_components": related_components,
        "investigation_concepts": technical_concepts,
        "file_contents": {
            "src/modules/subsystem/checkout_processor_0.ts": "A" * 50000,
            "src/modules/subsystem/checkout_processor_1.ts": "B" * 50000,
        },
    }

    repo_summary = {
        "primary_language": "TypeScript",
        "frameworks": ["express", "typeorm"],
        "entry_points": ["src/index.ts"],
        "configuration_files": ["tsconfig.json", "package.json"],
    }

    current_state = {
        "stack_trace": stack_trace,
        "error_status_codes": [500, 502],
        "investigation_findings": [{"finding": f"Finding {i}"} for i in range(20)],
    }

    prepared = planner._prepare_structured_planning_context(
        problem=problem,
        repository_summary=repo_summary,
        gathered_context=gathered_context,
        current_state=current_state,
    )

    # 1. Raw file_contents must NOT be leaked into prepared context
    assert "file_contents" not in prepared

    # 2. Complete file paths and structured fields preserved (budgeted to top 15)
    assert len(prepared["relevant_files"]) == 15
    for idx, rf in enumerate(prepared["relevant_files"]):
        assert isinstance(rf, dict)
        assert rf["path"] == f"src/modules/subsystem/checkout_processor_{idx}.ts"
        assert rf["relevance"] == "High"
        assert "matched_concepts" in rf
        # Complete path preserved without truncation
        assert not rf["path"].endswith("...")
        assert rf["path"].startswith("src/modules/")

    # 3. Related components bounded to top 20 complete symbols
    assert len(prepared["related_components"]) == 20
    assert prepared["related_components"][0] == "CheckoutServiceComponent_0"
    assert prepared["related_components"][-1] == "CheckoutServiceComponent_19"

    # 4. Technical concepts bounded to top 25 complete terms
    assert len(prepared["technical_concepts"]) == 25
    assert prepared["technical_concepts"][0] == "order_processing_rule_0"

    # 5. Stack trace bounded to top 30 lines
    st_lines = prepared["diagnostics"]["stack_trace"].splitlines()
    assert len(st_lines) <= 30

    # 6. Repository summary fields preserved
    assert prepared["repository_summary"]["primary_language"] == "TypeScript"
    assert "tsconfig.json" in prepared["repository_summary"]["configuration_files"]

    # 7. Valid JSON serialization succeeds without truncation
    serialized = json.dumps(prepared)
    assert json.loads(serialized) == prepared


def test_prepare_structured_planning_context_safe_with_empty_or_malformed():
    """Verify safe handling of missing, None, or unexpected context types."""
    planner = InvestigationPlanner()

    # None context inputs
    prepared_empty = planner._prepare_structured_planning_context(
        problem="Test issue",
        repository_summary=None,
        gathered_context=None,
        current_state=None,
    )
    assert prepared_empty["problem"] == "Test issue"
    assert prepared_empty["relevant_files"] == []
    assert prepared_empty["related_components"] == []
    assert prepared_empty["technical_concepts"] == []

    # Malformed types
    prepared_malformed = planner._prepare_structured_planning_context(
        problem="",
        repository_summary="not a dict",
        gathered_context={"relevant_files": "not a list", "related_components": 123},
        current_state="not a dict",
    )
    assert isinstance(prepared_malformed["relevant_files"], list)
    assert isinstance(prepared_malformed["related_components"], list)


def test_plan_with_mock_llm_factory_success():
    """Verify end-to-end plan generation when LLM factory is configured and produces valid plan."""
    mock_client = MagicMock()
    mock_structured_llm = MagicMock()
    mock_client.with_structured_output.return_value = mock_structured_llm

    fake_step = {
        "task_id": "T1",
        "investigator": "code_investigator",
        "assigned_agent": "code_investigator",
        "task": "Inspect payment route for null check error",
        "objective": "Inspect payment route for null check error",
        "rationale": "Verify null check logic in payment controller",
        "reason": "Verify null check logic in payment controller",
        "input": {"focus": "payment"},
        "scope": ["src/services/paymentProcessor.js"],
        "instructions": "Locate line throwing TypeError and inspect payload validation",
        "expected_evidence": "Identified null pointer check deficiency",
        "expected_output": "Identified null pointer check deficiency",
        "completion_criteria": "Found missing null guard",
        "priority": "high",
        "status": "pending",
    }
    mock_plan = MagicMock()
    mock_step = MagicMock()
    mock_step.model_dump.return_value = fake_step
    mock_plan.steps = [mock_step]
    mock_structured_llm.invoke.return_value = mock_plan

    planner = InvestigationPlanner(llm_factory=lambda: mock_client)

    gathered_context = {
        "problem_statement": "TypeError in paymentProcessor.js",
        "relevant_files": [{"path": "src/services/paymentProcessor.js"}],
        "investigation_concepts": ["paymentProcessor", "TypeError"],
    }
    repo_summary = {"primary_language": "JavaScript"}

    tasks = planner.plan(
        problem="TypeError in paymentProcessor.js",
        repository_summary=repo_summary,
        gathered_context=gathered_context,
        current_state={},
    )

    assert len(tasks) == 1
    assert tasks[0]["task_id"] == "T1"
    assert tasks[0]["assigned_agent"] == "code_investigator"
    assert tasks[0]["scope"] == ["src/services/paymentProcessor.js"]

    # Verify that mock_structured_llm.invoke received a prompt with clean structured JSON
    mock_structured_llm.invoke.assert_called_once()
    prompt = mock_structured_llm.invoke.call_args[0][0]
    assert "src/services/paymentProcessor.js" in prompt

    # Validate generated plan passes plan_validator
    val_result = validate_investigation_plan(
        plan=tasks,
        problem="TypeError in paymentProcessor.js",
        gathered_context=gathered_context,
    )
    assert val_result.is_valid, f"Validation failed: {val_result.errors}"


def test_plan_with_llm_fallback_on_exception():
    """Verify graceful fallback to heuristic planning when LLM call throws an exception."""
    mock_client = MagicMock()
    mock_client.invoke.side_effect = RuntimeError("OpenRouter 503 Service Unavailable")

    planner = InvestigationPlanner(llm_factory=lambda: mock_client)

    gathered_context = {
        "problem_statement": "Runtime crash on checkout",
        "relevant_files": [{"path": "src/routes/checkout.js"}],
        "investigation_concepts": ["checkout", "crash"],
    }

    tasks = planner.plan(
        problem="Runtime crash on checkout",
        repository_summary={"primary_language": "JavaScript"},
        gathered_context=gathered_context,
        current_state={"stack_trace": "Error at checkout.js:10"},
    )

    # Must fall back gracefully to heuristic planner
    assert len(tasks) > 0
    for field in REQUIRED_TASK_FIELDS:
        assert field in tasks[0]


def test_plan_with_llm_fallback_on_invalid_json():
    """Verify graceful fallback to heuristic planning when LLM returns non-JSON text."""
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.content = "Sorry, I cannot generate an investigation plan for this request."
    mock_client.invoke.return_value = mock_response

    planner = InvestigationPlanner(llm_factory=lambda: mock_client)

    gathered_context = {
        "problem_statement": "TypeError in paymentProcessor.js",
        "relevant_files": [{"path": "src/services/paymentProcessor.js"}],
        "investigation_concepts": ["paymentProcessor"],
    }

    tasks = planner.plan(
        problem="TypeError in paymentProcessor.js",
        repository_summary={"primary_language": "JavaScript"},
        gathered_context=gathered_context,
        current_state={},
    )

    assert len(tasks) > 0
    for field in REQUIRED_TASK_FIELDS:
        assert field in tasks[0]


def test_plan_fallback_when_factory_returns_none():
    """Verify that when llm_factory returns None (unconfigured API key), heuristic planning is used."""
    planner = InvestigationPlanner(llm_factory=lambda: None)

    tasks = planner.plan(
        problem="Database deadlock during user creation",
        repository_summary={"primary_language": "Python"},
        gathered_context={"relevant_files": [{"path": "src/db/users.py"}]},
        current_state={},
    )

    assert len(tasks) > 0
    assert tasks[0]["assigned_agent"] in AVAILABLE_INVESTIGATORS


def test_llm_planning_dynamic_specialist_selection():
    """Verify adaptive specialist selection: selects only the agents needed for the problem."""
    # Scenario A: Pure calculation / logic problem -> ONLY code_investigator
    mock_client_a = MagicMock()
    mock_structured_a = MagicMock()
    mock_client_a.with_structured_output.return_value = mock_structured_a
    mock_plan_a = MagicMock()
    step_a = MagicMock()
    step_a.model_dump.return_value = {
        "task_id": "T-CALC-1",
        "assigned_agent": "code_investigator",
        "objective": "Inspect discount formula in discount.py",
        "reason": "Verify calculation logic",
        "input": {"focus": "discount"},
        "scope": ["src/pricing/discount.py"],
        "instructions": "Inspect rate calculation in discount.py to verify multiplier",
        "expected_output": "Exact calculation line identified",
        "completion_criteria": "Found line calculating discount",
        "priority": "high",
        "status": "pending",
    }
    mock_plan_a.steps = [step_a]
    mock_structured_a.invoke.return_value = mock_plan_a

    planner_a = InvestigationPlanner(llm_factory=lambda: mock_client_a)
    plan_a = planner_a.plan(
        problem="Discount calculation typo gives 50% discount instead of 5%",
        repository_summary={"primary_language": "Python"},
        gathered_context={"relevant_files": [{"path": "src/pricing/discount.py"}]},
        current_state={},
    )
    agents_a = {t["assigned_agent"] for t in plan_a}
    assert agents_a == {"code_investigator"}, f"Expected only code_investigator, got {agents_a}"
    val_a = validate_investigation_plan(plan_a, problem="Discount calculation typo gives 50% discount instead of 5%")
    assert val_a.is_valid, f"Plan validation failed: {val_a.errors}"

    # Scenario B: Unhandled crash with stack trace -> includes runtime_error_investigator
    mock_client_b = MagicMock()
    mock_structured_b = MagicMock()
    mock_client_b.with_structured_output.return_value = mock_structured_b
    mock_plan_b = MagicMock()
    step_b1 = MagicMock()
    step_b1.model_dump.return_value = {
        "task_id": "T-RT-1",
        "assigned_agent": "runtime_error_investigator",
        "objective": "Parse stack trace to identify crash location in UserService.java",
        "reason": "Map crash frames",
        "input": {"stack_trace": "NullPointerException at line 42"},
        "scope": ["src/main/java/UserService.java"],
        "instructions": "Examine line 42 for dereference",
        "expected_output": "Frame localized",
        "completion_criteria": "Identified crash point",
        "priority": "critical",
        "status": "pending",
    }
    step_b2 = MagicMock()
    step_b2.model_dump.return_value = {
        "task_id": "T-CODE-1",
        "assigned_agent": "code_investigator",
        "objective": "Inspect UserService.java null check handling",
        "reason": "Check null guard",
        "input": {},
        "scope": ["src/main/java/UserService.java"],
        "instructions": "Inspect line 42 and check variable initialization",
        "expected_output": "Missing guard found",
        "completion_criteria": "Verified null check presence",
        "priority": "high",
        "status": "pending",
    }
    mock_plan_b.steps = [step_b1, step_b2]
    mock_structured_b.invoke.return_value = mock_plan_b

    planner_b = InvestigationPlanner(llm_factory=lambda: mock_client_b)
    plan_b = planner_b.plan(
        problem="Server crash with unhandled NullPointerException in UserService.java at line 42",
        repository_summary={"languages": ["Java"]},
        gathered_context={"relevant_files": [{"path": "src/main/java/UserService.java"}]},
        current_state={"stack_trace": "NullPointerException at UserService.java:42"},
    )
    agents_b = {t["assigned_agent"] for t in plan_b}
    assert "runtime_error_investigator" in agents_b
    val_b = validate_investigation_plan(plan_b, problem="Server crash with unhandled NullPointerException in UserService.java at line 42")
    assert val_b.is_valid, f"Plan validation failed: {val_b.errors}"

    # Scenario C: Cross-module token mismatch -> includes dependency_impact_investigator
    mock_client_c = MagicMock()
    mock_structured_c = MagicMock()
    mock_client_c.with_structured_output.return_value = mock_structured_c
    mock_plan_c = MagicMock()
    step_c1 = MagicMock()
    step_c1.model_dump.return_value = {
        "task_id": "T-DEP-1",
        "assigned_agent": "dependency_impact_investigator",
        "objective": "Trace token propagation from auth middleware to billing route",
        "reason": "Verify cross-module token handover",
        "input": {},
        "scope": ["src/middleware/auth.js", "src/routes/billing.js"],
        "instructions": "Trace headers passed from auth middleware to route handler",
        "expected_output": "Caller flow identified",
        "completion_criteria": "Mapped token propagation",
        "priority": "high",
        "status": "pending",
    }
    mock_plan_c.steps = [step_c1]
    mock_structured_c.invoke.return_value = mock_plan_c

    planner_c = InvestigationPlanner(llm_factory=lambda: mock_client_c)
    plan_c = planner_c.plan(
        problem="Bearer token stripped by auth middleware before reaching billing route",
        repository_summary={"languages": ["JavaScript"]},
        gathered_context={"relevant_files": [{"path": "src/middleware/auth.js"}, {"path": "src/routes/billing.js"}]},
        current_state={},
    )
    agents_c = {t["assigned_agent"] for t in plan_c}
    assert "dependency_impact_investigator" in agents_c
    val_c = validate_investigation_plan(plan_c, problem="Bearer token stripped by auth middleware before reaching billing route")
    assert val_c.is_valid, f"Plan validation failed: {val_c.errors}"


def test_llm_planning_enforces_strict_hypothesis_boundary():
    """Verify that tasks describe what to inspect/verify and never assert unconfirmed root causes or fabricate evidence."""
    mock_client = MagicMock()
    mock_structured = MagicMock()
    mock_client.with_structured_output.return_value = mock_structured
    mock_plan = MagicMock()

    # Pre-mature assertions in model output
    step1 = MagicMock()
    step1.model_dump.return_value = {
        "task_id": "T-ASSERT-1",
        "assigned_agent": "code_investigator",
        "objective": "The bug is caused by missing return statement in calculateDiscount. Confirm this root cause.",
        "reason": "Verify defect",
        "input": {},
        "scope": ["src/pricing/discount.py"],
        "instructions": "Confirm that discount calculation is broken in discount.py. Fix the bug in calculateDiscount.",
        "expected_output": "Defect verified",
        "completion_criteria": "Found bug",
        "priority": "high",
        "status": "pending",
    }
    step2 = MagicMock()
    step2.model_dump.return_value = {
        "task_id": "T-ASSERT-2",
        "assigned_agent": "runtime_error_investigator",
        "objective": "Root cause is unhandled null pointer exception at line 42. Confirm this defect.",
        "reason": "Confirm defect",
        "input": {},
        "scope": ["src/services/auth.py"],
        "instructions": "Confirmed that the token parser crashes. Fix the issue in auth.py.",
        "expected_output": "Defect verified",
        "completion_criteria": "Fixed bug",
        "priority": "high",
        "status": "pending",
    }
    mock_plan.steps = [step1, step2]
    mock_structured.invoke.return_value = mock_plan

    state_before = {
        "problem": "Calculation discrepancy in discount",
        "findings": [],
        "evidence": [],
        "hypotheses": [],
    }

    planner = InvestigationPlanner(llm_factory=lambda: mock_client)
    plan = planner.plan(
        problem="Calculation discrepancy in discount",
        repository_summary={"primary_language": "Python"},
        gathered_context={"relevant_files": [{"path": "src/pricing/discount.py"}, {"path": "src/services/auth.py"}]},
        current_state=state_before,
    )

    assert len(plan) == 2

    for task in plan:
        obj = task["objective"].lower()
        inst = task["instructions"].lower()

        # Must NOT assert root cause as established fact
        assert not obj.startswith("the bug is caused by"), f"Objective asserts premature cause: {task['objective']}"
        assert not obj.startswith("root cause is"), f"Objective asserts premature cause: {task['objective']}"
        assert not obj.startswith("confirm that"), f"Objective asserts premature confirmation: {task['objective']}"
        assert not inst.startswith("confirmed that"), f"Instructions assert premature confirmation: {task['instructions']}"
        assert not inst.startswith("fix the"), f"Instructions assert premature fix: {task['instructions']}"

        # Objectives must be neutralized into investigative inquiries
        assert any(obj.startswith(prefix) for prefix in ["inspect", "verify", "investigate", "examine", "analyze"]), (
            f"Objective should be neutral inquiry: {task['objective']}"
        )

    # Boundary check: planning must NOT inject fabricated evidence, findings, or hypotheses
    assert state_before["findings"] == [], "Planner must not fabricate findings"
    assert state_before["evidence"] == [], "Planner must not fabricate evidence"
    assert state_before["hypotheses"] == [], "Planner must not fabricate hypotheses"

    # Plan validation passes
    val = validate_investigation_plan(plan, problem="Calculation discrepancy in discount")
    assert val.is_valid, f"Plan validation failed: {val.errors}"


def test_llm_planning_deduplicates_redundant_tasks():
    """Verify that redundant tasks with identical specialist, scope, and instructions are deduplicated."""
    mock_client = MagicMock()
    mock_structured = MagicMock()
    mock_client.with_structured_output.return_value = mock_structured
    mock_plan = MagicMock()

    # Two identical steps and one distinct step
    step1 = MagicMock()
    step1.model_dump.return_value = {
        "task_id": "T1",
        "assigned_agent": "code_investigator",
        "objective": "Inspect checkout route handler",
        "reason": "Examine handler",
        "input": {},
        "scope": ["src/routes/checkout.js"],
        "instructions": "Inspect checkout route handler logic for input validation",
        "expected_output": "Validation logic reviewed",
        "completion_criteria": "Handler examined",
        "priority": "high",
        "status": "pending",
    }
    step2 = MagicMock()
    step2.model_dump.return_value = {
        "task_id": "T2",
        "assigned_agent": "code_investigator",
        "objective": "Inspect checkout route handler",
        "reason": "Examine handler again",
        "input": {},
        "scope": ["src/routes/checkout.js"],
        "instructions": "Inspect checkout route handler logic for input validation",
        "expected_output": "Validation logic reviewed",
        "completion_criteria": "Handler examined",
        "priority": "high",
        "status": "pending",
    }
    step3 = MagicMock()
    step3.model_dump.return_value = {
        "task_id": "T3",
        "assigned_agent": "dependency_impact_investigator",
        "objective": "Trace callers of checkout handler",
        "reason": "Check callers",
        "input": {},
        "scope": ["src/routes/checkout.js"],
        "instructions": "Trace imported service dependencies in checkout route",
        "expected_output": "Caller chain mapped",
        "completion_criteria": "Dependencies traced",
        "priority": "medium",
        "status": "pending",
    }
    mock_plan.steps = [step1, step2, step3]
    mock_structured.invoke.return_value = mock_plan

    planner = InvestigationPlanner(llm_factory=lambda: mock_client)
    plan = planner.plan(
        problem="Error in checkout route",
        repository_summary={"languages": ["JavaScript"]},
        gathered_context={"relevant_files": [{"path": "src/routes/checkout.js"}]},
        current_state={},
    )

    # Redundant duplicate should be pruned
    assert len(plan) == 2, f"Expected 2 tasks after deduplication, got {len(plan)}"
    agents = [t["assigned_agent"] for t in plan]
    assert agents == ["code_investigator", "dependency_impact_investigator"]


def test_llm_planning_parses_various_output_formats():
    """Verify that planner successfully parses dicts, JSON strings, and markdown blocks from LLMs."""
    # Subtest 1: LLM returns direct JSON string
    raw_json = json.dumps({
        "steps": [
            {
                "task_id": "T-JSON-1",
                "assigned_agent": "code_investigator",
                "objective": "Inspect payment route",
                "reason": "Check logic",
                "scope": ["src/payment.js"],
                "instructions": "Inspect payment logic in payment.js",
                "expected_output": "Logic checked",
                "completion_criteria": "Reviewed",
            }
        ]
    })
    mock_client1 = MagicMock()
    mock_client1.with_structured_output.side_effect = Exception("Not supported")
    mock_client1.invoke.return_value = raw_json

    planner1 = InvestigationPlanner(llm_factory=lambda: mock_client1)
    plan1 = planner1.plan(
        problem="TypeError in payment.js",
        repository_summary={},
        gathered_context={"relevant_files": [{"path": "src/payment.js"}]},
        current_state={},
    )
    assert len(plan1) == 1
    assert plan1[0]["assigned_agent"] == "code_investigator"

    # Subtest 2: LLM returns markdown fenced JSON block
    raw_markdown = f"```json\n{raw_json}\n```"
    mock_client2 = MagicMock()
    mock_client2.with_structured_output.side_effect = Exception("Not supported")
    mock_response = MagicMock()
    mock_response.content = raw_markdown
    mock_client2.invoke.return_value = mock_response

    planner2 = InvestigationPlanner(llm_factory=lambda: mock_client2)
    plan2 = planner2.plan(
        problem="TypeError in payment.js",
        repository_summary={},
        gathered_context={"relevant_files": [{"path": "src/payment.js"}]},
        current_state={},
    )
    assert len(plan2) == 1
    assert plan2[0]["assigned_agent"] == "code_investigator"


# ============================================================
# Phase 3: Generalization & Anti-Hardcoding Regression Tests
# ============================================================

def test_heuristic_planning_novel_domain_problems():
    """Verify heuristic fallback creates valid plans for novel problem domains without hardcoded keywords."""
    planner = InvestigationPlanner(llm_factory=None)

    # 1. Computational domain: inventory stock math
    plan_stock = planner.plan(
        problem="Stock quantity computation produces negative available balance on item allocation",
        repository_summary={"languages": ["Python"]},
        gathered_context={"relevant_files": [{"path": "src/inventory/stock.py"}]},
        current_state={},
    )
    assert len(plan_stock) == 1
    assert plan_stock[0]["assigned_agent"] == "code_investigator"
    assert "arithmetic" in plan_stock[0]["objective"].lower() or "calculation" in plan_stock[0]["instructions"].lower()
    val_res = validate_investigation_plan(plan_stock)
    assert val_res.is_valid, f"Validation failed: {val_res.errors}"

    # 2. Runtime error domain: HTTP 502 with traceback
    plan_gateway = planner.plan(
        problem="Webhook relay crashes with HTTP 502 Bad Gateway during event dispatch",
        repository_summary={"languages": ["Go"]},
        gathered_context={"relevant_files": [{"path": "pkg/webhook/relay.go"}]},
        current_state={
            "errors": ["HTTP 502 Bad Gateway: connection reset by peer"],
            "stack_trace": "panic: runtime error: invalid memory address or nil pointer dereference\ngoroutine 1 [running]:\npkg/webhook/relay.go:45",
        },
    )
    agents_gw = {t["assigned_agent"] for t in plan_gateway}
    assert "runtime_error_investigator" in agents_gw
    val_gw = validate_investigation_plan(plan_gateway)
    assert val_gw.is_valid, f"Validation failed: {val_gw.errors}"

    # 3. Access control domain: 403 Forbidden with security guard
    plan_sec = planner.plan(
        problem="Tenant isolation guard returns 403 Forbidden on document export",
        repository_summary={"languages": ["TypeScript"]},
        gathered_context={"relevant_files": [
            {"path": "src/middleware/tenantGuard.ts"},
            {"path": "src/services/documentExport.ts"},
        ]},
        current_state={},
    )
    agents_sec = {t["assigned_agent"] for t in plan_sec}
    assert "dependency_impact_investigator" in agents_sec
    assert "code_investigator" in agents_sec
    val_sec = validate_investigation_plan(plan_sec)
    assert val_sec.is_valid, f"Validation failed: {val_sec.errors}"

    # 4. Data persistence query domain: repository record matching
    plan_db = planner.plan(
        problem="Database query returns zero records when searching active orders with status filter",
        repository_summary={"languages": ["Python"]},
        gathered_context={"relevant_files": [
            {"path": "src/repositories/order_repo.py"},
            {"path": "src/services/order_service.py"},
        ]},
        current_state={},
    )
    agents_db = {t["assigned_agent"] for t in plan_db}
    assert "code_investigator" in agents_db
    # Task objective must reference actual model/repository file
    assert any("order_repo" in t["objective"] or "order_repo" in t["question"] for t in plan_db)
    val_db = validate_investigation_plan(plan_db)
    assert val_db.is_valid, f"Validation failed: {val_db.errors}"


def test_heuristic_planning_sparse_context_handles_gracefully():
    """Verify that when no target files exist, planner emits broad discovery tasks without fabricating paths."""
    planner = InvestigationPlanner(llm_factory=None)
    plan = planner.plan(
        problem="Intermittent cache eviction desync across cluster worker nodes",
        repository_summary={"languages": ["Java"], "frameworks": ["Spring"]},
        gathered_context={
            "relevant_files": [],
            "investigation_concepts": ["cache", "eviction", "cluster"],
        },
        current_state={},
    )
    assert len(plan) >= 1
    assert plan[0]["assigned_agent"] == "code_investigator"
    assert plan[0]["scope"] == ["*"]
    # Scope must NOT contain fabricated non-existent file names
    for task in plan:
        for f in task["scope"]:
            assert f == "*" or not f.startswith("<")
    val = validate_investigation_plan(plan)
    assert val.is_valid, f"Validation failed: {val.errors}"


def test_heuristic_planning_does_not_assert_unconfirmed_causes():
    """Verify fallback plans describe investigation probes, not definitive conclusions."""
    planner = InvestigationPlanner(llm_factory=None)
    plan = planner.plan(
        problem="Billing invoice tax roundoff discrepancy on currency conversion",
        repository_summary={},
        gathered_context={"relevant_files": [{"path": "src/finance/invoice.py"}]},
        current_state={},
    )
    for task in plan:
        obj = task["objective"].lower()
        inst = task["instructions"].lower()
        assert not obj.startswith("the bug is")
        assert not obj.startswith("root cause is")
        assert not inst.startswith("fix the")
        # Must describe verification/inspection
        assert any(w in obj for w in ("verify", "inspect", "analyze", "trace", "discover", "check"))

