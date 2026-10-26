import pytest
from pathlib import Path
from unittest.mock import MagicMock
from app.agents.investigation_orchestrator import InvestigationOrchestrator
from app.agents.code_investigator import CodeInvestigator, _is_non_application_path
from app.agents.dependency_impact_investigator import DependencyImpactInvestigator, _is_non_application_path as dep_is_non_app
from app.agents.replanner import Replanner
from app.agents.plan_validator import validate_investigation_plan
from app.agents.investigator_contract import unique_evidence, create_hypothesis
from app.agents.hypothesis_manager import HypothesisManager
from app.graph.graph import inconclusive_result_node


def test_non_application_path_filtering():
    """Requirement 1 & 5: Ensure non-application paths (lockfiles, manifests, docs) are rejected."""
    non_app_paths = [
        "package-lock.json",
        "frontend/package-lock.json",
        "yarn.lock",
        "package.json",
        "backend/package.json",
        "README.md",
        "docs/guide.markdown",
        "LICENSE",
        "CONTRIBUTING.txt",
    ]
    app_paths = [
        "backend/src/models/taskModel.js",
        "backend/src/services/taskService.js",
        "frontend/src/components/TaskForm.jsx",
        "backend/src/routes/taskRoutes.js",
    ]
    for p in non_app_paths:
        assert _is_non_application_path(p) is True, f"Expected {p} to be filtered as non-application path"
        assert dep_is_non_app(p) is True, f"Expected {p} to be filtered as non-application path in dep investigator"

    for p in app_paths:
        assert _is_non_application_path(p) is False, f"Expected {p} to be treated as valid application path"
        assert dep_is_non_app(p) is False, f"Expected {p} to be treated as valid application path in dep investigator"


def test_unique_evidence_deduplication_across_kinds():
    """Requirement 5: Identical file, line, and snippet must not be assigned multiple evidence IDs."""
    raw_evidence = [
        {
            "source": "backend/src/models/taskModel.js",
            "line": 15,
            "snippet": "const stmt = db.prepare('INSERT INTO tasks ...');",
            "kind": "code_match",
        },
        {
            "source": "backend/src/models/taskModel.js",
            "line": 15,
            "snippet": "const stmt = db.prepare('INSERT INTO tasks ...');",
            "kind": "code_search_match",
        },
        {
            "source": "backend/src/models/taskModel.js",
            "line": 15,
            "snippet": "const stmt = db.prepare('INSERT INTO tasks ...');",
            "kind": "code_flow_reference",
        },
    ]
    deduped = unique_evidence(raw_evidence, limit=10)
    assert len(deduped) == 1, "Duplicate matches with different kinds must be deduplicated to a single item"


