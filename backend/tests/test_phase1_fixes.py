"""Focused unit and integration tests for Phase 1 fixes of the AI Software Issue Investigator MVP.

Covers:
1. Multi-line evidence extraction in CodeInvestigator (complete SQL prepared statement,
   accurate source location, single-line preservation, evidence provenance, deduplication).
2. Inconclusive diagnosis evidence selection in inconclusive_result_node (resolving candidate
   hypothesis supporting_evidence_ids, fallback preservation, avoiding arbitrary selection).
3. Task-budget accounting in InvestigationOrchestrator (separating Reviewer iteration limit
   from task execution budget, allowing follow-up tasks in subsequent iterations, strictly bounded execution).
"""

import pytest
from unittest.mock import MagicMock
from pathlib import Path

from app.agents.code_investigator import CodeInvestigator, _extract_multiline_statement
from app.graph.graph import inconclusive_result_node
from app.graph.state import InvestigationState
from app.agents.investigation_orchestrator import InvestigationOrchestrator


# ==============================================================================
# 1. Multi-Line Evidence Extraction Tests
# ==============================================================================

def test_extract_multiline_statement_captures_complete_sql_declaration():
    """Verify _extract_multiline_statement captures db.prepare and multiline SQL declaration."""
    lines = [
        "import db from './db.js';",
        "",
        "const insertWithDue = db.prepare(",
        "  'INSERT INTO tasks (title, description, status, due_date, user_id) VALUES (?, ?, ?, ?, ?)'",
        ");",
        "const insertPlain = db.prepare(",
        "  'INSERT INTO tasks (title, description, status, user_id) VALUES (?, ?, ?, ?)'",
        ");",
    ]

    # Test match on line 3 (index 2: const insertWithDue = db.prepare()
    start_line, stmt = _extract_multiline_statement(lines, 2)
    assert start_line == 3
    assert "const insertWithDue = db.prepare(" in stmt
    assert "INSERT INTO tasks (title, description, status, due_date, user_id) VALUES (?, ?, ?, ?, ?)" in stmt
    assert stmt.endswith(");")

    # Test backward expansion when match is on line 4 (index 3: 'INSERT INTO...')
    start_line_bwd, stmt_bwd = _extract_multiline_statement(lines, 3)
    assert start_line_bwd == 3
    assert "const insertWithDue = db.prepare(" in stmt_bwd
    assert "INSERT INTO tasks (title, description, status, due_date, user_id) VALUES (?, ?, ?, ?, ?)" in stmt_bwd


def test_extract_multiline_statement_preserves_single_line_statements():
    """Single-line statements must not consume neighboring lines."""
    lines = [
        "export function create(task) {",
        "  if (task.dueDate) {",
        "    return insertWithDue.run(task.title, task.description, task.status, task.userId, task.dueDate);",
        "  }",
        "  return insertPlain.run(task.title, task.description, task.status, task.userId);",
        "}",
    ]

    # Line 3 (index 2): single-line invocation
    start_line, stmt = _extract_multiline_statement(lines, 2)
    assert start_line == 3
    assert stmt == "return insertWithDue.run(task.title, task.description, task.status, task.userId, task.dueDate);"
    assert "\n" not in stmt


def test_code_investigator_extracts_multiline_sql_and_invocation(tmp_path: Path):
    """End-to-end CodeInvestigator test on TaskFlow taskModel.js code pattern."""
    models_dir = tmp_path / "models"
    models_dir.mkdir(parents=True)
    task_model = models_dir / "taskModel.js"
    task_model.write_text(
        "import db from './db.js';\n"
        "\n"
        "const insertWithDue = db.prepare(\n"
        "  'INSERT INTO tasks (title, description, status, due_date, user_id) VALUES (?, ?, ?, ?, ?)'\n"
        ");\n"
        "const insertPlain = db.prepare(\n"
        "  'INSERT INTO tasks (title, description, status, user_id) VALUES (?, ?, ?, ?)'\n"
        ");\n"
        "\n"
        "export function create(task) {\n"
        "  if (task.dueDate) {\n"
        "    return insertWithDue.run(task.title, task.description, task.status, task.userId, task.dueDate);\n"
        "  }\n"
        "  return insertPlain.run(task.title, task.description, task.status, task.userId);\n"
        "}\n",
        encoding="utf-8",
    )

    investigator = CodeInvestigator(max_files=5, max_evidence=10)
    task_dict = {
        "task_id": "TASK-CODE-001",
        "objective": "Verify parameter ordering in insertWithDue and tasks table column mapping",
        "scope": ["models/taskModel.js"],
        "target_files": ["models/taskModel.js"],
    }
    context = {
        "problem": "Task creation fails with due dates due to parameter mismatch in taskModel.js insertWithDue",
        "target_files": ["models/taskModel.js"],
    }

    result = investigator.investigate(str(tmp_path), task_dict, context)
    evidence = result.get("evidence", [])
    assert len(evidence) >= 1

    # Look for the multi-line statement evidence
    sql_ev = next((e for e in evidence if "insertWithDue" in e["snippet"] and "INSERT INTO" in e["snippet"]), None)
    assert sql_ev is not None, "Failed to capture multi-line SQL statement containing both declaration and SQL query"
    assert sql_ev["line"] == 3, f"Expected line 3 for start of statement, got {sql_ev['line']}"
    assert sql_ev["sql_type"] == "write_operation", f"Expected write_operation, got {sql_ev.get('sql_type')}"
    assert sql_ev["kind"] == "database_write_operation"
    assert "due_date" in sql_ev["snippet"]
    assert "user_id" in sql_ev["snippet"]

    # Deduplication check: verify no duplicate record for line 4 covering the exact same query
    matching_sql_records = [e for e in evidence if "INSERT INTO tasks (title, description, status, due_date, user_id)" in e["snippet"]]
    assert len(matching_sql_records) == 1, "Duplicate evidence records created for lines within the same multi-line statement"


