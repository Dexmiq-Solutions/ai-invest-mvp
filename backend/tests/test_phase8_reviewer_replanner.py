"""Tests for Phase 8 — Reviewer & Re-planning.

Validates:
1. Reviewer receives and evaluates all hypotheses and evidence from shared state.
2. Insufficient evidence triggers Re-planner.
3. Re-planner creates only targeted additional tasks without replacing previous tasks.
4. Investigation workflow executes new tasks and updates shared state without losing previous findings.
5. Reviewer re-evaluates updated shared state.
6. Sufficient evidence routes directly to preliminary root-cause result.
7. Reaching the investigation limit produces an inconclusive result.
8. Phase 1-7 workflow invariants are fully preserved.
"""

import pytest
from unittest.mock import MagicMock

from app.agents.reviewer import Reviewer
from app.agents.replanner import Replanner
from app.graph.graph import (
    reviewer_node,
    route_after_reviewer,
    replanner_node,
    root_cause_result_node,
    inconclusive_result_node,
    build_graph,
)


# ============================================================
# 1. Reviewer Unit Tests
# ============================================================

def test_reviewer_receives_and_evaluates_all_hypotheses_and_evidence():
    reviewer = Reviewer()

    problem = "User receives 401 Unauthorized during valid login flow"
    evidence = [
        {
            "evidence_id": "EV-001",
            "source": "backend/auth.py",
            "line": 42,
            "snippet": "if token != expected_token: return 401",
            "relationship_to_hypothesis": "observational",
        },
        {
            "evidence_id": "EV-002",
            "source": "backend/client.py",
            "line": 15,
            "snippet": "headers['Authorization'] = f'Bearer {token}'",
            "relationship_to_hypothesis": "observational",
        },
    ]
    findings = [
        {"statement": "Login endpoint returns 401 on token validation check", "category": "runtime_rejection"}
    ]
    hypotheses = [
        {
            "hypothesis_id": "HYP-001",
            "hypothesis_title": "Token validation mismatch in auth middleware",
            "statement": "Auth middleware checks token header with case-sensitive mismatch",
            "supporting_evidence_ids": ["EV-001"],
            "contradicting_evidence_ids": [],
            "status": "unverified",
            "confidence": "low",
        }
    ]

    result = reviewer.review(
        problem=problem,
        evidence=evidence,
        findings=findings,
        hypotheses=hypotheses,
        iteration=1,
        max_iterations=3,
    )

    assert "decision" in result
    assert "reasoning" in result
    assert "identified_symptoms" in result
    assert "hypothesis_evaluations" in result
    assert len(result["hypothesis_evaluations"]) == 1
    assert result["hypothesis_evaluations"][0]["hypothesis_id"] == "HYP-001"
    assert "EV-001" in result["hypothesis_evaluations"][0]["supporting_evidence_ids"]
    assert result["decision"] == "insufficient"
    assert result["further_investigation_necessary"] is True


def test_reviewer_detects_sufficient_evidence():
    reviewer = Reviewer()

    problem = "App crashes on start due to missing environment variable"
    evidence = [
        {
            "evidence_id": "EV-CAUSAL-01",
            "source": "backend/config.py",
            "line": 28,
            "snippet": "DATABASE_URL = os.environ['DATABASE_URL'] # raises KeyError if missing",
            "relationship_to_hypothesis": "causal",
            "is_defect_evidence": True,
        }
    ]
    findings = [
        {
            "statement": "KeyError in config.py: DATABASE_URL is accessed directly with os.environ[] without default",
            "category": "root_cause_defect",
        }
    ]
    hypotheses = [
        {
            "hypothesis_id": "HYP-001",
            "statement": "Missing default value for DATABASE_URL in config.py raises KeyError on launch",
            "supporting_evidence_ids": ["EV-CAUSAL-01"],
            "contradicting_evidence_ids": [],
            "status": "verified",
            "confidence": "high",
        }
    ]

    result = reviewer.review(
        problem=problem,
        evidence=evidence,
        findings=findings,
        hypotheses=hypotheses,
        iteration=1,
        max_iterations=3,
    )

    assert result["decision"] == "sufficient"
    assert result["is_sufficient"] is True
    assert result["further_investigation_necessary"] is False


