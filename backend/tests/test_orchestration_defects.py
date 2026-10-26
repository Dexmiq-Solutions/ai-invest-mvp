"""Regression tests for Defects A through G in Phases 1-6 investigation workflow."""

import pytest
from app.graph.context_gathering import (
    identify_investigation_concepts,
    evaluate_gathered_context_quality,
    understand_problem,
)
from app.agents.investigation_orchestrator import (
    InvestigationOrchestrator,
    InvestigationReviewer,
    InvestigationPlanner,
)
from app.graph.state import InvestigationState
from fastapi.testclient import TestClient
from app.api import app, sessions


def test_context_gathering_filters_boilerplate_and_instructions():
    """Defect B: Prompt boilerplate and instructions must not become search terms."""
    noisy_problem = (
        "The email in the login token does not match the email stored for the user. "
        "Investigate the authentication flow, token generation, stored user email, "
        "and any mismatch between them. Identify relevant files and report findings "
        "based on actual code. Additional Developer Context: Please trace cross-component "
        "dependencies and verify taskflow behavior."
    )
    problem_understanding = understand_problem({"problem": noisy_problem})
    concepts = identify_investigation_concepts(problem_understanding, repository_summary={})
    
    # Check that instruction boilerplate is NOT in concepts
    forbidden_terms = {
        "investigate", "identify", "additional", "developer", "report", 
        "taskflow", "context", "findings", "relevant", "files", "source", "user."
    }
    concept_set = {c.lower() for c in concepts}
    found_forbidden = concept_set.intersection(forbidden_terms)
    assert not found_forbidden, f"Found boilerplate terms in concepts: {found_forbidden}"
    
    # Check that meaningful domain terms ARE present
    expected_technical_terms = {"login", "token", "email", "authentication"}
    assert any(term in concept_set for term in expected_technical_terms), (
        f"Expected technical terms from {expected_technical_terms} in {concepts}"
    )


def test_context_quality_evaluation_reports_sufficient_when_files_present():
    """Defect B / Phase 1: Completeness gate must report sufficient when problem and files are present, without generating prohibited questions."""
    gathered_context = {
        "relevant_files": [{"path": "authService.js"}, {"path": "userModel.js"}],
        "problem_understanding": {"problem_summary": "Token email mismatch"},
    }
    evaluation = evaluate_gathered_context_quality(gathered_context)
    assert evaluation["is_sufficient"] is True
    assert evaluation["context_status"] == "sufficient"
    assert "reason" in evaluation
    assert "unresolved_questions" not in evaluation
    assert "evidence_gaps" not in evaluation


def test_repeated_context_submissions_preserve_problem_and_accumulate_history():
    """Defect A: Repeated context submissions must not duplicate problem statement."""
    client = TestClient(app)
    
    # Start session
    resp = client.post("/api/investigation/start", data={"problem": "Login issue with token."})
    assert resp.status_code == 200
    session_id = resp.json()["session_id"]
    
    # Add context 1
    ctx1 = "User logs in, token has uppercase email while database has lowercase email."
    resp1 = client.post(f"/api/investigation/{session_id}/context", json={"additional_context": ctx1})
    assert resp1.status_code == 200
    state1 = sessions[session_id]
    assert state1["original_problem"] == "Login issue with token."
    assert ctx1 in state1["developer_context"]
    assert "Additional Developer Context:\nAdditional Developer Context:" not in state1["problem"]
    
    # Add context 2
    ctx2 = "Runtime response code has not been observed."
    resp2 = client.post(f"/api/investigation/{session_id}/context", json={"additional_context": ctx2})
    assert resp2.status_code == 200
    state2 = sessions[session_id]
    assert state2["original_problem"] == "Login issue with token."
    assert len(state2["developer_context"]) == 2
    assert state2["developer_context"][0] == ctx1
    assert state2["developer_context"][1] == ctx2
    assert len(state2["context_history"]) >= 2
    # Ensure no runaway concatenation
    assert state2["problem"].count("Additional Developer Context:") <= 1