# ==============================================================================
# 2. Inconclusive Diagnosis Evidence Selection Tests
# ==============================================================================

def test_inconclusive_diagnosis_resolves_candidate_hypothesis_evidence():
    """Inconclusive result must select supporting evidence linked to candidate hypothesis."""
    state = InvestigationState(
        session_id="test-session-inc-1",
        problem="Task creation fails when creating tasks with due dates in taskModel.js",
        original_problem="Task creation fails when creating tasks with due dates",
        workspace_path=".",
        evidence_pool=[
            {
                "evidence_id": "EV-CODE-001",
                "source": "backend/src/routes/taskRoutes.js",
                "line": 15,
                "snippet": "router.post('/', taskController.create);",
                "kind": "implementation_match",
            },
            {
                "evidence_id": "EV-CODE-002",
                "source": "backend/src/controllers/taskController.js",
                "line": 28,
                "snippet": "const task = await taskService.create(req.body);",
                "kind": "implementation_match",
            },
            {
                "evidence_id": "EV-CODE-003",
                "source": "backend/src/models/taskModel.js",
                "line": 3,
                "snippet": "const insertWithDue = db.prepare('INSERT INTO tasks (title, description, status, due_date, user_id) VALUES (?, ?, ?, ?, ?)');",
                "kind": "database_write_operation",
                "sql_type": "write_operation",
            },
            {
                "evidence_id": "EV-CODE-004",
                "source": "backend/src/models/taskModel.js",
                "line": 13,
                "snippet": "return insertWithDue.run(task.title, task.description, task.status, task.userId, task.dueDate);",
                "kind": "implementation_match",
            },
        ],
        aggregated_hypotheses=[
            {
                "hypothesis_id": "HYP-001",
                "statement": "Parameter order mismatch in taskModel.js: insertWithDue passes userId as 4th arg and dueDate as 5th arg, swapping due_date and user_id",
                "possible_cause": "Parameter ordering swap in insertWithDue.run",
                "status": "unverified",
                "supporting_evidence_ids": ["EV-CODE-003", "EV-CODE-004"],
            }
        ],
        review_result={
            "decision": "inconclusive",
            "reasoning": "Candidate hypothesis identified in taskModel.js but runtime execution confirmation pending.",
            "hypothesis_evaluations": [
                {
                    "hypothesis_id": "HYP-001",
                    "is_relevant_to_problem": True,
                }
            ],
            "unanswered_questions": ["What is the observed runtime error?"],
            "missing_evidence": ["Runtime verification logs."],
        },
    )

    result = inconclusive_result_node(state)
    diagnosis = result.get("diagnosis", {})
    supporting_ev = diagnosis.get("supporting_evidence", [])

    # Must resolve EV-CODE-003 and EV-CODE-004, NOT arbitrary EV-CODE-001 and EV-CODE-002
    supp_ids = [e.get("evidence_id") for e in supporting_ev]
    assert "EV-CODE-003" in supp_ids, "Expected hypothesis-linked EV-CODE-003 in supporting evidence"
    assert "EV-CODE-004" in supp_ids, "Expected hypothesis-linked EV-CODE-004 in supporting evidence"
    assert "EV-CODE-001" not in supp_ids, "Arbitrary EV-CODE-001 should not be selected when candidate hypothesis has linked evidence"
    assert "EV-CODE-002" not in supp_ids, "Arbitrary EV-CODE-002 should not be selected when candidate hypothesis has linked evidence"


def test_inconclusive_diagnosis_fallback_when_no_candidate_hypothesis_evidence():
    """Inconclusive result must preserve existing fallback when no hypothesis-linked evidence exists."""
    state = InvestigationState(
        session_id="test-session-inc-fallback",
        problem="Unknown defect",
        original_problem="Unknown defect",
        workspace_path=".",
        evidence_pool=[
            {
                "evidence_id": "EV-001",
                "source": "server.js",
                "line": 10,
                "snippet": "app.listen(3000);",
                "kind": "implementation_match",
            },
            {
                "evidence_id": "EV-002",
                "source": "db.js",
                "line": 5,
                "snippet": "const db = new Database();",
                "kind": "implementation_match",
            },
        ],
        aggregated_hypotheses=[],
        review_result={"decision": "inconclusive"},
    )

    result = inconclusive_result_node(state)
    diagnosis = result.get("diagnosis", {})
    supporting_ev = diagnosis.get("supporting_evidence", [])
    supp_ids = [e.get("evidence_id") for e in supporting_ev]

    assert supp_ids == ["EV-001", "EV-002"], "Fallback should select initial available evidence records"


