import pytest
from fastapi.testclient import TestClient
from pathlib import Path
import json

from app.api import app, sessions, _ensure_persisted_sessions_loaded

client = TestClient(app)

SAMPLE_SESSION_ID = "9642a195-b48c-45aa-9780-859bd88e341b"

def _ensure_sample_session():
    _ensure_persisted_sessions_loaded()
    if SAMPLE_SESSION_ID not in sessions:
        sessions[SAMPLE_SESSION_ID] = {
            "session_id": SAMPLE_SESSION_ID,
            "problem": "The email in the login token does not match the email stored for the user.",
            "current_step": "investigation_completed",
            "context_status": "sufficient",
            "execution_status": "completed",
            "workflow_status": "aggregated",
            "investigation_status": "inconclusive",
            "status_label": "Unverified hypothesis",
            "is_conclusive": False,
            "confidence": "medium",
            "investigation_iterations": 1,
            "findings": [{"statement": "Observed email mismatch", "category": "Confirmed Finding"}],
            "evidence": [{"evidence_id": "EV-001", "source": "authService.js", "line": 10, "snippet": "const token = jwt.sign..."}],
            "hypotheses": [{
                "hypothesis_id": "HYP-001",
                "hypothesis_title": "Token discrepancy",
                "possible_cause": "Email casing mismatch",
                "reasoning": "Observed mismatch",
                "supporting_evidence": [{"evidence_id": "EV-001", "source": "authService.js", "line": 10, "snippet": "const token = jwt.sign..."}],
                "status": "needs_validation",
            }],
            "investigation_plan": [{"task_id": "TASK-001", "assigned_agent": "code_investigator", "status": "completed"}],
        }

def test_list_investigations_endpoint():
    """Verify GET /api/investigations returns persisted investigations with correct schema."""
    _ensure_sample_session()
    res = client.get("/api/investigations")
    assert res.status_code == 200
    data = res.json()
    assert "investigations" in data
    assert isinstance(data["investigations"], list)
    assert len(data["investigations"]) > 0

    target = next((item for item in data["investigations"] if item["session_id"] == SAMPLE_SESSION_ID), data["investigations"][0])
    assert "session_id" in target
    assert "problem" in target
    assert "current_step" in target
    assert "status_label" in target
    assert "is_conclusive" in target
    assert "confidence" in target
    assert "findings_count" in target
    assert "evidence_count" in target
    assert "tasks_count" in target
    assert target["tasks_count"] > 0
    assert target["findings_count"] > 0
    assert target["evidence_count"] > 0


def test_get_session_endpoint():
    """Verify GET /api/investigation/{sessionId} returns actual state matching frontend contracts."""
    _ensure_sample_session()
    sid = SAMPLE_SESSION_ID
    assert sid in sessions

    res = client.get(f"/api/investigation/{sid}")
    assert res.status_code == 200
    data = res.json()
    assert data["session_id"] == sid
    assert "state" in data

    state = data["state"]
    assert "problem" in state
    assert "findings" in state
    assert "evidence" in state
    assert "hypotheses" in state
    assert "status_label" in state
    assert "is_conclusive" in state
    assert state["is_conclusive"] is False
    assert state["status_label"] == "Unverified hypothesis"

    # Hypotheses schema inspection
    assert len(state["hypotheses"]) > 0
    hyp = state["hypotheses"][0]
    assert "hypothesis_id" in hyp
    assert "hypothesis_title" in hyp
    assert "possible_cause" in hyp
    assert "reasoning" in hyp
    assert "supporting_evidence" in hyp
    assert isinstance(hyp["supporting_evidence"], list)
    assert len(hyp["supporting_evidence"]) > 0

    supp = hyp["supporting_evidence"][0]
    assert "evidence_id" in supp
    assert "source" in supp
    assert "line" in supp
    assert "snippet" in supp


def test_start_investigation_and_context_workflow():
    """Verify frontend request to start investigation and provide developer context."""
    problem_text = "The email in the login token does not match the email stored for the user."
    response = client.post(
        "/api/investigation/start",
        data={
            "problem": problem_text,
            "project_path": "default",
            "auto_run": "false",
        },
    )
    assert response.status_code == 200
    start_data = response.json()
    assert "session_id" in start_data
    session_id = start_data["session_id"]
    assert "state" in start_data

    # Context addition endpoint (as used by frontend LeftPanel)
    context_res = client.post(
        f"/api/investigation/{session_id}/context",
        json={"additional_context": "Discrepancy observed between login token payload and database users record."},
    )
    assert context_res.status_code == 200
    context_data = context_res.json()
    assert context_data["status"] == "CONTEXT_SUFFICIENT"
    assert context_data["state"]["context_status"] == "sufficient"


def test_reviewer_endpoint_on_persisted_session():
    """Verify POST /api/investigation/{sessionId}/review evaluates shared state."""
    _ensure_sample_session()
    sid = SAMPLE_SESSION_ID
    assert sid in sessions

    res = client.post(f"/api/investigation/{sid}/review")
    assert res.status_code == 200
    data = res.json()
    assert "review_result" in data
    assert data["review_result"] is not None
    assert "decision" in data["review_result"]
    assert "is_sufficient" in data["review_result"]
    assert "route" in data
    assert "is_conclusive" in data


def test_list_investigations_phase8_fields():
    """Verify GET /api/investigations returns iteration and review_decision."""
    _ensure_sample_session()
    res = client.get("/api/investigations")
    assert res.status_code == 200
    data = res.json()
    assert len(data["investigations"]) > 0
    target = next((item for item in data["investigations"] if item["session_id"] == SAMPLE_SESSION_ID), data["investigations"][0])
    assert "iteration" in target
    assert "review_decision" in target