def test_reviewer_marks_inconclusive_when_max_iterations_reached():
    reviewer = Reviewer()

    result = reviewer.review(
        problem="Intermittent network timeout",
        evidence=[],
        findings=[],
        hypotheses=[],
        iteration=3,
        max_iterations=3,
    )

    assert result["decision"] == "inconclusive"
    assert result["further_investigation_necessary"] is False


# ============================================================
# 2. Re-planner Unit Tests
# ============================================================

def test_replanner_creates_targeted_non_redundant_tasks():
    replanner = Replanner()

    shared_state = {
        "completed_tasks": [
            {
                "task_id": "TASK-001",
                "assigned_agent": "code_investigator",
                "task": "Inspect login form frontend",
            }
        ],
        "completed_investigators": ["code_investigator"],
        "investigation_plan": [
            {"task_id": "TASK-001", "assigned_agent": "code_investigator"}
        ],
    }
    review_result = {
        "decision": "insufficient",
        "reasoning": "Unverified cross-component caller flow or middleware invocation",
        "unanswered_questions": ["Is auth middleware properly registered on the router?"],
        "missing_evidence": ["Route definitions registering auth middleware"],
        "feedback": "Trace caller hierarchy and middleware registration",
    }
    gathered_context = {
        "relevant_files": ["backend/middleware/auth.py", "backend/routes/user.py"]
    }

    new_tasks = replanner.replan(
        problem="401 error on user profile",
        shared_state=shared_state,
        review_result=review_result,
        gathered_context=gathered_context,
    )

    assert len(new_tasks) >= 1
    # Check that new tasks have valid schema and do not duplicate TASK-001
    for task in new_tasks:
        assert task["task_id"] != "TASK-001"
        assert task["assigned_agent"] in ("code_investigator", "dependency_impact_investigator", "runtime_error_investigator")
        assert task["objective"]
        assert task["instructions"]
        assert task["status"] == "pending"


# ============================================================
# 3. Routing Unit Tests
# ============================================================

def test_route_after_reviewer_routes_to_root_cause_when_sufficient():
    state = {
        "review_result": {"decision": "sufficient", "is_sufficient": True},
        "investigation_iterations": 1,
        "max_iterations": 3,
    }
    assert route_after_reviewer(state) == "root_cause_result"


def test_route_after_reviewer_routes_to_replanner_when_insufficient():
    state = {
        "review_result": {"decision": "insufficient", "is_sufficient": False},
        "investigation_iterations": 1,
        "max_iterations": 3,
    }
    assert route_after_reviewer(state) == "replanner"


def test_route_after_reviewer_routes_to_inconclusive_when_limit_reached():
    state = {
        "review_result": {"decision": "insufficient", "is_sufficient": False},
        "investigation_iterations": 3,
        "max_iterations": 3,
    }
    assert route_after_reviewer(state) == "inconclusive_result"


# ============================================================
# 4. Phase 8 Node Tests
# ============================================================

def test_reviewer_node_updates_state():
    state = {
        "problem": "Sample issue",
        "investigation_iterations": 1,
        "max_iterations": 3,
        "evidence_pool": [],
        "aggregated_findings": [],
        "aggregated_hypotheses": [],
    }
    updated = reviewer_node(state)
    assert "review_result" in updated
    assert "review_feedback" in updated
    assert updated["current_step"] == "reviewer"


