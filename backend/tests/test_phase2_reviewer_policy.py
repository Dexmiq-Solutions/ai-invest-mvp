"""Focused unit and integration tests for Phase 2: Reviewer Evaluation & Root-Cause Conclusiveness Policy (Option B).

Covers:
1. Complete deterministic structural code evidence (SQL declaration + invocation parameter mismatch)
   evaluates as sufficient with medium confidence and "Likely root cause".
2. Reviewer provides an explicit causal explanation of the defect mechanism to the reported symptom.
3. Incomplete structural evidence (declaration only, or invocation only) produces an appropriately
   insufficient/unverified review.
4. Unrelated or domain-mismatched hypotheses (e.g. auth login for task creation) cannot establish sufficiency.
5. Crash/exception defects without stack traces/error logs remain insufficient.
6. Empirically verified findings (test probes, runtime verification) produce high confidence
   and "Verified root cause" status label.
7. Conclusiveness routing in LangGraph routes sufficient structural proof to root_cause_result_node
   with is_conclusive=True, confidence=medium, status_label="Likely root cause".
"""

import pytest
from unittest.mock import MagicMock

from app.agents.reviewer import (
    Reviewer,
    is_structural_contract_evidence_complete,
    is_hypothesis_relevant_to_problem,
)
from app.graph.graph import (
    root_cause_result_node,
    inconclusive_result_node,
    route_after_reviewer,
)


# ==============================================================================
# 1. Structural Contract Completeness Tests
# ==============================================================================

def test_structural_contract_evidence_complete_with_declaration_and_invocation():
    """Verify that having both SQL declaration and invocation satisfies contract completeness."""
    hypothesis = {
        "hypothesis_id": "HYP-001",
        "statement": "In taskModel.js, insertWithDue passes dueDate and userId parameters in swapped order relative to SQL columns",
    }
    evidence = [
        {
            "evidence_id": "EV-001",
            "source": "backend/src/models/taskModel.js",
            "line": 3,
            "snippet": "const insertWithDue = db.prepare('INSERT INTO tasks (title, description, status, due_date, user_id) VALUES (?, ?, ?, ?, ?)');",
        },
        {
            "evidence_id": "EV-002",
            "source": "backend/src/models/taskModel.js",
            "line": 13,
            "snippet": "return insertWithDue.run(task.title, task.description, task.status, task.userId, task.dueDate);",
        },
    ]

    is_complete, reason = is_structural_contract_evidence_complete(hypothesis, evidence)
    assert is_complete is True
    assert "Complete structural contract" in reason


def test_structural_contract_evidence_incomplete_when_invocation_missing():
    """Declaration alone must be flagged as incomplete for parameter mismatch hypothesis."""
    hypothesis = {
        "hypothesis_id": "HYP-001",
        "statement": "Parameter mismatch in insertWithDue: userId and dueDate are swapped",
    }
    evidence = [
        {
            "evidence_id": "EV-001",
            "source": "backend/src/models/taskModel.js",
            "line": 3,
            "snippet": "const insertWithDue = db.prepare('INSERT INTO tasks (title, description, status, due_date, user_id) VALUES (?, ?, ?, ?, ?)');",
        }
    ]

    is_complete, reason = is_structural_contract_evidence_complete(hypothesis, evidence)
    assert is_complete is False
    assert "invocation/call site is missing" in reason


def test_structural_contract_evidence_incomplete_when_declaration_missing():
    """Invocation alone must be flagged as incomplete for parameter mismatch hypothesis."""
    hypothesis = {
        "hypothesis_id": "HYP-001",
        "statement": "Parameter mismatch in insertWithDue: parameters passed out of order",
    }
    evidence = [
        {
            "evidence_id": "EV-002",
            "source": "backend/src/models/taskModel.js",
            "line": 13,
            "snippet": "return insertWithDue.run(task.title, task.description, task.status, task.userId, task.dueDate);",
        }
    ]

    is_complete, reason = is_structural_contract_evidence_complete(hypothesis, evidence)
    assert is_complete is False
    assert "contract declaration is missing" in reason


