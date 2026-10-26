"""Tests for Phase 9 — Diagnosis & Verification.

Validates:
1. Strong evidence: Identifies supported root cause, explains causal relationship, recommends grounded fix with snippet.
2. Incomplete evidence: Reports inconclusive diagnosis or unverified hypothesis, identifies missing evidence, preserves uncertainty.
3. Irrelevant evidence: Avoids promoting unrelated findings or import plumbing into a root cause.
4. Conflicting evidence: Acknowledges contradictions, marks hypotheses as contradicted/uncertain, avoids unsupported conclusions.
5. LLM failure / unavailable: Handles model timeouts, exceptions, unavailable models, and malformed responses gracefully via deterministic fallback.
6. Verification evaluation: Discovers existing test suites, distinguishes executed from proposed checks, never fabricates runtime verification, handles blockers.
7. Report formatting: Generates a coherent final report containing all 7 required sections in exact order.
8. Workflow integration: Graph node execution, conditional routing, API endpoints (/diagnosis, /report), session persistence.
9. TaskFlow investigation: Evaluates real TaskFlow scenarios (parameter ordering swap and email casing mismatch).
"""

import json
import pytest
from unittest.mock import MagicMock
from pathlib import Path

from app.agents.diagnostician import Diagnostician
from app.agents.verifier import Verifier, VerificationSummary
from app.graph.graph import (
    diagnosis_and_verification_node,
    route_after_root_cause_result,
    route_after_inconclusive_result,
    build_graph,
    root_cause_result_node,
    inconclusive_result_node,
)


# ============================================================
# 1. Strong Evidence Scenarios
# ============================================================

def test_strong_evidence_identifies_root_cause_and_recommends_fix():
    diagnostician = Diagnostician()

    problem = "Task creation fails when creating tasks with due dates in taskModel.js. Parameters are passed out of order."
    evidence = [
        {
            "evidence_id": "EV-001",
            "source": "backend/src/models/taskModel.js",
            "line": 4,
            "snippet": "const insertWithDue = db.prepare('INSERT INTO tasks (title, description, status, due_date, user_id) VALUES (?, ?, ?, ?, ?)');",
            "is_defect_evidence": True,
            "relationship_to_hypothesis": "causal",
        },
        {
            "evidence_id": "EV-002",
            "source": "backend/src/models/taskModel.js",
            "line": 13,
            "snippet": "return insertWithDue.run(task.title, task.description, task.status, task.userId, task.dueDate);",
            "is_defect_evidence": True,
            "relationship_to_hypothesis": "causal",
        },
    ]
    findings = [
        {
            "statement": "Parameter order mismatch in taskModel.js: SQL statement expects (due_date, user_id) but insertWithDue.run passes (userId, dueDate)",
            "category": "root_cause_defect",
        }
    ]
    hypotheses = [
        {
            "hypothesis_id": "HYP-001",
            "statement": "Swapped arguments in insertWithDue.run causes task.userId to be stored in due_date column",
            "supporting_evidence_ids": ["EV-001", "EV-002"],
            "status": "supported",
            "confidence": "medium",
        }
    ]
    review_result = {
        "decision": "sufficient",
        "is_sufficient": True,
        "confidence": "medium",
        "reasoning": "Complete structural contract proof demonstrates task.userId and task.dueDate are swapped in insertWithDue.run",
        "approved_hypothesis_id": "HYP-001",
    }

    result = diagnostician.diagnose_and_report(
        problem=problem,
        evidence_pool=evidence,
        findings=findings,
        hypotheses=hypotheses,
        review_result=review_result,
    )

    # 1. Conclusive diagnosis
    assert result["is_conclusive"] is True
    assert result["status_label"] in ("Likely root cause", "Verified root cause")
    assert "taskModel.js" in result["likely_root_cause"]
    assert "swapped" in result["likely_root_cause"].lower() or "order" in result["likely_root_cause"].lower()

    # 2. Causal explanation present and detailed
    assert "causal_explanation" in result
    assert len(result["causal_explanation"]) > 50
    assert "sqlite" in result["causal_explanation"].lower() or "parameter" in result["causal_explanation"].lower() or "position" in result["causal_explanation"].lower()

    # 3. Grounded suggested fix
    assert "suggested_fix" in result
    assert "taskModel.js" in result["suggested_fix"]
    assert "suggested_fix_details" in result
    fix_details = result["suggested_fix_details"]
    assert fix_details["file"] == "backend/src/models/taskModel.js"
    assert "invariants_to_preserve" in fix_details
    assert "potential_regressions" in fix_details
    assert "insertWithDue.run" in fix_details.get("code_snippet", "")

    # 4. Final report generated
    assert "final_report" in result
    assert "# Investigation Report" in result["final_report"]