def test_replanner_node_appends_tasks_and_increments_iteration():
    state = {
        "problem": "Sample issue",
        "investigation_iterations": 1,
        "max_iterations": 3,
        "investigation_plan": [{"task_id": "TASK-001", "assigned_agent": "code_investigator"}],
        "completed_tasks": [{"task_id": "TASK-001", "assigned_agent": "code_investigator"}],
        "review_result": {
            "decision": "insufficient",
            "feedback": "Need to trace middleware caller flow",
            "unanswered_questions": ["Where is auth called?"],
        },
        "gathered_context": {"relevant_files": ["server.py"]},
    }
    updated = replanner_node(state)
    assert len(updated["investigation_plan"]) > 1
    assert updated["investigation_plan"][0]["task_id"] == "TASK-001"
    assert updated["investigation_iterations"] == 2
    assert len(updated["investigation_history"]) == 1
    assert updated["current_step"] == "replanned"


def test_root_cause_result_node_produces_preliminary_result():
    state = {
        "problem": "TypeError in data parser",
        "aggregated_hypotheses": [
            {
                "hypothesis_id": "HYP-001",
                "statement": "Missing check for None input in parse_data()",
                "status": "supported",
                "supporting_evidence_ids": ["EV-001"],
                "confidence": "high",
            }
        ],
        "evidence_pool": [
            {
                "evidence_id": "EV-001",
                "source": "parser.py",
                "line": 12,
                "snippet": "data.strip() without null check",
                "is_defect_evidence": True,
            }
        ],
        "review_result": {
            "decision": "sufficient",
            "is_sufficient": True,
            "confidence": "high",
            "reasoning": "parser.py line 12 directly causes TypeError on None input",
        },
    }
    updated = root_cause_result_node(state)

    assert updated["is_conclusive"] is True
    assert updated["status_label"] == "Likely root cause"
    assert "parse_data" in updated["likely_root_cause"]
    assert updated["diagnosis"]["root_cause"] == updated["likely_root_cause"]
    assert len(updated["diagnosis"]["supporting_evidence"]) >= 1
    assert updated["diagnosis"]["confidence"] == "high"
    assert updated["suggested_fix"] == ""
    assert updated["verification_steps"] == []
    assert updated["current_step"] == "investigation_completed"


def test_inconclusive_result_node_produces_inconclusive_result():
    state = {
        "problem": "Random crash",
        "review_result": {
            "decision": "inconclusive",
            "reasoning": "Could not reproduce or isolate defect after maximum iterations",
            "unanswered_questions": ["Which thread crashed?"],
            "missing_evidence": ["Crash dump"],
        },
        "investigation_iterations": 3,
        "max_iterations": 3,
    }
    updated = inconclusive_result_node(state)

    assert updated["is_conclusive"] is False
    assert updated["status_label"] == "Inconclusive"
    assert updated["investigation_status"] == "inconclusive"
    assert "inconclusive" in updated["likely_root_cause"].lower() or "unverified" in updated["likely_root_cause"].lower()
    assert updated["suggested_fix"] == ""
    assert updated["verification_steps"] == []
    assert updated["current_step"] == "investigation_completed"


# ============================================================
# 5. Graph Integration Tests
# ============================================================

def test_build_graph_contains_phase8_nodes():
    graph = build_graph()
    nodes = set(graph.nodes.keys())
    assert "reviewer" in nodes
    assert "replanner" in nodes
    assert "root_cause_result" in nodes
    assert "inconclusive_result" in nodes