# ==============================================================================
# 2. Reviewer Evaluation on TaskFlow Task-Creation Bug
# ==============================================================================

def test_reviewer_evaluates_taskflow_structural_defect_as_sufficient_medium_confidence():
    """Test that TaskFlow task-creation bug with declaration + invocation evaluates as sufficient with medium confidence."""
    reviewer = Reviewer()

    problem = "Task creation fails when creating tasks with due dates in taskModel.js. Parameters are passed out of order."
    orig_problem = problem
    evidence = [
        {
            "evidence_id": "EV-CODE-001",
            "source": "taskflow/backend/src/models/taskModel.js",
            "line": 3,
            "matched_text": "const insertWithDue = db.prepare(\n  'INSERT INTO tasks (title, description, status, due_date, user_id) VALUES (?, ?, ?, ?, ?)'\n);",
            "snippet": "const insertWithDue = db.prepare(\n  'INSERT INTO tasks (title, description, status, due_date, user_id) VALUES (?, ?, ?, ?, ?)'\n);",
            "is_defect_evidence": True,
        },
        {
            "evidence_id": "EV-CODE-002",
            "source": "taskflow/backend/src/models/taskModel.js",
            "line": 13,
            "matched_text": "return insertWithDue.run(task.title, task.description, task.status, task.userId, task.dueDate);",
            "snippet": "return insertWithDue.run(task.title, task.description, task.status, task.userId, task.dueDate);",
            "is_defect_evidence": True,
        },
    ]
    findings = [
        {
            "statement": "Parameter mismatch in taskModel.js: insertWithDue prepared statement expects due_date at column index 4 and user_id at index 5, but invocation passes task.userId 4th and task.dueDate 5th",
            "evidence_ids": ["EV-CODE-001", "EV-CODE-002"],
        }
    ]
    hypotheses = [
        {
            "hypothesis_id": "HYP-001",
            "hypothesis_title": "Column and parameter order mismatch in insertWithDue",
            "statement": "In taskModel.js, insertWithDue passes dueDate and userId parameters in swapped order relative to SQL columns",
            "supporting_evidence_ids": ["EV-CODE-001", "EV-CODE-002"],
            "contradicting_evidence_ids": [],
            "status": "supported",
        }
    ]

    result = reviewer.review(
        problem=problem,
        evidence=evidence,
        findings=findings,
        hypotheses=hypotheses,
        iteration=1,
        max_iterations=3,
        original_problem=orig_problem,
    )

    assert result["decision"] == "sufficient"
    assert result["is_sufficient"] is True
    # Under Option B: deterministic structural code proof MUST be medium confidence
    assert result["confidence"] == "medium"
    assert result["approved_hypothesis_id"] == "HYP-001"
    # Reviewer must explain causal relationship in reasoning
    assert "reasoning" in result
    assert "parameter" in result["reasoning"].lower() or "mismatch" in result["reasoning"].lower() or "order" in result["reasoning"].lower()
    assert result["causal_chain_verified"] is True
    assert result["further_investigation_necessary"] is False