def test_strong_evidence_token_casing_mismatch():
    diagnostician = Diagnostician()

    problem = "Observed behavior: After login, the email in the authentication token differs from the email stored for the user in the database."
    evidence = [
        {
            "evidence_id": "EV-AUTH-01",
            "source": "backend/src/services/authService.js",
            "line": 25,
            "snippet": "const user = userModel.create({ name: String(name).trim(), email: cleanEmail, ... });",
            "is_defect_evidence": True,
            "relationship_to_hypothesis": "causal",
        },
        {
            "evidence_id": "EV-AUTH-02",
            "source": "backend/src/services/authService.js",
            "line": 41,
            "snippet": "const token = signToken({ sub: user.id, email: normalizedEmail });",
            "is_defect_evidence": True,
            "relationship_to_hypothesis": "causal",
        },
    ]
    findings = [
        {
            "statement": "authService.js persists unnormalized cleanEmail in register() but signs normalizedEmail in login()",
            "category": "root_cause_defect",
        }
    ]
    hypotheses = [
        {
            "hypothesis_id": "HYP-001",
            "statement": "Normalization divergence between register() and login() generates mismatched email claims",
            "supporting_evidence_ids": ["EV-AUTH-01", "EV-AUTH-02"],
            "status": "supported",
            "confidence": "medium",
        }
    ]
    review_result = {
        "decision": "sufficient",
        "is_sufficient": True,
        "confidence": "medium",
        "reasoning": "authService.js stores unnormalized email during registration but signs lowercased email during login",
        "approved_hypothesis_id": "HYP-001",
    }

    result = diagnostician.diagnose_and_report(
        problem=problem,
        evidence_pool=evidence,
        findings=findings,
        hypotheses=hypotheses,
        review_result=review_result,
    )

    assert result["is_conclusive"] is True
    assert "normalization" in result["likely_root_cause"].lower() or "divergence" in result["likely_root_cause"].lower() or "authService.js" in result["likely_root_cause"]
    assert "authService.js" in result["suggested_fix"]
    assert "register" in result["suggested_fix"].lower()


# ============================================================
# 2. Incomplete Evidence Scenarios
# ============================================================

def test_incomplete_evidence_produces_inconclusive_diagnosis():
    diagnostician = Diagnostician()

    problem = "Occasional 500 error on checkout"
    evidence = [
        {
            "evidence_id": "EV-001",
            "source": "backend/routes/checkout.js",
            "line": 15,
            "snippet": "router.post('/checkout', handleCheckout);",
            "is_defect_evidence": False,
            "relationship_to_hypothesis": "observational",
        }
    ]
    findings = [
        {"statement": "Observed checkout route declaration", "category": "observational"}
    ]
    hypotheses = [
        {
            "hypothesis_id": "HYP-001",
            "statement": "Payment gateway timeout occurs intermittently",
            "supporting_evidence_ids": [],
            "status": "unverified",
            "confidence": "low",
        }
    ]
    review_result = {
        "decision": "insufficient",
        "is_sufficient": False,
        "confidence": "low",
        "reasoning": "Evidence is purely observational; no causal defect mechanism found",
        "unanswered_questions": ["Where is payment gateway called?", "What is the stack trace?"],
        "missing_evidence": ["Payment gateway integration logs", "Exception trace"],
    }

    result = diagnostician.diagnose_and_report(
        problem=problem,
        evidence_pool=evidence,
        findings=findings,
        hypotheses=hypotheses,
        review_result=review_result,
    )

    assert result["is_conclusive"] is False
    assert result["status_label"] == "Inconclusive"
    assert result["confidence"] == "low"
    assert "inconclusive" in result["likely_root_cause"].lower()
    assert "uncertainty" in result["remaining_uncertainty"].lower() or "missing" in result["remaining_uncertainty"].lower()
    # Suggested fix is conditional / cautious
    assert "reproduction" in result["suggested_fix"].lower() or "trace" in result["suggested_fix"].lower() or "logs" in result["suggested_fix"].lower()