def test_graph_sufficient_evidence_reaches_root_cause_result(monkeypatch):
    monkeypatch.setattr("app.graph.graph.validate_input", lambda s: {**s, "current_step": "input_validated", "validation_errors": []})
    monkeypatch.setattr("app.graph.graph.check_context", lambda s: {**s, "context_status": "sufficient", "current_step": "context_checked"})
    monkeypatch.setattr("app.graph.graph.setup_workspace", lambda s: {**s, "workspace_path": "d:/mock/ws", "current_step": "workspace_ready"})
    monkeypatch.setattr("app.graph.graph.analyze_repository", lambda p: {"project_structure": {"file_count": 5}})
    monkeypatch.setattr("app.graph.graph.context_gathering_graph_node", lambda s: {**s, "gathered_context": {"relevant_files": ["server.py"]}, "current_step": "context_gathered"})
    monkeypatch.setattr(
        "app.graph.graph.create_investigation_plan_node",
        lambda s: {
            **s,
            "plan": {"plan_id": "PLAN-001", "objectives": [{"objective_id": "OBJ-001", "objective": "Verify error"}]},
            "plan_id": "PLAN-001",
            "lifecycle_status": "validated",
            "investigation_plan": s.get("investigation_plan") or [{"task_id": "TASK-001", "assigned_agent": "code_investigator", "status": "pending"}],
            "selected_investigators": ["code_investigator"],
            "current_step": "create_investigation_plan",
        },
    )
    monkeypatch.setattr(
        "app.graph.graph.validate_investigation_plan_node",
        lambda s: {
            **s,
            "plan_validation": {"is_valid": True, "errors": [], "warnings": []},
            "lifecycle_status": "execution-ready",
            "current_step": "validate_investigation_plan",
        },
    )

    graph = build_graph()

    def mock_execute(self, plan, state, context):
        return {
            "execution_status": "completed",
            "workflow_status": "completed",
            "evidence_pool": [
                {
                    "evidence_id": "EV-001",
                    "source": "server.py",
                    "snippet": "port = int(env['PORT']) # causes ValueError if string is invalid",
                    "is_defect_evidence": True,
                    "relationship_to_hypothesis": "causal",
                }
            ],
            "aggregated_findings": [
                {"statement": "server.py line 10 crashes on invalid port format", "category": "root_cause_defect"}
            ],
            "aggregated_hypotheses": [
                {
                    "hypothesis_id": "HYP-001",
                    "statement": "Port parsing without error handling crashes server",
                    "supporting_evidence_ids": ["EV-001"],
                    "status": "verified",
                    "confidence": "high",
                }
            ],
            "completed_tasks": [{"task_id": "TASK-001", "assigned_agent": "code_investigator"}],
            "task_results": {"TASK-001": {"evidence": [{"evidence_id": "EV-001"}]}},
        }

    from app.agents.investigation_orchestrator import InvestigationOrchestrator
    monkeypatch.setattr(InvestigationOrchestrator, "execute_plan_single_pass", mock_execute)

    input_state = {
        "problem": "Server crashes on startup when port is invalid",
        "expected_behavior": "Server falls back to port 8080 or logs clear error",
        "actual_behavior": "Server crashes with unhandled ValueError",
        "workspace_path": "d:/mock/ws",
        "investigation_plan": [{"task_id": "TASK-001", "assigned_agent": "code_investigator", "status": "pending"}],
        "lifecycle_status": "execution-ready",
        "plan_validation": {"is_valid": True, "errors": [], "warnings": []},
        "llm_factory": lambda: None,
    }

    result = graph.invoke(input_state)

    assert result["current_step"] == "investigation_completed"
    assert result["is_conclusive"] is True
    assert result["status_label"] == "Likely root cause"
    assert "likely_root_cause" in result
    assert result["diagnosis"]["root_cause"] != ""
    assert result["suggested_fix"] == ""
    assert result["verification_steps"] == []