def test_planned_tasks_lifecycle_records_skipped_tasks_when_stopping_early():
    """Defect C: Unexecuted planned tasks must be recorded as skipped with skip_reason."""
    state = InvestigationState(
        session_id="test-session-defect-c",
        problem="Token email mismatch",
        original_problem="Token email mismatch",
        workspace_path=".",
        investigation_plan=[
            {
                "task_id": "task-1",
                "objective": "Inspect write site",
                "assigned_agent": "code_investigator",
                "reason": "Verify token email write site",
                "input": {"problem": "Token email mismatch"},
                "scope": ["auth/token.py"],
                "instructions": "Inspect write site for token email",
                "expected_output": "Token write statement",
                "completion_criteria": "Found write statement",
                "status": "pending",
                "step_id": "task-1",
            },
            {
                "task_id": "task-2",
                "objective": "Inspect read site",
                "assigned_agent": "code_investigator",
                "reason": "Verify token email read site",
                "input": {"problem": "Token email mismatch"},
                "scope": ["auth/token.py"],
                "instructions": "Inspect read site for token email",
                "expected_output": "Token read statement",
                "completion_criteria": "Found read statement",
                "status": "pending",
                "step_id": "task-2",
            },
            {
                "task_id": "task-3",
                "objective": "Trace auth middleware",
                "assigned_agent": "dependency_impact_investigator",
                "reason": "Trace token propagation in middleware",
                "input": {"problem": "Token email mismatch"},
                "scope": ["auth/middleware.py"],
                "instructions": "Trace auth middleware email mapping",
                "expected_output": "Middleware chain",
                "completion_criteria": "Traced middleware",
                "status": "pending",
                "step_id": "task-3",
            },
            {
                "task_id": "task-4",
                "objective": "Trace cross-component impact",
                "assigned_agent": "dependency_impact_investigator",
                "reason": "Inspect token impact across services",
                "input": {"problem": "Token email mismatch"},
                "scope": ["services/user.py"],
                "instructions": "Trace cross-component impact",
                "expected_output": "Impact report",
                "completion_criteria": "Traced impact",
                "status": "pending",
                "step_id": "task-4",
            },
        ],
        findings=[],
        evidence=[],
    )
    
    orchestrator = InvestigationOrchestrator(max_iterations=2, task_budget=2)
    # Run orchestrator with state containing 4 planned tasks
    results = orchestrator.investigate(
        investigation_task="Token email mismatch",
        workspace_path=".",
        current_state=state,
    )
    
    planned = results["investigation_plan"]
    assert len(planned) == 4
    
    # First executed task is completed
    assert planned[0]["status"] == "completed"
    
    # All tasks are accounted for: completed + failed + skipped == total planned
    assert len(results["completed_tasks"]) >= 1
    assert len(results["completed_tasks"]) + len(results.get("failed_tasks", [])) + len(results["skipped_tasks"]) == 4
    
    # All skipped tasks have explicit skip_reason
    for skipped in results["skipped_tasks"]:
        assert skipped["status"] == "skipped"
        assert skipped.get("skip_reason") is not None
    
    skipped_step_ids = [t["step_id"] for t in results["skipped_tasks"]]
    assert "task-4" in skipped_step_ids
    assert results["execution_status"] in ("completed", "partial")
    assert results["orchestration_status"] in ("partial", "completed")