def test_reviewer_rejects_incomplete_evidence_for_structural_defect():
    """If only declaration is present, Reviewer must evaluate as insufficient."""
    reviewer = Reviewer()

    problem = "Task creation fails when creating tasks with due dates in taskModel.js. Parameters are passed out of order."
    evidence = [
        {
            "evidence_id": "EV-CODE-001",
            "source": "taskflow/backend/src/models/taskModel.js",
            "line": 3,
            "matched_text": "const insertWithDue = db.prepare('INSERT INTO tasks (title, description, status, due_date, user_id) VALUES (?, ?, ?, ?, ?)');",
            "snippet": "const insertWithDue = db.prepare('INSERT INTO tasks (title, description, status, due_date, user_id) VALUES (?, ?, ?, ?, ?)');",
        }
    ]
    hypotheses = [
        {
            "hypothesis_id": "HYP-001",
            "statement": "In taskModel.js, insertWithDue passes dueDate and userId parameters in swapped order",
            "supporting_evidence_ids": ["EV-CODE-001"],
            "contradicting_evidence_ids": [],
            "status": "unverified",
        }
    ]

    result = reviewer.review(
        problem=problem,
        evidence=evidence,
        findings=[],
        hypotheses=hypotheses,
        iteration=1,
        max_iterations=3,
    )

    assert result["decision"] == "insufficient"
    assert result["is_sufficient"] is False
    assert result["confidence"] == "low"
    assert result["further_investigation_necessary"] is True
    assert any("invocation" in me.lower() or "call site" in me.lower() for me in result["missing_evidence"])


def test_reviewer_rejects_unrelated_hypotheses():
    """Unrelated hypothesis (auth login for task creation problem) cannot establish sufficiency."""
    reviewer = Reviewer()

    problem = "Task creation fails when creating tasks with due dates in taskModel.js."
    evidence = [
        {
            "evidence_id": "EV-AUTH-001",
            "source": "taskflow/backend/src/routes/authRoutes.js",
            "line": 10,
            "snippet": "router.post('/login', validateLogin, (req, res) => {});",
        }
    ]
    hypotheses = [
        {
            "hypothesis_id": "HYP-AUTH-001",
            "hypothesis_title": "Login authentication failure",
            "statement": "Login token generation does not match stored user email",
            "supporting_evidence_ids": ["EV-AUTH-001"],
            "status": "supported",
        }
    ]

    result = reviewer.review(
        problem=problem,
        evidence=evidence,
        findings=[],
        hypotheses=hypotheses,
        iteration=1,
        max_iterations=3,
    )

    assert result["decision"] == "insufficient"
    assert result["is_sufficient"] is False
    assert result["confidence"] == "low"
    assert result["hypothesis_evaluations"][0]["status"] == "unverified"


def test_reviewer_preserves_high_confidence_for_empirically_verified_findings():
    """Empirically verified findings (test probes, runtime reproducer) yield high confidence."""
    reviewer = Reviewer()

    problem = "Server crashes on startup due to missing environment variable DATABASE_URL"
    evidence = [
        {
            "evidence_id": "EV-001",
            "source": "backend/src/config.py",
            "line": 15,
            "snippet": "DATABASE_URL = os.environ['DATABASE_URL']",
            "is_defect_evidence": True,
        }
    ]
    hypotheses = [
        {
            "hypothesis_id": "HYP-001",
            "statement": "Missing default for DATABASE_URL raises KeyError on startup",
            "supporting_evidence_ids": ["EV-001"],
            "status": "verified",  # Empirically verified
            "is_empirically_verified": True,
            "confidence": "high",
        }
    ]

    result = reviewer.review(
        problem=problem,
        evidence=evidence,
        findings=[],
        hypotheses=hypotheses,
        iteration=1,
        max_iterations=3,
    )

    assert result["decision"] == "sufficient"
    assert result["is_sufficient"] is True
    assert result["confidence"] == "high"


# ==============================================================================
# 3. LangGraph Result Node Alignment Tests
# ==============================================================================