def test_graph_insufficient_evidence_loops_and_terminates_at_limit(monkeypatch):
    monkeypatch.setattr("app.graph.graph.validate_input", lambda s: {**s, "current_step": "input_validated", "validation_errors": []})
    monkeypatch.setattr("app.graph.graph.check_context", lambda s: {**s, "context_status": "sufficient", "current_step": "context_checked"})
    monkeypatch.setattr("app.graph.graph.setup_workspace", lambda s: {**s, "workspace_path": "d:/mock/ws", "current_step": "workspace_ready"})
    monkeypatch.setattr("app.graph.graph.analyze_repository", lambda p: {"project_structure": {"file_count": 5}})
    monkeypatch.setattr("app.graph.graph.context_gathering_graph_node", lambda s: {**s, "gathered_context": {"relevant_files": ["server.py"]}, "current_step": "context_gathered"})
    monkeypatch.setattr(
        "app.graph.graph.create_investigation_plan_node",
        lambda s: {
            **s,
            "plan": {"plan_id": "PLAN-001", "objectives": [{"objective_id": "OBJ-001", "objective": "Verify error"}]},
            "plan_id": "PLAN-001",
            "lifecycle_status": "validated",
            "investigation_plan": s.get("investigation_plan") or [{"task_id": "TASK-001", "assigned_agent": "code_investigator", "status": "pending"}],
            "selected_investigators": ["code_investigator"],
            "current_step": "create_investigation_plan",
        },
    )
    monkeypatch.setattr(
        "app.graph.graph.validate_investigation_plan_node",
        lambda s: {
            **s,
            "plan_validation": {"is_valid": True, "errors": [], "warnings": []},
            "lifecycle_status": "execution-ready",
            "current_step": "validate_investigation_plan",
        },
    )

    graph = build_graph()

    call_count = {"count": 0}

    def mock_execute(self, plan, state, context):
        call_count["count"] += 1
        return {
            "execution_status": "completed",
            "workflow_status": "completed",
            "evidence_pool": [
                {
                    "evidence_id": f"EV-{call_count['count']}",
                    "source": "common.py",
                    "snippet": "logging.info('start')",
                    "relationship_to_hypothesis": "observational",
                }
            ],
            "aggregated_findings": [
                {"statement": f"Iteration {call_count['count']} finding", "category": "observational"}
            ],
            "aggregated_hypotheses": [
                {
                    "hypothesis_id": f"HYP-{call_count['count']}",
                    "statement": "Unverified speculation without causal proof",
                    "supporting_evidence_ids": [],
                    "status": "unverified",
                    "confidence": "low",
                }
            ],
            "completed_tasks": [{"task_id": f"TASK-00{call_count['count']}", "assigned_agent": "code_investigator"}],
            "task_results": {},
        }

    from app.agents.investigation_orchestrator import InvestigationOrchestrator
    monkeypatch.setattr(InvestigationOrchestrator, "execute_plan_single_pass", mock_execute)

    input_state = {
        "problem": "Mysterious intermittent glitch",
        "expected_behavior": "No glitch",
        "actual_behavior": "Glitch",
        "workspace_path": "d:/mock/ws",
        "investigation_plan": [{"task_id": "TASK-001", "assigned_agent": "code_investigator", "status": "pending"}],
        "lifecycle_status": "execution-ready",
        "plan_validation": {"is_valid": True, "errors": [], "warnings": []},
        "investigation_iterations": 1,
        "max_iterations": 2,
        "llm_factory": lambda: None,
    }

    result = graph.invoke(input_state)

    assert result["current_step"] == "investigation_completed"
    assert result["is_conclusive"] is False
    assert result["status_label"] == "Inconclusive"
    assert call_count["count"] == 2
    assert result["investigation_iterations"] >= 2


# ============================================================
# 6. Focused Reviewer Accuracy & Problem-Relevance Tests
# ============================================================