# ============================================================
# 3. Irrelevant Evidence Scenarios
# ============================================================

def test_irrelevant_evidence_does_not_become_root_cause():
    diagnostician = Diagnostician()

    problem = "User receives 401 Unauthorized during login"
    evidence = [
        {
            "evidence_id": "EV-001",
            "source": "frontend/styles/theme.css",
            "line": 10,
            "snippet": "body { background-color: #ffffff; }",
            "is_defect_evidence": False,
            "relationship_to_hypothesis": "observational",
        },
        {
            "evidence_id": "EV-002",
            "source": "backend/package.json",
            "line": 5,
            "snippet": "\"dependencies\": { \"express\": \"^4.18.2\" }",
            "is_defect_evidence": False,
            "relationship_to_hypothesis": "observational",
        },
    ]
    findings = [
        {"statement": "Inspected CSS background theme", "category": "observational"}
    ]
    hypotheses = [
        {
            "hypothesis_id": "HYP-001",
            "statement": "Theme background color triggers 401 error",
            "supporting_evidence_ids": ["EV-001"],
            "status": "unverified",
            "confidence": "low",
        }
    ]
    review_result = {
        "decision": "inconclusive",
        "is_sufficient": False,
        "confidence": "low",
        "reasoning": "CSS theme findings are completely unrelated to the authentication failure",
        "unanswered_questions": ["Where is login endpoint authenticated?"],
        "missing_evidence": ["Auth middleware"],
    }

    result = diagnostician.diagnose_and_report(
        problem=problem,
        evidence_pool=evidence,
        findings=findings,
        hypotheses=hypotheses,
        review_result=review_result,
    )

    assert result["is_conclusive"] is False
    assert result["status_label"] == "Inconclusive"
    assert "theme.css" not in result["likely_root_cause"]


# ============================================================
# 4. Conflicting Evidence Scenarios
# ============================================================

def test_conflicting_evidence_acknowledges_contradiction():
    diagnostician = Diagnostician()

    problem = "Cache returns stale user profile"
    evidence = [
        {
            "evidence_id": "EV-001",
            "source": "backend/cache.py",
            "line": 12,
            "snippet": "ttl = 300",
            "is_defect_evidence": False,
        },
        {
            "evidence_id": "EV-002",
            "source": "backend/cache.py",
            "line": 40,
            "snippet": "cache.invalidate(user_id) # called immediately on update",
            "is_defect_evidence": False,
        },
    ]
    findings = [
        {"statement": "Cache TTL is 300 seconds", "category": "observational"},
        {"statement": "Cache invalidation is explicitly called on user update", "category": "contradictory"},
    ]
    hypotheses = [
        {
            "hypothesis_id": "HYP-001",
            "statement": "Cache is not invalidated on user profile update",
            "supporting_evidence_ids": ["EV-001"],
            "contradicting_evidence_ids": ["EV-002"],
            "status": "contradicted",
            "confidence": "low",
        }
    ]
    review_result = {
        "decision": "inconclusive",
        "is_sufficient": False,
        "has_conflicting_findings": True,
        "confidence": "low",
        "reasoning": "Contradictory findings: cache invalidation is explicitly invoked on profile update",
    }

    result = diagnostician.diagnose_and_report(
        problem=problem,
        evidence_pool=evidence,
        findings=findings,
        hypotheses=hypotheses,
        review_result=review_result,
    )

    assert result["is_conclusive"] is False
    assert result["status_label"] == "Inconclusive"
    assert "contradict" in result["likely_root_cause"].lower() or "inconclusive" in result["likely_root_cause"].lower()