def test_root_cause_result_node_sets_likely_root_cause_for_structural_defect():
    """Deterministic structural code proof sets status_label='Likely root cause' with medium confidence."""
    state = {
        "problem": "Task creation fails when creating tasks with due dates in taskModel.js.",
        "hypotheses": [
            {
                "hypothesis_id": "HYP-001",
                "statement": "In taskModel.js, insertWithDue passes dueDate and userId in swapped order relative to SQL columns",
                "status": "supported",
                "confidence": "medium",
                "supporting_evidence_ids": ["EV-001", "EV-002"],
            }
        ],
        "evidence": [
            {"evidence_id": "EV-001", "source": "taskModel.js", "line": 3, "snippet": "const insertWithDue = db.prepare(...);"},
            {"evidence_id": "EV-002", "source": "taskModel.js", "line": 13, "snippet": "insertWithDue.run(...);"},
        ],
        "review_result": {
            "decision": "sufficient",
            "is_sufficient": True,
            "confidence": "medium",
            "reasoning": "Complete SQL declaration and invocation confirm swapped userId and dueDate parameters.",
            "approved_hypothesis_id": "HYP-001",
        },
    }

    result = root_cause_result_node(state)

    assert result["is_conclusive"] is True
    assert result["confidence"] == "medium"
    assert result["status_label"] == "Likely root cause"
    assert result["diagnosis"]["status_label"] == "Likely root cause"
    assert result["diagnosis"]["confidence"] == "medium"
    assert "swapped" in result["likely_root_cause"] or "taskModel" in result["likely_root_cause"]
    assert len(result["diagnosis"]["supporting_evidence"]) == 2


def test_root_cause_result_node_sets_verified_root_cause_for_empirical_verification():
    """Empirical verification sets status_label='Verified root cause' with high confidence."""
    state = {
        "problem": "App crashes with unhandled KeyError on startup",
        "hypotheses": [
            {
                "hypothesis_id": "HYP-001",
                "statement": "Missing DATABASE_URL environment variable causes crash",
                "status": "verified",
                "confidence": "high",
                "is_empirically_verified": True,
                "supporting_evidence_ids": ["EV-001"],
            }
        ],
        "evidence": [
            {"evidence_id": "EV-001", "source": "config.py", "line": 10, "snippet": "os.environ['DATABASE_URL']"},
        ],
        "review_result": {
            "decision": "sufficient",
            "is_sufficient": True,
            "confidence": "high",
            "is_empirically_verified": True,
            "reasoning": "Direct reproducer execution verifies KeyError when DATABASE_URL is unset.",
            "approved_hypothesis_id": "HYP-001",
        },
    }

    result = root_cause_result_node(state)

    assert result["is_conclusive"] is True
    assert result["confidence"] == "high"
    assert result["status_label"] == "Verified root cause"
    assert result["diagnosis"]["status_label"] == "Verified root cause"
    assert result["diagnosis"]["confidence"] == "high"


def test_inconclusive_result_node_sets_inconclusive_low_confidence():
    """Inconclusive result node sets is_conclusive=False, confidence=low, status_label='Inconclusive'."""
    state = {
        "problem": "Intermittent latency spikes",
        "review_result": {
            "decision": "inconclusive",
            "is_sufficient": False,
            "confidence": "low",
            "reasoning": "Maximum iterations reached without causal evidence.",
        },
    }

    result = inconclusive_result_node(state)

    assert result["is_conclusive"] is False
    assert result["confidence"] == "low"
    assert result["status_label"] == "Inconclusive"
    assert result["diagnosis"]["status_label"] == "Inconclusive"
    assert result["diagnosis"]["confidence"] == "low"


def test_conclusiveness_routing():
    """Verify route_after_reviewer routes sufficient reviews to root_cause_result."""
    state_sufficient = {
        "review_result": {"decision": "sufficient", "is_sufficient": True, "confidence": "medium"},
        "investigation_iterations": 1,
        "max_iterations": 3,
    }
    assert route_after_reviewer(state_sufficient) == "root_cause_result"

    state_insufficient = {
        "review_result": {"decision": "insufficient", "is_sufficient": False, "confidence": "low"},
        "investigation_iterations": 1,
        "max_iterations": 3,
    }
    assert route_after_reviewer(state_insufficient) == "replanner"

    state_limit = {
        "review_result": {"decision": "insufficient", "is_sufficient": False, "confidence": "low"},
        "investigation_iterations": 3,
        "max_iterations": 3,
    }
    assert route_after_reviewer(state_limit) == "inconclusive_result"
