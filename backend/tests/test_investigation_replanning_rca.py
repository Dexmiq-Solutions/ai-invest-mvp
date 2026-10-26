"""Regression & integration tests for evidence-driven investigation and re-planning workflow.

Validates:
1. Investigation context preservation across replanning cycles.
2. Structured task outputs (objective, status, findings, evidence with explanations,
   hypothesis_assessment, unanswered_questions, evidence_gaps, limitations).
3. Reviewer evaluation returning established_by_evidence, causal_chain_verified,
   unanswered_objectives, and actionable_feedback.
4. Evidence-driven replanning targeting evidence gaps without redundant repeating.
5. Replan-to-Orchestrator execution loop without restarting initial workflow setup.
6. Task completion verification (tasks without answered objectives marked unresolved).
7. Hypothesis reassessment with supporting/contradicting evidence.
8. Controlled task execution (tracking completed, failed, skipped, and unresolved with reasons).
9. Diverse software problem archetypes (config missing, parameter mismatch, middleware failure).
"""

import pytest
from unittest.mock import MagicMock

from app.agents.investigator_contract import InvestigatorResult, EvidenceRecord
from app.agents.code_investigator import CodeInvestigator
from app.agents.runtime_error_investigator import RuntimeErrorInvestigator
from app.agents.dependency_impact_investigator import DependencyImpactInvestigator
from app.agents.reviewer import Reviewer
from app.agents.replanner import Replanner
from app.agents.investigation_orchestrator import InvestigationOrchestrator
from app.graph.graph import (
    reviewer_node,
    route_after_reviewer,
    replanner_node,
    root_cause_result_node,
    inconclusive_result_node,
    build_graph,
)


# =====================================================================
# 1. Structured Investigation Task Outputs
# =====================================================================

def test_investigator_result_contract_fields():
    """Verify InvestigatorResult has all required structured output fields."""
    ev = EvidenceRecord(
        source="app/config.py",
        line=42,
        matched_text="PORT = int(os.environ['PORT'])",
        kind="code_match",
        explanation="Direct access to 'PORT' without fallback causes KeyError on startup",
    )
    res = InvestigatorResult(
        agent_name="runtime_investigator",
        status="completed",
        objective="Determine why the application crashes during startup",
        findings=[],
        evidence=[ev],
        hypotheses=[],
        unanswered_questions=[],
        evidence_gaps=[],
        limitations=["Runtime logs unavailable in static analysis"],
        hypothesis_assessment={"HYP-01": "supported"},
    )

    assert res.objective == "Determine why the application crashes during startup"
    assert res.status == "completed"
    assert len(res.evidence) == 1
    assert res.evidence[0].explanation == "Direct access to 'PORT' without fallback causes KeyError on startup"
    assert res.limitations == ["Runtime logs unavailable in static analysis"]
    assert res.hypothesis_assessment == {"HYP-01": "supported"}


def test_specialists_produce_structured_contract_outputs():
    """Verify specialist investigators initialize all required structured fields."""
    code_inv = CodeInvestigator()
    res1 = code_inv.investigate(
        workspace_path=".",
        investigation_task="check password verification order",
    )
    assert "objective" in res1
    assert "status" in res1
    assert "evidence_gaps" in res1
    assert "hypothesis_assessment" in res1
    assert res1["status"] in ("completed", "partial", "failed", "unresolved")

    dep_inv = DependencyImpactInvestigator()
    res2 = dep_inv.investigate(
        workspace_path=".",
        investigation_task="trace database connector dependency",
    )
    assert "objective" in res2
    assert "evidence_gaps" in res2
    assert "hypothesis_assessment" in res2

    rt_inv = RuntimeErrorInvestigator()
    res3 = rt_inv.investigate(
        workspace_path=".",
        investigation_task="trace 500 error stack trace",
    )
    assert "objective" in res3
    assert "evidence_gaps" in res3
    assert "hypothesis_assessment" in res3


# =====================================================================
# 2. Reviewer Evidence Evaluation & Actionable Feedback
# =====================================================================