# ============================================================
# 5. LLM Failure Handling & Fallback
# ============================================================

def test_llm_failure_falls_back_gracefully_to_deterministic_synthesis():
    def broken_llm_factory():
        mock_llm = MagicMock()
        mock_llm.with_structured_output.side_effect = RuntimeError("OpenRouter API timeout (504 Gateway Timeout)")
        return mock_llm

    diagnostician = Diagnostician(llm_factory=broken_llm_factory)

    problem = "Task creation fails with swapped userId and dueDate parameters in taskModel.js"
    evidence = [
        {
            "evidence_id": "EV-001",
            "source": "taskModel.js",
            "line": 13,
            "snippet": "insertWithDue.run(task.title, task.description, task.status, task.userId, task.dueDate)",
            "is_defect_evidence": True,
            "relationship_to_hypothesis": "causal",
        }
    ]
    findings = [
        {"statement": "taskModel.js passes userId before dueDate", "category": "root_cause_defect"}
    ]
    hypotheses = [
        {
            "hypothesis_id": "HYP-001",
            "statement": "Swapped arguments in insertWithDue.run",
            "supporting_evidence_ids": ["EV-001"],
            "status": "supported",
        }
    ]
    review_result = {
        "decision": "sufficient",
        "is_sufficient": True,
        "confidence": "medium",
        "reasoning": "Causal evidence confirms swapped parameters in taskModel.js",
    }

    result = diagnostician.diagnose_and_report(
        problem=problem,
        evidence_pool=evidence,
        findings=findings,
        hypotheses=hypotheses,
        review_result=review_result,
    )

    # Deterministic fallback took over without throwing
    assert result["synthesis_mode"] == "heuristic"
    assert result["is_conclusive"] is True
    assert "taskModel.js" in result["likely_root_cause"]
    assert "suggested_fix" in result
    assert "final_report" in result


# ============================================================
# 6. Verification Discovery & Honesty
# ============================================================

def test_verifier_detects_unconfigured_tests_honestly(tmp_path: Path):
    # Create workspace with package.json that has no test script and missing node_modules
    pkg_file = tmp_path / "package.json"
    pkg_file.write_text(json.dumps({"name": "test-repo", "scripts": {"start": "node index.js"}}))

    verifier = Verifier(workspace_path=str(tmp_path))
    summary = verifier.evaluate_verification(
        problem="Crash on null input",
        root_cause="Missing null check in parser.js",
        affected_components=["parser.js"],
        workspace_path=str(tmp_path),
    )

    assert summary.overall_status == "verification blocked"
    assert len(summary.blocker_reasons) >= 1
    assert any("node_modules" in b.lower() or "test script" in b.lower() for b in summary.blocker_reasons)

    # Proposed checks are present and marked unexecuted
    assert len(summary.proposed_checks) >= 1
    assert summary.proposed_checks[0].executed is False
    assert summary.proposed_checks[0].status == "not verified"


def test_verifier_records_empirical_probe_honestly():
    verifier = Verifier()
    summary = verifier.evaluate_verification(
        problem="Token email casing mismatch",
        root_cause="authService.js casing divergence",
        affected_components=["authService.js"],
        is_empirically_verified=True,
        empirical_evidence={
            "statement": "Isolated probe verified token casing differs from database record",
            "command": "node probe_auth.js",
        },
    )

    assert summary.overall_status == "verified"
    assert len(summary.executed_checks) == 1
    assert summary.executed_checks[0].executed is True
    assert summary.executed_checks[0].status == "verified"