def test_irrelevant_hypotheses_do_not_produce_sufficient_decision():
    """1. Irrelevant hypotheses must not produce a sufficient decision."""
    reviewer = Reviewer()

    problem = (
        "The task creation logic passes the user ID and due date in the wrong order "
        "when inserting a task into the database, potentially resulting in incorrect task records."
    )
    # Hypothesis and evidence talk about an entirely unrelated issue (auth/email casing)
    hypotheses = [
        {
            "hypothesis_id": "HYP-AUTH-01",
            "hypothesis_title": "Email casing mismatch during authentication",
            "statement": "The email stored in userModel uses original case but login token normalizes to lowercase, causing 401 Unauthorized during token validation.",
            "supporting_evidence_ids": ["EV-AUTH-01"],
            "contradicting_evidence_ids": [],
            "status": "unverified",
            "confidence": "high",
        }
    ]
    evidence = [
        {
            "evidence_id": "EV-AUTH-01",
            "source": "taskflow/backend/src/services/authService.js",
            "line": 41,
            "snippet": "const token = signToken({ sub: user.id, email: normalizedEmail });",
            "relationship_to_hypothesis": "causal",
            "is_defect_evidence": True,
        }
    ]
    findings = [
        {
            "statement": "Token email is normalized causing casing mismatch with database record.",
            "category": "root_cause_defect",
            "evidence_ids": ["EV-AUTH-01"],
        }
    ]

    result = reviewer.review(
        problem=problem,
        evidence=evidence,
        findings=findings,
        hypotheses=hypotheses,
        iteration=1,
        max_iterations=3,
        original_problem=problem,
    )

    # Must reject sufficiency because hypothesis does not address task creation parameter order
    assert result["is_sufficient"] is False
    assert result["decision"] == "insufficient"
    assert result["further_investigation_necessary"] is True

    # Hypothesis review must explicitly identify irrelevance
    assert len(result["hypothesis_evaluations"]) == 1
    h_eval = result["hypothesis_evaluations"][0]
    assert h_eval["hypothesis_id"] == "HYP-AUTH-01"
    assert h_eval["status"] == "unverified"
    assert not h_eval.get("is_relevant_to_problem", True) or "does not address" in h_eval["evaluation"].lower()

    # Unanswered questions or feedback must direct inquiry back to the reported problem
    all_gap_text = " ".join(result["unanswered_questions"] + result["missing_evidence"] + [result["feedback"]]).lower()
    assert any(k in all_gap_text for k in ("task", "creation", "order", "parameter", "reported problem"))


def test_import_relationships_alone_do_not_establish_root_cause():
    """2. Import relationships alone must not establish a root cause."""
    reviewer = Reviewer()

    problem = (
        "The task creation logic passes the user ID and due date in the wrong order "
        "when inserting a task into the database, potentially resulting in incorrect task records."
    )
    # Hypothesis claims imports cause the defect, but evidence is only import statements
    hypotheses = [
        {
            "hypothesis_id": "HYP-TASK-IMPORT",
            "hypothesis_title": "Module import relationship in task creation",
            "statement": "taskRoutes imports taskService which in turn imports taskModel in task creation flow",
            "supporting_evidence_ids": ["EV-IMP-01", "EV-IMP-02"],
            "contradicting_evidence_ids": [],
            "status": "unverified",
        }
    ]
    evidence = [
        {
            "evidence_id": "EV-IMP-01",
            "source": "taskflow/backend/src/routes/taskRoutes.js",
            "line": 4,
            "snippet": "import * as taskService from '../services/taskService.js';",
            "relationship_to_hypothesis": "causal",
        },
        {
            "evidence_id": "EV-IMP-02",
            "source": "taskflow/backend/src/services/taskService.js",
            "line": 1,
            "snippet": "import * as taskModel from '../models/taskModel.js';",
            "relationship_to_hypothesis": "causal",
        },
    ]
    findings = [
        {
            "statement": "taskRoutes imports taskService and taskService imports taskModel",
            "category": "confirmed_finding",
            "evidence_ids": ["EV-IMP-01", "EV-IMP-02"],
        }
    ]

    result = reviewer.review(
        problem=problem,
        evidence=evidence,
        findings=findings,
        hypotheses=hypotheses,
        iteration=1,
        max_iterations=3,
        original_problem=problem,
    )

    # Must reject sufficiency: import relationships alone do not prove a defect
    assert result["is_sufficient"] is False
    assert result["decision"] == "insufficient"
    assert result["further_investigation_necessary"] is True

    # Reasoning or evaluation must flag import/wiring limitation
    assert "import" in result["feedback"].lower() or any("import" in he["evaluation"].lower() for he in result["hypothesis_evaluations"])