def test_reviewer_produces_actionable_feedback_and_evaluations():
    """Reviewer must return what has been established, unanswered objectives, and actionable feedback."""
    reviewer = Reviewer()
    problem = "User receives HTTP 500 when saving user profile"

    evidence = [
        {
            "evidence_id": "EV-01",
            "source": "backend/api/users.py",
            "line": 45,
            "snippet": "update_user_profile(user_id, profile_data)",
            "relationship_to_hypothesis": "observational",
            "explanation": "Call site for profile update in user controller",
        }
    ]
    findings = [
        {"statement": "Controller routes POST /profile to update_user_profile", "category": "code_flow"}
    ]
    hypotheses = [
        {
            "hypothesis_id": "HYP-01",
            "hypothesis_title": "Database column type mismatch on profile update",
            "statement": "Profile bio is passed as string but column expects binary blob",
            "supporting_evidence_ids": [],
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
        unanswered_questions=["What exception is thrown inside update_user_profile?"],
    )

    assert result["decision"] == "insufficient"
    assert result["further_investigation_necessary"] is True
    assert "established_by_evidence" in result
    assert "causal_chain_verified" in result
    assert result["causal_chain_verified"] is False
    assert "unanswered_objectives" in result
    assert len(result["unanswered_objectives"]) >= 1

    feedback = result.get("actionable_feedback", {})
    assert "unanswered_questions" in feedback
    assert "related_hypotheses" in feedback
    assert "required_next_steps" in feedback
    assert len(feedback["required_next_steps"]) > 0


def test_reviewer_rejects_plausible_hypothesis_without_causal_evidence():
    """A plausible hypothesis without defect/causal evidence must not be approved."""
    reviewer = Reviewer()
    problem = "Payment checkout times out after 30 seconds"

    evidence = [
        {
            "evidence_id": "EV-TIME-01",
            "source": "backend/payment.py",
            "line": 100,
            "snippet": "timeout = 30",
            "relationship_to_hypothesis": "observational",
            "is_defect_evidence": False,
        }
    ]
    findings = [{"statement": "Timeout is set to 30", "category": "observation"}]
    hypotheses = [
        {
            "hypothesis_id": "HYP-GATEWAY",
            "hypothesis_title": "Third-party gateway is dropping requests",
            "statement": "Gateway drops outbound socket connections without response",
            "supporting_evidence_ids": ["EV-TIME-01"],
            "status": "unverified",
            "confidence": "medium",
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

    assert result["decision"] == "insufficient"
    assert result["causal_chain_verified"] is False


def test_reviewer_accepts_verified_causal_chain():
    """When causal evidence connects defect to the original problem, reviewer accepts."""
    reviewer = Reviewer()
    problem = "Database connection pool exhausted during peak loads"

    evidence = [
        {
            "evidence_id": "EV-CAUSAL-LEAK",
            "source": "backend/db/session.py",
            "line": 88,
            "snippet": "conn = pool.acquire()\n# missing pool.release(conn) in error branch",
            "relationship_to_hypothesis": "causal",
            "is_defect_evidence": True,
            "explanation": "Connection is acquired without release in error branch, leaking connections",
        }
    ]
    findings = [{"statement": "Unreleased connection in exception handling", "category": "defect"}]
    hypotheses = [
        {
            "hypothesis_id": "HYP-LEAK",
            "hypothesis_title": "Connection leak in db session error handler",
            "statement": "Failure to release pooled connection leaks connections until pool is exhausted",
            "supporting_evidence_ids": ["EV-CAUSAL-LEAK"],
            "status": "supported",
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
    assert result["causal_chain_verified"] is True
    assert len(result["established_by_evidence"]) >= 1


# =====================================================================
# 3. Evidence-Driven Replanning
# =====================================================================

def test_replanner_targets_unanswered_questions_and_avoids_redundancy():
    """Replanner must generate targeted tasks with expected evidence and avoid repeating previous tasks."""
    replanner = Replanner()

    previous_tasks = [
        {
            "task_id": "TASK-01",
            "assigned_agent": "code_investigator",
            "target_files": ["backend/api/auth.py"],
            "investigation_inquiry": "Check token validation",
            "task": "Check token validation",
            "instructions": "Check token validation",
        }
    ]
    review_result = {
        "decision": "insufficient",
        "reasoning": "Need to trace token expiration check in session manager",
        "unanswered_questions": ["Where is the session token expiration timestamp validated?"],
        "missing_evidence": ["Line of code where expiration timestamp is checked against current time"],
        "actionable_feedback": {
            "unanswered_questions": ["Where is the session token expiration timestamp validated?"],
            "related_hypotheses": [{"hypothesis_id": "HYP-EXPIRE", "status": "unverified"}],
            "required_next_steps": ["Investigate session manager token validation"],
        },
    }
    hypotheses = [
        {
            "hypothesis_id": "HYP-EXPIRE",
            "hypothesis_title": "Token expiration checked with local instead of UTC time",
            "statement": "Local time causes immediate expiration across timezones",
            "status": "unverified",
        }
    ]

    new_tasks = replanner.replan(
        problem="Token rejected across timezones",
        shared_state={
            "completed_tasks": previous_tasks,
            "investigation_plan": previous_tasks,
            "hypotheses": hypotheses,
        },
        review_result=review_result,
        gathered_context={
            "relevant_files": ["backend/api/auth.py", "backend/session/manager.py"]
        },
    )

    assert len(new_tasks) > 0
    new_task = new_tasks[0]
    # Check new task attributes
    assert "expected_evidence" in new_task
    assert "unanswered_question" in new_task
    assert new_task.get("target_hypothesis_id") == "HYP-EXPIRE"
    # Scope should not be duplicate of TASK-01
    assert new_task.get("scope") != ["backend/api/auth.py"] or new_task.get("assigned_agent") != "code_investigator"


# =====================================================================
# 4. Orchestrator Task Completion & Hypothesis Reassessment
# =====================================================================

def test_orchestrator_marks_unresolved_tasks_when_objective_not_answered():
    """Tasks where no relevant evidence or findings were found should be marked unresolved, not completed."""
    orchestrator = InvestigationOrchestrator()

    # Mock an investigator that returns empty evidence and empty findings
    mock_specialist = MagicMock()
    mock_specialist.return_value = {
        "agent_name": "code_investigator",
        "status": "unresolved",
        "objective": "Find token expiration code",
        "findings": [],
        "evidence": [],
        "hypotheses": [],
        "unanswered_questions": ["Token expiration check not found in inspected file"],
        "evidence_gaps": ["Missing session file"],
    }
    orchestrator.register_investigator("code_investigator", mock_specialist)

    tasks = [
        {
            "task_id": "TASK-UNRESOLVED",
            "objective": "Find token expiration code",
            "assigned_agent": "code_investigator",
            "reason": "Verify token expiration logic",
            "input": {"problem": "Token rejected immediately"},
            "scope": ["non_existent_file.py"],
            "instructions": "Find token expiration code in non_existent_file.py",
            "expected_output": "Token expiration validation logic",
            "completion_criteria": "Token expiration verified or refuted",
            "status": "pending",
        }
    ]

    result = orchestrator.execute_plan_single_pass(
        plan=tasks,
        state={"problem": "Token rejected immediately", "workspace_path": "."},
        context={"project_type": "python"},
    )

    # Check tracking
    completed_ids = [t.get("task_id") for t in result.get("completed_tasks", [])]
    unresolved_ids = [t.get("task_id") for t in result.get("unresolved_tasks", [])]

    assert "TASK-UNRESOLVED" not in completed_ids
    assert "TASK-UNRESOLVED" in unresolved_ids
    assert len(result.get("unanswered_questions", [])) > 0


def test_orchestrator_reassesses_hypotheses_with_supporting_evidence():
    """Orchestrator must update hypothesis status and confidence when causal evidence is found."""
    orchestrator = InvestigationOrchestrator()

    ev = {
        "evidence_id": "EV-TIMEOUT",
        "source": "app/config.py",
        "line": 12,
        "snippet": "TIMEOUT = -1",
        "relationship_to_hypothesis": "causal",
        "is_defect_evidence": True,
        "explanation": "Negative timeout causes immediate timeout in socket connect",
    }
    mock_specialist = MagicMock()
    mock_specialist.return_value = {
        "agent_name": "code_investigator",
        "status": "completed",
        "objective": "Inspect socket timeout configuration",
        "findings": [{"statement": "Found TIMEOUT configured to -1 in app/config.py", "category": "defect"}],
        "evidence": [ev],
        "hypotheses": [],
        "hypothesis_assessment": {"HYP-TIMEOUT": "supported"},
    }
    orchestrator.register_investigator("code_investigator", mock_specialist)

    tasks = [
        {
            "task_id": "TASK-TIMEOUT",
            "objective": "Inspect socket timeout configuration",
            "assigned_agent": "code_investigator",
            "reason": "Inspect socket timeout configuration",
            "input": {"problem": "Socket immediately aborts"},
            "scope": ["app/config.py"],
            "instructions": "Inspect socket timeout configuration in app/config.py",
            "expected_output": "Socket timeout configuration code snippet",
            "completion_criteria": "Socket timeout verified or refuted",
            "status": "pending",
        }
    ]
    initial_hypotheses = [
        {
            "hypothesis_id": "HYP-TIMEOUT",
            "hypothesis_title": "Negative socket timeout configured",
            "statement": "Socket timeout is set to -1 in config app/config.py",
            "status": "unverified",
            "confidence": "low",
            "supporting_evidence_ids": [],
            "contradicting_evidence_ids": [],
        }
    ]

    result = orchestrator.execute_plan_single_pass(
        plan=tasks,
        state={
            "problem": "Socket immediately aborts",
            "workspace_path": ".",
            "hypotheses": initial_hypotheses,
        },
        context={"project_type": "python"},
    )

    reassessed_hyps = result.get("hypotheses", [])
    assert len(reassessed_hyps) == 1
    hyp = reassessed_hyps[0]
    assert hyp["hypothesis_id"] == "HYP-TIMEOUT"
    assert hyp["status"] == "supported"
    assert hyp["confidence"] in ("high", "medium")


# =====================================================================
# 5. LangGraph End-to-End Execution Flow (Investigation -> Replan -> Orchestrator)
# =====================================================================

def test_replan_to_orchestrator_loop_reaches_resolution(monkeypatch):
    """Verify multi-cycle flow: initial investigation -> insufficient review -> replan -> follow-up investigation -> sufficient root cause."""
    monkeypatch.setattr("app.graph.graph.validate_input", lambda s: {**s, "current_step": "input_validated", "validation_errors": []})
    monkeypatch.setattr("app.graph.graph.check_context", lambda s: {**s, "context_status": "sufficient", "current_step": "context_checked"})
    monkeypatch.setattr("app.graph.graph.setup_workspace", lambda s: {**s, "workspace_path": "d:/mock/ws", "current_step": "workspace_ready"})
    monkeypatch.setattr("app.graph.graph.analyze_repository", lambda p: {"project_structure": {"file_count": 5}})
    monkeypatch.setattr("app.graph.graph.context_gathering_graph_node", lambda s: {**s, "gathered_context": {"relevant_files": ["api.py", "service.py"]}, "current_step": "context_gathered"})
    monkeypatch.setattr(
        "app.graph.graph.create_investigation_plan_node",
        lambda s: {
            **s,
            "plan": {"plan_id": "PLAN-001", "objectives": [{"objective_id": "OBJ-001", "objective": "Inspect call_service"}]},
            "plan_id": "PLAN-001",
            "lifecycle_status": "validated",
            "investigation_plan": [{"task_id": "TASK-001", "assigned_agent": "code_investigator", "status": "pending", "scope": ["api.py"]}],
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

    call_count = {"passes": 0}

    def mock_execute(self, plan, state, context):
        call_count["passes"] += 1
        if call_count["passes"] == 1:
            # Cycle 1: observational evidence only
            return {
                "execution_status": "completed",
                "workflow_status": "completed",
                "completed_tasks": plan,
                "failed_tasks": [],
                "skipped_tasks": [],
                "unresolved_tasks": [],
                "evidence_pool": [
                    {
                        "evidence_id": "EV-OBS",
                        "source": "api.py",
                        "line": 10,
                        "snippet": "call_service()",
                        "relationship_to_hypothesis": "observational",
                        "is_defect_evidence": False,
                    }
                ],
                "aggregated_findings": [{"statement": "Observed call_service invocation", "category": "flow"}],
                "aggregated_hypotheses": [
                    {
                        "hypothesis_id": "HYP-01",
                        "hypothesis_title": "Parameter order mismatch in call_service",
                        "statement": "Parameter order mismatch causing 403 Forbidden in service",
                        "status": "unverified",
                        "confidence": "low",
                        "supporting_evidence_ids": [],
                        "contradicting_evidence_ids": [],
                    }
                ],
                "unanswered_questions": ["What arguments are passed to call_service?"],
                "missing_evidence": ["Function signature and parameter ordering of call_service"],
            }
        else:
            # Cycle 2: follow-up execution discovers causal defect evidence
            return {
                "execution_status": "completed",
                "workflow_status": "completed",
                "completed_tasks": plan,
                "failed_tasks": [],
                "skipped_tasks": [],
                "unresolved_tasks": [],
                "evidence_pool": [
                    {
                        "evidence_id": "EV-OBS",
                        "source": "api.py",
                        "line": 10,
                        "snippet": "call_service()",
                        "relationship_to_hypothesis": "observational",
                    },
                    {
                        "evidence_id": "EV-CAUSAL",
                        "source": "service.py",
                        "line": 25,
                        "snippet": "def call_service(tenant_id, user_id): ... # caller passed (user_id, tenant_id)",
                        "relationship_to_hypothesis": "causal",
                        "is_defect_evidence": True,
                        "explanation": "Parameter order mismatch reverses user_id and tenant_id",
                    },
                ],
                "aggregated_findings": [
                    {"statement": "Observed call_service invocation", "category": "flow"},
                    {"statement": "Parameter order transposed in caller vs definition", "category": "defect"},
                ],
                "aggregated_hypotheses": [
                    {
                        "hypothesis_id": "HYP-01",
                        "hypothesis_title": "Parameter order mismatch in call_service",
                        "statement": "Parameter order mismatch causing 403 Forbidden in service",
                        "status": "verified",
                        "confidence": "high",
                        "supporting_evidence_ids": ["EV-CAUSAL"],
                        "contradicting_evidence_ids": [],
                    }
                ],
                "unanswered_questions": [],
                "missing_evidence": [],
            }

    monkeypatch.setattr(InvestigationOrchestrator, "execute_plan_single_pass", mock_execute)

    graph = build_graph()

    input_state = {
        "problem": "Parameter order mismatch causing 403 Forbidden in service",
        "expected_behavior": "Service accepts user_id and tenant_id in correct order",
        "actual_behavior": "403 Forbidden returned",
        "workspace_path": "d:/mock/ws",
        "investigation_plan": [{"task_id": "TASK-001", "assigned_agent": "code_investigator", "status": "pending", "scope": ["api.py"]}],
        "lifecycle_status": "execution-ready",
        "plan_validation": {"is_valid": True, "errors": [], "warnings": []},
        "iteration": 1,
        "max_iterations": 3,
        "llm_factory": lambda: None,
    }

    final_state = graph.invoke(input_state)

    # Verified that it executed multiple passes
    assert call_count["passes"] == 2
    # Verified that replan history recorded cycle 1
    assert len(final_state.get("replan_history", []) or final_state.get("investigation_history", [])) >= 1
    # Reviewer approved with sufficient evidence
    assert final_state.get("review_result", {}).get("decision") == "sufficient"
    # Root cause result produced
    assert final_state.get("is_conclusive") is True
    assert final_state.get("status_label") == "Likely root cause"


# =====================================================================
# 6. Problem Archetypes Validation
# =====================================================================

@pytest.mark.parametrize(
    "archetype_problem,causal_code,defect_explanation",
    [
        (
            "Missing environment variable causes crash on startup",
            "API_KEY = os.environ['SECRET_API_KEY']",
            "KeyError raised because SECRET_API_KEY is missing from environment without fallback",
        ),
        (
            "Parameter transposition causes 403 Forbidden",
            "def verify_access(role, resource_id): pass\n# called with verify_access('doc_123', 'admin')",
            "Parameters swapped: resource_id passed where role expected",
        ),
        (
            "Middleware fails to protect admin route",
            "@app.route('/admin')\n# missing @require_admin decorator\ndef admin_dashboard(): return {}",
            "Route handler omitted @require_admin decorator allowing unauthenticated access",
        ),
    ],
)
def test_reviewer_and_root_cause_across_archetypes(archetype_problem, causal_code, defect_explanation):
    """Reviewer and root cause node correctly identify root cause across multiple distinct problem archetypes."""
    reviewer = Reviewer()
    evidence = [
        {
            "evidence_id": "EV-ARCH-01",
            "source": "backend/app.py",
            "line": 50,
            "snippet": causal_code,
            "relationship_to_hypothesis": "causal",
            "is_defect_evidence": True,
            "explanation": defect_explanation,
        }
    ]
    findings = [{"statement": defect_explanation, "category": "defect"}]
    hypotheses = [
        {
            "hypothesis_id": "HYP-ARCH-01",
            "hypothesis_title": archetype_problem,
            "statement": defect_explanation,
            "supporting_evidence_ids": ["EV-ARCH-01"],
            "status": "supported",
            "confidence": "high",
        }
    ]

    res = reviewer.review(
        problem=archetype_problem,
        evidence=evidence,
        findings=findings,
        hypotheses=hypotheses,
        iteration=1,
        max_iterations=3,
    )

    assert res["decision"] == "sufficient"
    assert res["causal_chain_verified"] is True
    assert len(res["established_by_evidence"]) >= 1

    # Run root cause node to verify diagnostic assembly
    state = {
        "problem": archetype_problem,
        "hypotheses": hypotheses,
        "evidence": evidence,
        "findings": findings,
        "review_result": res,
        "established_by_evidence": res["established_by_evidence"],
    }
    rc_result = root_cause_result_node(state)
    assert rc_result["is_conclusive"] is True
    assert len(rc_result["established_by_evidence"]) >= 1
    assert defect_explanation in rc_result["likely_root_cause"] or defect_explanation in rc_result["diagnosis"]["root_cause"]