def test_reviewer_preserves_unanswered_questions_without_runtime_verification():
    """Defect D: Reviewer must preserve missing evidence and unanswered questions for unverified runtime behavior."""
    reviewer = InvestigationReviewer()
    
    # Evidence with code-level write-site and read-site findings, but no runtime probe
    evidence = [
        {
            "evidence_id": "ev-1",
            "source": "authService.js",
            "line": 41,
            "matched_text": "const token = signToken({ sub: user.id, email: normalizedEmail });",
            "relationship_to_hypothesis": "causal",
            "observation_type": "direct_code",
        },
        {
            "evidence_id": "ev-2",
            "source": "authenticate.js",
            "line": 14,
            "matched_text": "const user = userService.findByEmail(payload.email);",
            "relationship_to_hypothesis": "causal",
            "observation_type": "direct_code",
        },
    ]
    findings = [
        {
            "finding": "Token uses normalizedEmail causing casing mismatch with database",
            "category": "possible_root_cause",
            "source": "authService.js",
            "evidence_ids": ["ev-1", "ev-2"],
        }
    ]
    
    review = reviewer.review(
        problem="Token email casing mismatch",
        evidence_pool=evidence,
        findings=findings,
        hypotheses=[],
        iteration=1,
        max_iterations=3,
    )
    
    # Although sufficient for code hypothesis, it must not wipe missing evidence or unanswered questions
    assert review["is_sufficient"] is True
    assert "runtime" in review["stop_reason"].lower() or "pending" in review["stop_reason"].lower()
    assert len(review["unanswered_questions"]) > 0
    assert any("runtime" in q.lower() or "probe" in q.lower() for q in review["unanswered_questions"])
    assert len(review["missing_evidence"]) > 0


def test_evidence_and_finding_deduplication():
    """Defect E: Same source file, line number, and text must be deduplicated."""
    orchestrator = InvestigationOrchestrator()
    
    raw_evidence = [
        {
            "id": "ev-1",
            "source": "backend/src/models/userModel.js",
            "line": 25,
            "text": "SELECT * FROM users WHERE email = ?",
            "kind": "direct_code",
        },
        {
            "id": "ev-2",
            "source": "backend/src/models/userModel.js",
            "line": 25,
            "text": "SELECT * FROM users WHERE email = ?",
            "kind": "candidate_observation",
        },
        {
            "id": "ev-3",
            "source": "backend/src/services/authService.js",
            "line": 41,
            "text": "signToken({ sub: user.id, email: normalizedEmail })",
            "kind": "direct_code",
        },
    ]
    
    deduped = orchestrator.deduplicate_evidence(raw_evidence)
    assert len(deduped) == 2, f"Expected 2 deduplicated evidence items, got {len(deduped)}"
    sources = [e["source"] for e in deduped]
    assert "backend/src/models/userModel.js" in sources
    assert "backend/src/services/authService.js" in sources


def test_separation_of_execution_status_investigation_status_and_conclusiveness():
    """Defect G: Execution completion must be decoupled from diagnostic conclusiveness."""
    client = TestClient(app)
    
    # In an unverified hypothesis situation, the response should show execution completed,
    # but investigation partial and is_conclusive False.
    state = InvestigationState(
        session_id="test-session-defect-g",
        problem="Token email casing mismatch",
        workspace_path=".",
        investigation_status="partial",
        execution_status="completed",
        status_label="Unverified hypothesis",
        is_conclusive=False,
        findings=[{"claim": "Write site normalizes, read site exact query"}],
        evidence=[{"source": "authService.js", "line": 41, "text": "signToken"}],
        investigation_stop_reason="Code-level causal evidence collected across components; runtime verification pending",
    )
    sessions["test-session-defect-g"] = state
    
    resp = client.post("/api/investigation/test-session-defect-g/execute")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "INVESTIGATION_COMPLETED"
    assert data["execution_status"] == "completed"
    assert data["investigation_status"] == "partial"
    assert data["status_label"] == "Unverified hypothesis"
    assert data["is_conclusive"] is False