def test_code_investigator_does_not_generate_auth_hypothesis_for_unrelated_problem(tmp_path: Path):
    """Requirement 1 & 5: When investigating task insertion defects, do not fabricate 401/auth hypotheses."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    models_dir = workspace / "models"
    models_dir.mkdir()
    model_file = models_dir / "taskModel.js"
    model_file.write_text(
        "export function insertWithDue(title, description, status, dueDate, userId) {\n"
        "  const trimmed = title.trim();\n"
        "  return db.prepare('SELECT * FROM tasks WHERE id = ?').get(trimmed);\n"
        "}\n",
        encoding="utf-8",
    )

    investigator = CodeInvestigator(max_files=5, max_evidence=10)
    task_def = {
        "task_id": "TASK-CODE-001",
        "objective": "Verify parameter ordering and column mapping in insertWithDue",
        "target_files": ["models/taskModel.js"],
        "scope": ["models/taskModel.js"],
    }
    context = {
        "problem": "Task creation fails when creating tasks with due dates in taskModel.js. Investigate parameter ordering, column mapping, and database insertion in insertWithDue.",
        "symptoms": "SQLite constraint error or column value corruption when inserting due dates.",
        "original_problem": "Task creation fails when creating tasks with due dates in taskModel.js.",
        "target_files": ["models/taskModel.js"],
    }

    result = investigator.investigate(str(workspace), task_def, context)
    for hyp in result.get("hypotheses", []):
        text = f"{hyp.get('hypothesis_title', '')} {hyp.get('possible_cause', '')} {hyp.get('reasoning', '')}".lower()
        assert "401" not in text, "Unrelated task defect should not generate 401 Unauthorized hypothesis"
        assert "unauthorized" not in text, "Unrelated task defect should not generate unauthorized hypothesis"
        assert "login" not in text, "Unrelated task defect should not generate login hypothesis"


def test_replanner_prevents_duplicate_scopes_and_cleans_objective():
    """Requirement 2: Replanning prevents twin duplicate scopes and formats objectives grammatically."""
    replanner = Replanner(llm_factory=None)
    existing_plan = [
        {
            "task_id": "TASK-CODE-001",
            "assigned_agent": "code_investigator",
            "objective": "Verify initial code",
            "scope": ["models/taskModel.js", "models/db.js"],
            "status": "completed",
        }
    ]
    review_result = {
        "decision": "insufficient",
        "reasoning": "Need causal defect mechanism.",
        "unanswered_questions": [
            "What specific code logic causes the reported issue: 'Task creation fails when creating tasks with due dates'?",
            "What specific code logic causes the reported issue: 'Task creation fails when creating tasks with due dates in taskModel.js'?",
        ],
        "missing_evidence": [
            "Direct causal defect evidence addressing 'Task creation fails when creating tasks with due dates'."
        ],
    }
    gathered_context = {
        "relevant_files": [
            "models/taskModel.js",
            "models/db.js",
            "services/taskService.js",
            "routes/taskRoutes.js",
            "controllers/taskController.js",
        ]
    }
    shared_state = {
        "original_problem": "Task creation fails when creating tasks with due dates",
        "problem": "Task creation fails when creating tasks with due dates",
        "investigation_plan": existing_plan,
        "completed_tasks": existing_plan,
    }

    replan_tasks = replanner.replan(
        problem="Task creation fails when creating tasks with due dates",
        shared_state=shared_state,
        review_result=review_result,
        gathered_context=gathered_context,
    )

    assert len(replan_tasks) >= 1

    # Check objective phrasing grammar (no "Verify whether What")
    for t in replan_tasks:
        assert not t["objective"].startswith("Verify whether What"), f"Ungrammatical objective: {t['objective']}"
        assert t["status"] == "pending"

    # Check that scopes among new replan tasks do not collide with each other or existing completed tasks
    scopes = [tuple(sorted(t["scope"])) for t in replan_tasks]
    assert len(scopes) == len(set(scopes)), f"Duplicate scopes generated in replan cycle: {scopes}"


def test_inconclusive_result_node_never_promotes_irrelevant_hypothesis():
    """Requirement 4: Inconclusive diagnosis keeps root_cause inconclusive and ignores irrelevant hypotheses."""
    hyp_irrelevant = create_hypothesis(
        hypothesis_id="HYP-001",
        hypothesis_title="Cross-module credential propagation and lookup discrepancy",
        produced_by="Dependency Impact Investigator",
        possible_cause="Cross-module token claim mismatch causes 401 Unauthorized",
        evidence="App.jsx:2",
        reasoning="Authentication token mismatch",
        status="needs_validation",
    )
    state = {
        "problem": "Task creation fails when creating tasks with due dates in taskModel.js",
        "hypotheses": [hyp_irrelevant],
        "aggregated_hypotheses": [hyp_irrelevant],
        "evidence_pool": [{"evidence_id": "EV-001", "source": "taskService.js", "line": 1}],
        "review_result": {
            "decision": "inconclusive",
            "reasoning": "Candidate hypotheses do not address original reported problem.",
            "hypothesis_evaluations": [
                {
                    "hypothesis_id": "HYP-001",
                    "statement": "Cross-module token claim mismatch causes 401 Unauthorized",
                    "status": "unverified",
                    "is_relevant_to_problem": False,
                    "relevance_explanation": "Hypothesis addresses an unrelated subsystem rather than the reported problem domain",
                }
            ],
            "unanswered_questions": ["What specific code logic causes task creation failure?"],
            "missing_evidence": ["Direct causal defect evidence"],
            "established_by_evidence": [],
        },
    }

    result_state = inconclusive_result_node(state)
    diagnosis = result_state["diagnosis"]

    assert diagnosis["root_cause"].startswith("Inconclusive:"), (
        f"Root cause should be Inconclusive, got: {diagnosis['root_cause']}"
    )
    assert not diagnosis["root_cause"].startswith("Unverified hypothesis: Cross-module credential"), (
        "Irrelevant hypothesis must never be promoted to root cause in diagnosis"
    )
    assert result_state["is_conclusive"] is False
    assert result_state["workflow_status"] == "inconclusive"


def test_plan_validator_accepts_completed_and_unresolved_tasks_in_multi_iteration():
    """Requirement 6: PlanValidator allows valid lifecycle statuses without warning on multi-iteration plans."""
    plan = [
        {
            "task_id": "TASK-CODE-001",
            "assigned_agent": "code_investigator",
            "objective": "Inspect initial files",
            "instructions": "Inspect files",
            "scope": ["models/taskModel.js"],
            "status": "completed",
        },
        {
            "task_id": "TASK-REPLAN-002",
            "assigned_agent": "code_investigator",
            "objective": "Inspect follow up",
            "instructions": "Inspect callers",
            "scope": ["services/taskService.js"],
            "status": "unresolved",
        },
        {
            "task_id": "TASK-REPLAN-003",
            "assigned_agent": "code_investigator",
            "objective": "Inspect newly targeted files",
            "instructions": "Inspect db queries",
            "scope": ["models/db.js"],
            "status": "pending",
        },
    ]
    context = {"problem": "Task creation failure", "relevant_files": ["models/taskModel.js", "services/taskService.js", "models/db.js"]}
    result = validate_investigation_plan(plan=plan, problem="Task creation failure", gathered_context=context)

    # There should be no warnings regarding task status expecting 'pending'
    status_warnings = [w for w in (result.warnings or []) if "expected 'pending'" in w]
    assert len(status_warnings) == 0, f"Spurious status warnings found: {status_warnings}"