# ==============================================================================
# 3. Task-Budget Accounting Tests
# ==============================================================================

def _make_task(task_id: str, agent: str = "code_investigator", problem: str = "Investigate task creation defect in taskModel.js"):
    return {
        "task_id": task_id,
        "step_id": task_id,
        "assigned_agent": agent,
        "investigator": agent,
        "objective": f"Inspect code for {task_id}",
        "reason": f"Investigate root cause of {problem}",
        "input": {"problem": problem},
        "scope": ["models/taskModel.js"],
        "instructions": f"Inspect code in models/taskModel.js for {task_id}",
        "expected_output": "Code inspection findings and evidence",
        "completion_criteria": "Findings identified or refuted",
        "status": "pending",
    }


def test_task_budget_allows_follow_up_tasks_across_iterations():
    """Reviewer/Re-planner iteration limit must be separated from individual task-execution budget."""
    orch = InvestigationOrchestrator(
        max_iterations=3,
        task_budget=10,
        max_tasks_per_iteration=3,
    )

    mock_agent = MagicMock()
    mock_agent.return_value = {
        "agent_name": "code_investigator",
        "status": "completed",
        "objective": "Inspect code",
        "findings": [{"statement": "Found code match"}],
        "evidence": [{"evidence_id": "EV-1", "source": "models/taskModel.js", "line": 1, "snippet": "code"}],
    }
    orch.register_investigator("code_investigator", mock_agent)

    problem = "Task creation fails with due dates due to parameter mismatch in taskModel.js"
    # Iteration 1: 2 initial tasks executed
    plan_iter1 = [
        _make_task("TASK-001", "code_investigator", problem),
        _make_task("TASK-002", "code_investigator", problem),
    ]
    res1 = orch.execute_plan_single_pass(
        plan=plan_iter1,
        state={"workspace_path": ".", "problem": problem},
        context={"relevant_files": ["models/taskModel.js"]},
    )
    assert len(res1["completed_tasks"]) == 2
    assert len(res1["skipped_tasks"]) == 0

    # Iteration 2: Re-planner appends 2 new follow-up tasks (total planned = 4)
    # In the old code (task_budget = max(max_iterations, 1) = 3), completed_tasks was already 2,
    # so TASK-003 ran (2+1=3) and TASK-004 was skipped with "Iteration budget reached".
    plan_iter2 = list(res1["investigation_plan"]) + [
        _make_task("TASK-003", "code_investigator", problem),
        _make_task("TASK-004", "code_investigator", problem),
    ]
    res2 = orch.execute_plan_single_pass(
        plan=plan_iter2,
        state=res1,
        context={"relevant_files": ["models/taskModel.js"]},
    )

    completed_ids_iter2 = [t["task_id"] for t in res2["completed_tasks"]]
    assert "TASK-003" in completed_ids_iter2, "TASK-003 should execute in iteration 2"
    assert "TASK-004" in completed_ids_iter2, "TASK-004 should execute in iteration 2 without budget starvation"
    assert len(res2["completed_tasks"]) == 4
    assert len(res2["skipped_tasks"]) == 0


def test_task_budget_bounds_execution_and_prevents_runaway():
    """Tasks exceeding the configured task budget must be cleanly skipped with explicit reasons."""
    orch = InvestigationOrchestrator(
        max_iterations=3,
        task_budget=3,
        max_tasks_per_iteration=5,
    )

    mock_agent = MagicMock()
    mock_agent.return_value = {
        "agent_name": "code_investigator",
        "status": "completed",
        "objective": "Inspect code",
        "findings": [],
        "evidence": [],
    }
    orch.register_investigator("code_investigator", mock_agent)

    problem = "Task creation fails with due dates due to parameter mismatch in taskModel.js"
    plan = [
        _make_task("TASK-1", "code_investigator", problem),
        _make_task("TASK-2", "code_investigator", problem),
        _make_task("TASK-3", "code_investigator", problem),
        _make_task("TASK-4", "code_investigator", problem),
        _make_task("TASK-5", "code_investigator", problem),
    ]

    res = orch.execute_plan_single_pass(
        plan=plan,
        state={"workspace_path": ".", "problem": problem},
        context={"relevant_files": ["models/taskModel.js"]},
    )
    completed_ids = [t["task_id"] for t in res["completed_tasks"]]
    skipped_ids = [t["task_id"] for t in res["skipped_tasks"]]

    assert len(completed_ids) == 3
    assert len(skipped_ids) == 2
    assert "TASK-4" in skipped_ids
    assert "TASK-5" in skipped_ids
    for sk in res["skipped_tasks"]:
        assert "budget reached" in sk["skip_reason"].lower()