def test_reviewer_continues_when_unanswered_causal_question_has_planned_task():
    """Phase 6 Orchestration: Reviewer must not stop when important causal questions remain answerable by planned tasks."""
    reviewer = InvestigationReviewer()

    problem = "The email in the login token does not match the email stored for the user."
    # Causal evidence collected only from a single component/layer (userModel.js in storage)
    evidence_pool = [
        {
            "evidence_id": "E1",
            "source": "backend/src/models/userModel.js",
            "line": 7,
            "matched_text": "const byEmail = db.prepare('SELECT * FROM users WHERE email = ?');",
            "kind": "implementation_match",
        },
        {
            "evidence_id": "E2",
            "source": "backend/src/models/userModel.js",
            "line": 8,
            "matched_text": "const byLogin = db.prepare('SELECT * FROM users WHERE LOWER(email) = ?');",
            "kind": "implementation_match",
        },
    ]
    findings = [
        {
            "statement": "Database query in userModel.js performs exact equality comparison on findByEmail",
            "category": "Confirmed Finding",
            "evidence_ids": ["E1"],
            "confidence": "medium",
        }
    ]
    hypotheses = [
        {
            "statement": "Token email casing mismatch between normalized token and exact query",
            "status": "unverified",
        }
    ]
    # Planned tasks remain for client interceptor and auth middleware tracing
    pending_tasks = [
        {
            "step_id": "code-2",
            "investigator": "code_investigator",
            "task": "Inspect frontend API client request interceptors, URL matching conditions, and header configuration",
            "objective": "Verify request interceptor configuration in api.js/client.js and token attachment rules.",
            "status": "pending",
        },
        {
            "step_id": "dependency-3",
            "investigator": "dependency_impact_investigator",
            "task": "Trace authentication middleware and route handler invocation path",
            "objective": "Trace route registration and authentication middleware to identify where the 401 is emitted.",
            "status": "pending",
        },
    ]
    completed_investigators = {"code_investigator"}
    completed_tasks = [
        {
            "step_id": "query-1",
            "investigator": "code_investigator",
            "task": "Inspect database query in userModel.js",
            "status": "completed",
        }
    ]

    review = reviewer.review(
        problem=problem,
        evidence_pool=evidence_pool,
        findings=findings,
        hypotheses=hypotheses,
        iteration=1,
        max_iterations=5,
        pending_tasks=pending_tasks,
        completed_investigators=completed_investigators,
        completed_tasks=completed_tasks,
    )

    # Reviewer must NOT declare sufficiency and must NOT stop
    assert review["is_sufficient"] is False, "Reviewer prematurely claimed sufficiency after single task"
    assert review["should_stop"] is False, "Reviewer prematurely stopped loop while answerable causal tasks remain"
    assert len(review["unanswered_questions"]) > 0, "Expected unanswered questions to guide follow-up tasks"
    assert any("middleware" in q.lower() or "caller" in q.lower() or "intercept" in q.lower() for q in review["unanswered_questions"])