def test_hypothesis_addressing_original_problem_with_causal_evidence_can_be_marked_sufficient():
    """3. A hypothesis addressing the original problem with relevant supporting evidence can be marked sufficient."""
    reviewer = Reviewer()

    problem = (
        "The task creation logic passes the user ID and due date in the wrong order "
        "when inserting a task into the database, potentially resulting in incorrect task records."
    )
    hypotheses = [
        {
            "hypothesis_id": "HYP-TASK-ORDER",
            "hypothesis_title": "Parameter order mismatch in taskModel.create",
            "statement": "In taskModel.js create(), insertWithDue.run() passes task.userId as 4th argument and task.dueDate as 5th argument, swapping due_date and user_id in tasks table.",
            "supporting_evidence_ids": ["EV-TASK-01"],
            "contradicting_evidence_ids": [],
            "status": "unverified",
            "confidence": "high",
        }
    ]
    evidence = [
        {
            "evidence_id": "EV-TASK-01",
            "source": "taskflow/backend/src/models/taskModel.js",
            "line": 13,
            "snippet": "return insertWithDue.run(task.title, task.description, task.status, task.userId, task.dueDate);",
            "relationship_to_hypothesis": "causal",
            "is_defect_evidence": True,
        }
    ]
    findings = [
        {
            "statement": "insertWithDue SQL column order is due_date then user_id, but taskModel.create passes userId then dueDate, swapping column values.",
            "category": "root_cause_defect",
            "evidence_ids": ["EV-TASK-01"],
        }
    ]

    result = reviewer.review(
        problem=problem,
        evidence=evidence,
        findings=findings,
        hypotheses=hypotheses,
        iteration=1,
        max_iterations=3,
        original_problem=problem,
    )

    assert result["is_sufficient"] is True
    assert result["decision"] == "sufficient"
    assert result["further_investigation_necessary"] is False
    assert result.get("approved_hypothesis_id") == "HYP-TASK-ORDER"

    h_eval = [he for he in result["hypothesis_evaluations"] if he["hypothesis_id"] == "HYP-TASK-ORDER"][0]
    assert h_eval["status"] == "supported"
    assert "EV-TASK-01" in h_eval["supporting_evidence_ids"]


def test_missing_evidence_triggers_replanning_flow():
    """4. Missing evidence must trigger the existing re-planning flow."""
    reviewer = Reviewer()

    problem = (
        "The task creation logic passes the user ID and due date in the wrong order "
        "when inserting a task into the database, potentially resulting in incorrect task records."
    )
    # Hypothesis addresses the problem, but supporting evidence is missing
    hypotheses = [
        {
            "hypothesis_id": "HYP-TASK-ORDER",
            "hypothesis_title": "Parameter order mismatch in taskModel.create",
            "statement": "In taskModel.js create(), insertWithDue passes arguments in swapped order",
            "supporting_evidence_ids": [],
            "contradicting_evidence_ids": [],
            "status": "unverified",
        }
    ]

    review_result = reviewer.review(
        problem=problem,
        evidence=[],
        findings=[],
        hypotheses=hypotheses,
        iteration=1,
        max_iterations=3,
        original_problem=problem,
    )

    # 1. Reviewer determines evidence is insufficient
    assert review_result["is_sufficient"] is False
    assert review_result["decision"] == "insufficient"
    assert len(review_result["missing_evidence"]) > 0
    assert len(review_result["unanswered_questions"]) > 0

    # 2. Routing after reviewer selects replanner
    state = {
        "problem": problem,
        "original_problem": problem,
        "review_result": review_result,
        "investigation_iterations": 1,
        "max_iterations": 3,
        "investigation_plan": [
            {"task_id": "TASK-001", "assigned_agent": "code_investigator", "status": "completed"}
        ],
        "completed_tasks": [
            {"task_id": "TASK-001", "assigned_agent": "code_investigator"}
        ],
        "gathered_context": {
            "relevant_files": ["taskflow/backend/src/models/taskModel.js", "taskflow/backend/src/services/taskService.js"]
        },
    }
    next_node = route_after_reviewer(state)
    assert next_node == "replanner"

    # 3. Replanner node generates targeted tasks addressing the missing evidence
    updated_state = replanner_node(state)
    assert updated_state["current_step"] == "replanned"
    assert updated_state["investigation_iterations"] == 2
    assert len(updated_state["investigation_plan"]) > 1

    new_task = updated_state["investigation_plan"][-1]
    assert new_task["assigned_agent"] in ("code_investigator", "dependency_impact_investigator", "runtime_error_investigator")
    assert new_task["status"] == "pending"
    assert new_task["task_id"] != "TASK-001"


