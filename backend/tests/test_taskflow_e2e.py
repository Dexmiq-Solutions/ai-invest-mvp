"""End-to-end test with real TaskFlow.zip reproducing the exact user workflow."""

import os
from pathlib import Path
from fastapi.testclient import TestClient
from app.api import app


def test_taskflow_real_zip_e2e_investigation():
    client = TestClient(app)
    zip_path = Path(os.environ["USERPROFILE"]) / "Downloads" / "TaskFlow.zip"
    if not zip_path.exists():
        pytest.skip(f"TaskFlow.zip not found at {zip_path}")

    problem = (
        "The email in the login token does not match the email stored for "
        "the user. Investigate the authentication flow, token generation, "
        "stored user email, and any mismatch between them. Identify relevant "
        "files and report findings based on actual code."
    )

    # 1. Start investigation with real ZIP
    with open(zip_path, "rb") as f:
        resp_start = client.post(
            "/api/investigation/start",
            data={"problem": problem},
            files={"project_zip": ("TaskFlow.zip", f, "application/zip")},
        )
    assert resp_start.status_code == 200
    start_data = resp_start.json()
    session_id = start_data["session_id"]
    assert start_data["status"] in ("CONTEXT_INSUFFICIENT", "CONTEXT_SUFFICIENT")

    # 2. Add developer context
    additional_context = (
        "The reported issue is that the email in the login token does not match "
        "the email stored for the user. The exact runtime symptom, error message, "
        "and HTTP status code have not yet been provided. Inspect the actual TaskFlow "
        "source code to trace where the user email is stored, how the login token is "
        "generated, and how the token email is validated or consumed. Identify the "
        "relevant files and possible mismatch based on code evidence; do not assume "
        "the root cause."
    )
    resp_ctx = client.post(
        f"/api/investigation/{session_id}/context",
        json={"additional_context": additional_context},
    )
    assert resp_ctx.status_code == 200
    ctx_data = resp_ctx.json()
    assert ctx_data["status"] == "CONTEXT_SUFFICIENT"

    # 3. Call repository analysis
    resp_analysis = client.post(f"/api/investigation/{session_id}/repository-analysis")
    assert resp_analysis.status_code == 200
    analysis_data = resp_analysis.json()
    assert analysis_data["status"] == "REPOSITORY_ANALYZED"
    assert analysis_data["state"]["current_step"] == "repository_analyzed"
    summary = analysis_data["state"]["repository_summary"]
    assert len(summary.get("languages", [])) > 0
    assert summary.get("project_structure", {}).get("file_count", 0) > 0
    assert Path(analysis_data["state"]["workspace_path"]).exists()

    # 4. Verify session retrieval
    resp_get = client.get(f"/api/investigation/{session_id}")
    assert resp_get.status_code == 200
    get_data = resp_get.json()
    assert get_data["status"] == "repository_analyzed"
    assert get_data["state"]["workspace_path"] == analysis_data["state"]["workspace_path"]
    assert get_data["state"]["repository_summary"] == summary