def test_reviewer_stops_with_partial_diagnosis_when_cross_component_evidence_gathered_and_remaining_tasks_redundant():
    """Phase 6 Orchestration: Reviewer may stop with partial diagnosis when cross-component causal evidence is collected and remaining tasks are redundant."""
    reviewer = InvestigationReviewer()

    problem = "The email in the login token does not match the email stored for the user."
    # Causal evidence collected across multiple distinct layers: storage, client, middleware
    evidence_pool = [
        {
            "evidence_id": "E1",
            "source": "backend/src/models/userModel.js",
            "line": 7,
            "matched_text": "const byEmail = db.prepare('SELECT * FROM users WHERE email = ?');",
            "kind": "implementation_match",
        },
        {
            "evidence_id": "E2",
            "source": "frontend/src/services/api.js",
            "line": 10,
            "matched_text": "if (token && !config.url.includes('auth')) { config.headers.Authorization = `Bearer ${token}`; }",
            "kind": "implementation_match",
        },
        {
            "evidence_id": "E3",
            "source": "backend/src/middleware/authenticate.js",
            "line": 13,
            "matched_text": "const payload = authService.verifyToken(token); const user = userService.findByEmail(payload.email);",
            "kind": "implementation_match",
        },
    ]
    findings = [
        {
            "statement": "Database query in userModel.js performs exact equality comparison on findByEmail",
            "category": "Confirmed Finding",
            "evidence_ids": ["E1"],
            "confidence": "medium",
        },
        {
            "statement": "Frontend request interceptor conditionally omits Authorization header on auth urls",
            "category": "Confirmed Finding",
            "evidence_ids": ["E2"],
            "confidence": "medium",
        },
        {
            "statement": "Authentication middleware verifies token and invokes exact findByEmail lookup",
            "category": "Confirmed Finding",
            "evidence_ids": ["E3"],
            "confidence": "medium",
        },
    ]
    hypotheses = [
        {
            "statement": "Token email casing mismatch between normalized token and exact query",
            "status": "unverified",
        }
    ]
    # Completed tasks include both specialized roles and target all critical layers
    completed_tasks = [
        {"step_id": "query-1", "investigator": "code_investigator", "task": "Inspect database query in userModel.js", "status": "completed"},
        {"step_id": "code-2", "investigator": "code_investigator", "task": "Inspect frontend API client request interceptors", "status": "completed"},
        {"step_id": "dependency-3", "investigator": "dependency_impact_investigator", "task": "Trace authentication middleware and route handler invocation path", "status": "completed"},
    ]
    completed_investigators = {"code_investigator", "dependency_impact_investigator"}

    # Only a redundant generic dependency pass remains
    pending_tasks = [
        {
            "step_id": "dependency-4",
            "investigator": "dependency_impact_investigator",
            "task": "Trace dependencies, callers, and cross-component flow related to: The email in the login token does not match the email stored for the user",
            "status": "pending",
        }
    ]

    review = reviewer.review(
        problem=problem,
        evidence_pool=evidence_pool,
        findings=findings,
        hypotheses=hypotheses,
        iteration=3,
        max_iterations=5,
        pending_tasks=pending_tasks,
        completed_investigators=completed_investigators,
        completed_tasks=completed_tasks,
    )

    # Reviewer should stop cleanly with medium confidence and pending runtime verification
    assert review["is_sufficient"] is True, "Reviewer failed to accept sufficient cross-component evidence"
    assert review["should_stop"] is True, "Reviewer failed to stop when only redundant tasks remained"
    assert review["confidence"] == "medium"
    assert "runtime verification pending" in review["stop_reason"]


def test_reviewer_stops_with_partial_diagnosis_when_iteration_budget_reached():
    """Phase 6 Orchestration: Reviewer must stop when the iteration budget is reached, even if pending tasks remain."""
    reviewer = InvestigationReviewer()

    problem = "The email in the login token does not match the email stored for the user."
    evidence_pool = [
        {
            "evidence_id": "E1",
            "source": "backend/src/models/userModel.js",
            "line": 7,
            "matched_text": "const byEmail = db.prepare('SELECT * FROM users WHERE email = ?');",
            "kind": "implementation_match",
        }
    ]
    findings = [
        {
            "statement": "Database query in userModel.js performs exact equality comparison",
            "category": "Confirmed Finding",
            "evidence_ids": ["E1"],
            "confidence": "medium",
        }
    ]
    hypotheses = [{"statement": "Token email casing mismatch", "status": "unverified"}]
    pending_tasks = [
        {"step_id": "dependency-3", "investigator": "dependency_impact_investigator", "task": "Trace auth middleware", "status": "pending"}
    ]

    review = reviewer.review(
        problem=problem,
        evidence_pool=evidence_pool,
        findings=findings,
        hypotheses=hypotheses,
        iteration=5,
        max_iterations=5,
        pending_tasks=pending_tasks,
        completed_investigators={"code_investigator"},
        completed_tasks=[],
    )

    # Must stop on budget limit
    assert review["should_stop"] is True
    assert "Maximum iteration limit reached" in review["stop_reason"]