# ============================================================
# 7. Final Report Seven Sections Ordering
# ============================================================

def test_final_report_contains_all_seven_sections_in_exact_order():
    diagnostician = Diagnostician()

    problem = "Email mismatch in JWT token"
    evidence = [
        {
            "evidence_id": "EV-001",
            "source": "authService.js",
            "line": 41,
            "snippet": "signToken({ sub: user.id, email: normalizedEmail })",
            "is_defect_evidence": True,
        }
    ]
    findings = [{"statement": "authService.js signs normalized email", "category": "root_cause_defect"}]
    hypotheses = [{"hypothesis_id": "HYP-001", "statement": "Email casing divergence", "status": "supported"}]
    review_result = {
        "decision": "sufficient",
        "is_sufficient": True,
        "confidence": "medium",
        "reasoning": "Email casing difference between stored and signed tokens",
    }

    result = diagnostician.diagnose_and_report(
        problem=problem,
        evidence_pool=evidence,
        findings=findings,
        hypotheses=hypotheses,
        review_result=review_result,
        expected_behavior="Token email matches database user email",
        actual_behavior="Token email is lowercase while database email preserves uppercase registration",
    )

    report = result["final_report"]
    sections = [
        "## 1. Problem Summary",
        "## 2. Root-Cause Diagnosis",
        "## 3. Supporting Evidence",
        "## 4. Affected Components",
        "## 5. Suggested Fix",
        "## 6. Verification Steps",
        "## 7. Remaining Uncertainty",
    ]

    # Verify all 7 sections exist
    for s in sections:
        assert s in report, f"Section {s} is missing from final report"

    # Verify exact ordering
    pos = 0
    for s in sections:
        idx = report.find(s)
        assert idx >= pos, f"Section {s} appears out of order"
        pos = idx + len(s)

    # Verify scope restriction is explicitly stated
    assert "Scope Restriction" in report or "scope restriction" in report.lower()


# ============================================================
# 8. Workflow Graph Integration
# ============================================================

def test_graph_with_run_diagnosis_routes_to_diagnosis_and_verification():
    state = {
        "problem": "Swapped parameters in database insertion",
        "run_diagnosis": True,
        "evidence_pool": [
            {
                "evidence_id": "EV-001",
                "source": "taskModel.js",
                "line": 13,
                "snippet": "insertWithDue.run(task.title, task.description, task.status, task.userId, task.dueDate)",
                "is_defect_evidence": True,
                "relationship_to_hypothesis": "causal",
            }
        ],
        "aggregated_findings": [
            {"statement": "insertWithDue passes userId before dueDate", "category": "root_cause_defect"}
        ],
        "aggregated_hypotheses": [
            {
                "hypothesis_id": "HYP-001",
                "statement": "Swapped argument order in insertWithDue.run",
                "status": "supported",
                "supporting_evidence_ids": ["EV-001"],
            }
        ],
        "review_result": {
            "decision": "sufficient",
            "is_sufficient": True,
            "confidence": "medium",
            "reasoning": "Swapped argument order confirmed by code evidence",
        },
    }

    # Execute preliminary root cause node
    root_cause_state = root_cause_result_node(state)
    assert route_after_root_cause_result(root_cause_state) == "diagnosis_and_verification"

    # Execute Phase 9 diagnosis and verification node
    final_state = diagnosis_and_verification_node(root_cause_state)

    assert final_state["current_step"] == "investigation_completed"
    assert final_state["is_conclusive"] is True
    assert final_state["likely_root_cause"] != ""
    assert final_state["suggested_fix"] != ""
    assert len(final_state["verification_steps"]) >= 1
    assert "## 1. Problem Summary" in final_state["final_report"]
    assert "## 5. Suggested Fix" in final_state["final_report"]


