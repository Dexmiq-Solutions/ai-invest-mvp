"""
Tests for Audit Findings:
1. P2 — Multi-session Persistence across restarts
2. P2 — Standardized API response metadata (workspace_id, workspace_path, current_step)
"""

import json
import uuid
import pytest
from pathlib import Path
from fastapi.testclient import TestClient

from app.api import (
    app,
    sessions,
    _persist_session,
    _ensure_persisted_sessions_loaded,
    _get_sessions_dir,
    _build_investigation_response,
)

client = TestClient(app)


def test_multi_session_persistence_across_restart(tmp_path):
    """Confirm multiple sessions are preserved independently and loaded upon restart."""
    sessions_dir = _get_sessions_dir()

    sid1 = f"test-session-1-{uuid.uuid4().hex[:6]}"
    sid2 = f"test-session-2-{uuid.uuid4().hex[:6]}"
    sid3 = f"test-session-3-{uuid.uuid4().hex[:6]}"

    state1 = {
        "session_id": sid1,
        "problem": "First investigation: memory leak in worker",
        "current_step": "investigation_completed",
        "workspace_id": "ws-001",
        "workspace_path": "/tmp/ws1",
        "status_label": "Verified root cause",
        "is_conclusive": True,
        "confidence": "high",
        "findings": [{"id": "f1", "statement": "Leak identified"}],
        "evidence": [{"source": "worker.js", "line": 42}],
        "investigation_plan": [{"task_id": "T-1"}],
    }

    state2 = {
        "session_id": sid2,
        "problem": "Second investigation: database timeout",
        "current_step": "investigation_completed",
        "workspace_id": "ws-002",
        "workspace_path": "/tmp/ws2",
        "status_label": "Likely root cause",
        "is_conclusive": True,
        "confidence": "medium",
        "findings": [{"id": "f2", "statement": "Pool exhausted"}],
        "evidence": [{"source": "db.js", "line": 10}],
        "investigation_plan": [{"task_id": "T-2"}],
    }

    state3 = {
        "session_id": sid3,
        "problem": "Third investigation: auth token expiry mismatch",
        "current_step": "context_insufficient",
        "workspace_id": None,
        "workspace_path": None,
        "status_label": "Inconclusive",
        "is_conclusive": False,
        "confidence": "low",
        "findings": [],
        "evidence": [],
        "investigation_plan": [],
    }

    # Persist all three sessions
    _persist_session(sid1, state1)
    _persist_session(sid2, state2)
    _persist_session(sid3, state3)

    # Verify individual session files exist
    assert (sessions_dir / f"{sid1}.json").exists()
    assert (sessions_dir / f"{sid2}.json").exists()
    assert (sessions_dir / f"{sid3}.json").exists()

    # Simulate backend restart: clear in-memory sessions dictionary
    sessions.clear()
    assert sid1 not in sessions
    assert sid2 not in sessions
    assert sid3 not in sessions

    # Reload persisted sessions
    _ensure_persisted_sessions_loaded()

    # All three must be restored
    assert sid1 in sessions
    assert sid2 in sessions
    assert sid3 in sessions
    assert sessions[sid1]["problem"] == state1["problem"]
    assert sessions[sid2]["workspace_id"] == "ws-002"
    assert sessions[sid3]["current_step"] == "context_insufficient"

    # Verify listing endpoint includes all three
    resp = client.get("/api/investigations")
    assert resp.status_code == 200
    data = resp.json()
    listed_sids = [inv["session_id"] for inv in data.get("investigations", [])]
    assert sid1 in listed_sids
    assert sid2 in listed_sids
    assert sid3 in listed_sids


def test_standardized_api_response_metadata():
    """Verify that workspace_id, workspace_path, and current_step are exposed at top-level."""
    sid = f"test-meta-{uuid.uuid4().hex[:6]}"
    state = {
        "session_id": sid,
        "workspace_id": "ws-meta-123",
        "workspace_path": "d:/workspaces/ws-meta-123",
        "current_step": "workspace_ready",
        "problem": "Sample issue",
    }
    sessions[sid] = state

    # Test helper
    resp = _build_investigation_response(
        session_id=sid,
        state=state,
        status="WORKSPACE_CREATED",
        extra={"custom_key": "custom_val"},
    )

    assert resp["session_id"] == sid
    assert resp["status"] == "WORKSPACE_CREATED"
    assert resp["current_step"] == "workspace_ready"
    assert resp["workspace_id"] == "ws-meta-123"
    assert resp["workspace_path"] == "d:/workspaces/ws-meta-123"
    assert resp["state"] == state
    assert resp["custom_key"] == "custom_val"


def test_get_session_endpoint_exposes_metadata():
    """Verify /api/investigation/{session_id} returns top-level metadata."""
    sid = f"test-endpoint-{uuid.uuid4().hex[:6]}"
    state = {
        "session_id": sid,
        "workspace_id": "ws-end-789",
        "workspace_path": "/path/to/ws",
        "current_step": "investigation_completed",
    }
    sessions[sid] = state
    _persist_session(sid, state)

    resp = client.get(f"/api/investigation/{sid}")
    assert resp.status_code == 200
    data = resp.json()

    assert data["session_id"] == sid
    assert data["current_step"] == "investigation_completed"
    assert data["workspace_id"] == "ws-end-789"
    assert data["workspace_path"] == "/path/to/ws"
    assert "state" in data