def test_final_preliminary_root_cause_matches_original_problem_and_approved_hypothesis():
    """5. The final preliminary root cause must match the original problem and the approved hypothesis."""
    problem = (
        "The task creation logic passes the user ID and due date in the wrong order "
        "when inserting a task into the database, potentially resulting in incorrect task records."
    )
    hyp_irrelevant = {
        "hypothesis_id": "HYP-AUTH-01",
        "hypothesis_title": "Email casing mismatch during authentication",
        "statement": "The email stored in userModel uses original case but login token normalizes to lowercase.",
        "supporting_evidence_ids": ["EV-AUTH-01"],
        "status": "unverified",
    }
    hyp_approved = {
        "hypothesis_id": "HYP-TASK-ORDER",
        "hypothesis_title": "Parameter order mismatch in taskModel.create",
        "statement": "In taskModel.js create(), insertWithDue passes userId as 4th argument and dueDate as 5th argument, swapping due_date and user_id in tasks table.",
        "supporting_evidence_ids": ["EV-TASK-01"],
        "status": "supported",
    }
    evidence_pool = [
        {
            "evidence_id": "EV-AUTH-01",
            "source": "taskflow/backend/src/services/authService.js",
            "snippet": "const token = signToken({ sub: user.id, email: normalizedEmail });",
        },
        {
            "evidence_id": "EV-TASK-01",
            "source": "taskflow/backend/src/models/taskModel.js",
            "line": 13,
            "snippet": "return insertWithDue.run(task.title, task.description, task.status, task.userId, task.dueDate);",
            "relationship_to_hypothesis": "causal",
            "is_defect_evidence": True,
        },
    ]

    review_result = {
        "decision": "sufficient",
        "is_sufficient": True,
        "confidence": "high",
        "approved_hypothesis_id": "HYP-TASK-ORDER",
        "approved_hypothesis": hyp_approved,
        "hypothesis_evaluations": [
            {
                "hypothesis_id": "HYP-AUTH-01",
                "status": "unverified",
                "is_relevant_to_problem": False,
                "evaluation": "Irrelevant to task creation parameter order.",
            },
            {
                "hypothesis_id": "HYP-TASK-ORDER",
                "status": "supported",
                "is_relevant_to_problem": True,
                "evaluation": "Supported by causal parameter swap evidence in taskModel.js.",
            },
        ],
        "reasoning": "taskModel.js line 13 swaps userId and dueDate parameters when inserting a task with a due date.",
    }

    state = {
        "problem": problem,
        "original_problem": problem,
        "review_result": review_result,
        "aggregated_hypotheses": [hyp_irrelevant, hyp_approved],
        "evidence_pool": evidence_pool,
    }

    result_state = root_cause_result_node(state)

    # Conclusive root cause established
    assert result_state["is_conclusive"] is True
    assert result_state["status_label"] == "Likely root cause"

    # Must match the approved hypothesis (taskModel parameter order), NOT the irrelevant auth hypothesis
    assert "taskModel" in result_state["likely_root_cause"] or "user_id" in result_state["likely_root_cause"] or "parameter" in result_state["likely_root_cause"].lower()
    assert "email casing" not in result_state["likely_root_cause"].lower()
    assert result_state["diagnosis"]["root_cause"] == result_state["likely_root_cause"]

    # Supporting evidence must only include the relevant task creation causal evidence
    supp_ids = [e.get("evidence_id") for e in result_state["diagnosis"]["supporting_evidence"]]
    assert "EV-TASK-01" in supp_ids
    assert "EV-AUTH-01" not in supp_ids