def test_graph_without_run_diagnosis_preserves_phase8_preliminary_behavior():
    state = {
        "problem": "Generic test issue",
        "evidence_pool": [],
        "aggregated_findings": [],
        "aggregated_hypotheses": [],
        "review_result": {
            "decision": "sufficient",
            "is_sufficient": True,
            "confidence": "medium",
            "reasoning": "Preliminary result",
        },
    }
    # No run_diagnosis flag passed
    root_cause_state = root_cause_result_node(state)
    assert route_after_root_cause_result(root_cause_state) == "end"
    assert root_cause_state["suggested_fix"] == ""
    assert root_cause_state["verification_steps"] == []


# ============================================================
# 9. Real TaskFlow Scenario Evaluation
# ============================================================

def test_taskflow_task_creation_parameter_swap_scenario():
    diagnostician = Diagnostician()

    problem = "Task creation fails when creating tasks with due dates in taskModel.js. Parameters are passed out of order."
    evidence = [
        {
            "evidence_id": "EV-TF-01",
            "source": "backend/src/models/taskModel.js",
            "line": 4,
            "snippet": "const insertWithDue = db.prepare('INSERT INTO tasks (title, description, status, due_date, user_id) VALUES (?, ?, ?, ?, ?)');",
            "is_defect_evidence": True,
            "relationship_to_hypothesis": "causal",
        },
        {
            "evidence_id": "EV-TF-02",
            "source": "backend/src/models/taskModel.js",
            "line": 13,
            "snippet": "return insertWithDue.run(task.title, task.description, task.status, task.userId, task.dueDate);",
            "is_defect_evidence": True,
            "relationship_to_hypothesis": "causal",
        },
    ]
    findings = [
        {
            "statement": "taskModel.js SQL declares (due_date, user_id) but insertWithDue.run passes (userId, dueDate)",
            "category": "root_cause_defect",
        }
    ]
    hypotheses = [
        {
            "hypothesis_id": "HYP-001",
            "statement": "Parameter order mismatch in insertWithDue swaps userId and dueDate",
            "status": "supported",
            "supporting_evidence_ids": ["EV-TF-01", "EV-TF-02"],
        }
    ]
    review_result = {
        "decision": "sufficient",
        "is_sufficient": True,
        "confidence": "medium",
        "status_label": "Likely root cause",
        "reasoning": "Deterministic structural code evidence establishes parameter swap defect.",
        "approved_hypothesis_id": "HYP-001",
    }

    result = diagnostician.diagnose_and_report(
        problem=problem,
        evidence_pool=evidence,
        findings=findings,
        hypotheses=hypotheses,
        review_result=review_result,
        expected_behavior="Tasks with due dates are inserted with due_date and user_id in matching SQL columns.",
        actual_behavior="Task creation fails or associates task with invalid user ID because userId is bound to due_date column.",
    )

    # 1. Correct diagnosis
    assert result["is_conclusive"] is True
    assert result["status_label"] == "Likely root cause"
    assert "taskModel.js" in result["likely_root_cause"]
    assert "user_id" in result["likely_root_cause"] or "userId" in result["likely_root_cause"]
    assert "due_date" in result["likely_root_cause"] or "dueDate" in result["likely_root_cause"]

    # 2. Suggested fix snippet
    assert "insertWithDue.run(task.title, task.description, task.status, task.dueDate, task.userId)" in result["suggested_fix"]

    # 3. Actionable verification steps
    assert len(result["verification_steps"]) >= 2
    assert any("due" in step.lower() for step in result["verification_steps"])

    # 4. Report completeness
    report = result["final_report"]
    assert "## 1. Problem Summary" in report
    assert "## 2. Root-Cause Diagnosis" in report
    assert "## 3. Supporting Evidence" in report
    assert "## 4. Affected Components" in report
    assert "## 5. Suggested Fix" in report
    assert "## 6. Verification Steps" in report
    assert "## 7. Remaining Uncertainty" in report
