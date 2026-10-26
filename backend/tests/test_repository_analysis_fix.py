"""Tests for the repository analysis pipeline fix.

Validates:
1. Missing workspace_path in repository_analysis_node records a clear validation error.
2. Valid workspace reaches analyze_repository and returns repository_summary.
3. API repository-analysis endpoint auto-prepares workspace when context is sufficient.
4. API repository-analysis endpoint respects context insufficiency.
5. Workspace setup failure is distinguishable from repository analysis failure.
6. Repeated requests do not corrupt session state.
7. ZIP project extraction and analysis works end-to-end.
"""

import zipfile
from pathlib import Path
from fastapi.testclient import TestClient

from app.api import app, sessions
from app.graph.graph import repository_analysis_node


def test_repository_analysis_node_missing_workspace_path_records_error():
    """Verify that calling repository_analysis_node without workspace_path populates validation_errors."""
    state = {
        "problem": "Login token mismatch",
        "validation_errors": [],
    }
    result = repository_analysis_node(state)
    assert result["current_step"] == "repository_analysis_failed"
    assert result["repository_summary"] == {}
    assert len(result["validation_errors"]) > 0
    assert any("workspace_path is missing" in err for err in result["validation_errors"])


def test_repository_analysis_node_valid_workspace(tmp_path):
    """Verify repository_analysis_node executes correctly on a valid workspace directory."""
    project = tmp_path / "project"
    project.mkdir()
    (project / "main.py").write_text("print('hello')", encoding="utf-8")
    (project / "index.js").write_text("console.log('js')", encoding="utf-8")
    (project / "package.json").write_text('{"name": "demo"}', encoding="utf-8")

    state = {
        "problem": "Sample issue",
        "workspace_path": str(project),
        "validation_errors": [],
    }
    result = repository_analysis_node(state)
    assert result["current_step"] == "repository_analyzed"
    assert result["repository_summary"]["languages"] == ["JavaScript", "Python"]
    assert result["repository_summary"]["project_structure"]["file_count"] == 3


def test_api_repository_analysis_aborts_on_insufficient_context():
    """Verify repository analysis endpoint returns CONTEXT_INSUFFICIENT if context is not sufficient."""
    client = TestClient(app)
    # Start investigation with vague problem
    resp = client.post(
        "/api/investigation/start",
        data={"problem": "it doesn't work", "project_path": "default"},
    )
    assert resp.status_code == 200
    session_id = resp.json()["session_id"]
    assert resp.json()["status"] == "CONTEXT_INSUFFICIENT"

    # Attempt repository-analysis directly
    resp_analysis = client.post(f"/api/investigation/{session_id}/repository-analysis")
    assert resp_analysis.status_code == 200
    data = resp_analysis.json()
    assert data["status"] == "CONTEXT_INSUFFICIENT"
    assert any("context is insufficient" in err for err in data["state"]["validation_errors"])


def test_api_repository_analysis_auto_prepares_workspace_and_analyzes(tmp_path):
    """Verify repository analysis endpoint prepares workspace when context is sufficient and analyzes it."""
    client = TestClient(app)

    # Create a small valid ZIP
    src = tmp_path / "app_src"
    src.mkdir()
    (src / "app.js").write_text("console.log('test');", encoding="utf-8")
    (src / "package.json").write_text('{"name": "test-app"}', encoding="utf-8")

    zip_file = tmp_path / "test_app.zip"
    with zipfile.ZipFile(zip_file, "w") as z:
        z.write(src / "app.js", "app.js")
        z.write(src / "package.json", "package.json")

    # Start session with sufficient problem
    resp = client.post(
        "/api/investigation/start",
        data={
            "problem": "Authentication failure: login returns 401 unauthorized with invalid token",
            "project_path": str(zip_file),
        },
    )
    assert resp.status_code == 200
    session_id = resp.json()["session_id"]
    assert resp.json()["status"] == "CONTEXT_SUFFICIENT"

    # Ensure workspace has not been created yet
    assert not resp.json()["state"].get("workspace_path")

    # Call repository-analysis directly (skipping /workspace)
    resp_analysis = client.post(f"/api/investigation/{session_id}/repository-analysis")
    assert resp_analysis.status_code == 200
    data = resp_analysis.json()
    assert data["status"] == "REPOSITORY_ANALYZED"
    assert data["state"]["current_step"] == "repository_analyzed"
    assert data["state"]["workspace_path"] is not None
    assert Path(data["state"]["workspace_path"]).exists()
    assert data["state"]["repository_summary"]["languages"] == ["JavaScript"]
    assert data["state"]["repository_summary"]["project_structure"]["file_count"] == 2

    # Verify session retrieval matches
    resp_get = client.get(f"/api/investigation/{session_id}")
    assert resp_get.status_code == 200
    assert resp_get.json()["status"] == "repository_analyzed"
    assert resp_get.json()["state"]["workspace_path"] == data["state"]["workspace_path"]


def test_api_repository_analysis_fails_with_workspace_failed_on_invalid_source(tmp_path):
    """Verify repository analysis endpoint returns WORKSPACE_FAILED when project source is invalid."""
    client = TestClient(app)

    # Start session with non-existent project_path
    resp = client.post(
        "/api/investigation/start",
        data={
            "problem": "Authentication failure: login returns 401 unauthorized with invalid token",
            "project_path": str(tmp_path / "non_existent_folder"),
        },
    )
    assert resp.status_code == 200
    session_id = resp.json()["session_id"]
    assert resp.json()["status"] == "CONTEXT_SUFFICIENT"

    # Call repository-analysis
    resp_analysis = client.post(f"/api/investigation/{session_id}/repository-analysis")
    assert resp_analysis.status_code == 200
    data = resp_analysis.json()
    # Must report WORKSPACE_FAILED, NOT REPOSITORY_ANALYSIS_FAILED
    assert data["status"] == "WORKSPACE_FAILED"
    assert data["state"]["current_step"] == "workspace_setup_failed"
    assert len(data["state"]["validation_errors"]) > 0


def test_api_repeated_repository_analysis_is_idempotent(tmp_path):
    """Verify calling repository-analysis repeatedly does not duplicate workspace or corrupt state."""
    client = TestClient(app)

    src = tmp_path / "app_src2"
    src.mkdir()
    (src / "index.py").write_text("print('hello')", encoding="utf-8")

    zip_file = tmp_path / "test_app2.zip"
    with zipfile.ZipFile(zip_file, "w") as z:
        z.write(src / "index.py", "index.py")

    resp = client.post(
        "/api/investigation/start",
        data={
            "problem": "Runtime error: unexpected 500 status code on GET /users endpoint",
            "project_path": str(zip_file),
        },
    )
    session_id = resp.json()["session_id"]
    assert resp.json()["status"] == "CONTEXT_SUFFICIENT"

    # First call
    resp1 = client.post(f"/api/investigation/{session_id}/repository-analysis")
    assert resp1.json()["status"] == "REPOSITORY_ANALYZED"
    w_path_1 = resp1.json()["state"]["workspace_path"]

    # Second call
    resp2 = client.post(f"/api/investigation/{session_id}/repository-analysis")
    assert resp2.json()["status"] == "REPOSITORY_ANALYZED"
    w_path_2 = resp2.json()["state"]["workspace_path"]

    # Workspace path should be reused and unchanged
    assert w_path_1 == w_path_2
