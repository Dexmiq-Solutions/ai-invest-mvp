from pathlib import Path
import subprocess
import zipfile

import pytest

from app.graph.graph import build_graph


# ============================================================
# Input Validation
# ============================================================


def test_valid_investigation_creates_workspace(tmp_path):
    """Valid problem + directory should create an isolated workspace."""

    project = tmp_path / "sample_project"
    project.mkdir()

    (project / "main.py").write_text(
        "print('hello')"
    )

    graph = build_graph()

    result = graph.invoke(
        {
            "problem": "My profile page returns 401 after login.",
            "project_path": str(project),
            "problem_context": {
                "expected_behavior": "Profile page should open after login.",
                "actual_behavior": "The profile page returns HTTP 401.",
            },
        }
    )

    assert result["context_status"] == "sufficient"
    assert result["validation_errors"] == []
    assert result["current_step"] == "workspace_ready"

    assert result["workspace_id"]
    assert result["workspace_path"]
    assert result["workspace_source_type"] == "directory"

    workspace = Path(result["workspace_path"])

    assert workspace.exists()
    assert (workspace / "sample_project" / "main.py").exists()


def test_missing_problem_fails_validation(tmp_path):
    """Missing problem description should stop the workflow."""

    project = tmp_path / "sample_project"
    project.mkdir()

    graph = build_graph()

    result = graph.invoke(
        {
            "project_path": str(project),
        }
    )

    assert result["current_step"] == "validation_failed"

    assert "Problem description is required." in (
        result["validation_errors"]
    )

    assert "workspace_id" not in result
    assert "workspace_path" not in result


def test_missing_project_source_fails_validation():
    """Missing project path and repository URL should stop validation."""

    graph = build_graph()

    result = graph.invoke(
        {
            "problem": "My profile page returns 401 after login.",
        }
    )

    assert result["current_step"] == "validation_failed"

    assert "A project path or repository URL is required." in (
        result["validation_errors"]
    )

    assert "workspace_id" not in result
    assert "workspace_path" not in result


def test_both_project_path_and_repository_url_fail_validation(tmp_path):
    """Both project path and Git URL should not be accepted together."""

    project = tmp_path / "sample_project"
    project.mkdir()

    graph = build_graph()

    result = graph.invoke(
        {
            "problem": "My profile page returns 401 after login.",
            "project_path": str(project),
            "repository_url": "https://github.com/example/project.git",
        }
    )

    assert result["current_step"] == "validation_failed"

    assert (
        "Provide either a project path or repository URL, not both."
        in result["validation_errors"]
    )

    assert "workspace_id" not in result
    assert "workspace_path" not in result


# ============================================================
# Context Checking
# ============================================================


def test_insufficient_context_does_not_create_workspace(tmp_path):
    """A vague problem should stop before workspace creation."""

    project = tmp_path / "sample_project"
    project.mkdir()

    graph = build_graph()

    result = graph.invoke(
        {
            "problem": "My application is not working.",
            "project_path": str(project),
        }
    )

    assert result["context_status"] == "insufficient"
    assert result["current_step"] == "context_insufficient"

    assert result["missing_information"] == [
        "specific_problem"
    ]

    assert result["context_questions"]

    assert "workspace_id" not in result
    assert "workspace_path" not in result


@pytest.mark.parametrize(
    "problem",
    [
        "My application is not working.",
        "My app is not working.",
        "Something is wrong.",
        "There is an issue.",
        "There is a problem.",
        "It doesn't work.",
        "It doesnt work.",
    ],
)
def test_vague_problems_are_rejected(tmp_path, problem):
    """Common vague problem descriptions should be rejected."""

    project = tmp_path / "sample_project"
    project.mkdir()

    graph = build_graph()

    result = graph.invoke(
        {
            "problem": problem,
            "project_path": str(project),
        }
    )

    assert result["context_status"] == "insufficient"
    assert result["current_step"] == "context_insufficient"
    assert result["missing_information"] == [
        "specific_problem"
    ]


def test_specific_problem_is_accepted(tmp_path):
    """A specific engineering problem should pass context checking."""

    project = tmp_path / "sample_project"
    project.mkdir()

    graph = build_graph()

    result = graph.invoke(
        {
            "problem": "My profile page returns 401 after login.",
            "project_path": str(project),
        }
    )

    assert result["context_status"] == "sufficient"
    assert result["missing_information"] == []
    assert result["context_questions"] == []


# ============================================================
# Directory Workspace
# ============================================================


def test_directory_project_is_copied_to_workspace(tmp_path):
    """A local project directory should be copied into an isolated workspace."""

    project = tmp_path / "sample_project"
    project.mkdir()

    (project / "main.py").write_text(
        "print('hello')"
    )

    (project / "config.py").write_text(
        "DEBUG = True"
    )

    graph = build_graph()

    result = graph.invoke(
        {
            "problem": "The application returns an unexpected response.",
            "project_path": str(project),
        }
    )

    assert result["workspace_source_type"] == "directory"

    workspace = Path(result["workspace_path"])

    assert workspace.exists()

    copied_project = workspace / "sample_project"

    assert copied_project.exists()
    assert (copied_project / "main.py").exists()
    assert (copied_project / "config.py").exists()


def test_original_directory_is_not_modified(tmp_path):
    """Workspace creation should not modify the original project."""

    project = tmp_path / "sample_project"
    project.mkdir()

    original_file = project / "main.py"

    original_content = "print('original')"

    original_file.write_text(original_content)

    graph = build_graph()

    result = graph.invoke(
        {
            "problem": "The application returns an unexpected response.",
            "project_path": str(project),
        }
    )

    workspace = Path(result["workspace_path"])

    copied_file = workspace / "sample_project" / "main.py"

    assert copied_file.exists()
    assert copied_file.read_text() == original_content

    assert original_file.read_text() == original_content


# ============================================================
# ZIP Workspace
# ============================================================


def test_zip_project_creates_workspace(tmp_path):
    """A ZIP project should be extracted into an isolated workspace."""

    project = tmp_path / "sample_project"
    project.mkdir()

    (project / "main.py").write_text(
        "print('hello from zip')"
    )

    zip_path = tmp_path / "sample_project.zip"

    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.write(
            project / "main.py",
            arcname="sample_project/main.py",
        )

    graph = build_graph()

    result = graph.invoke(
        {
            "problem": "The application returns an unexpected response.",
            "project_path": str(zip_path),
        }
    )

    assert result["context_status"] == "sufficient"
    assert result["workspace_source_type"] == "zip"
    assert result["current_step"] == "workspace_ready"

    workspace = Path(result["workspace_path"])

    assert workspace.exists()
    assert (
        workspace / "sample_project" / "main.py"
    ).exists()


def test_unsafe_zip_path_traversal_is_rejected(tmp_path):
    """ZIP files attempting path traversal should be rejected."""

    zip_path = tmp_path / "malicious.zip"

    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr(
            "../../malicious.txt",
            "malicious content",
        )

    graph = build_graph()

    with pytest.raises(
        ValueError,
        match="Unsafe ZIP file",
    ):
        graph.invoke(
            {
                "problem": "The application returns an unexpected response.",
                "project_path": str(zip_path),
            }
        )


# ============================================================
# Git Repository
# ============================================================


def test_git_repository_creates_workspace(tmp_path):
    """A Git repository should be cloned into an isolated workspace."""

    source_repo = tmp_path / "source_repo"
    source_repo.mkdir()

    (source_repo / "main.py").write_text(
        "print('hello from git')"
    )

    subprocess.run(
        ["git", "init"],
        cwd=source_repo,
        check=True,
        capture_output=True,
    )

    subprocess.run(
        [
            "git",
            "config",
            "user.email",
            "test@example.com",
        ],
        cwd=source_repo,
        check=True,
        capture_output=True,
    )

    subprocess.run(
        [
            "git",
            "config",
            "user.name",
            "Test User",
        ],
        cwd=source_repo,
        check=True,
        capture_output=True,
    )

    subprocess.run(
        ["git", "add", "."],
        cwd=source_repo,
        check=True,
        capture_output=True,
    )

    subprocess.run(
        [
            "git",
            "commit",
            "-m",
            "Initial commit",
        ],
        cwd=source_repo,
        check=True,
        capture_output=True,
    )

    graph = build_graph()

    result = graph.invoke(
        {
            "problem": "My profile page returns 401 after login.",
            "repository_url": str(source_repo),
            "problem_context": {
                "expected_behavior": "Profile page should open.",
                "actual_behavior": "Profile page returns HTTP 401.",
            },
        }
    )

    assert result["context_status"] == "sufficient"
    assert result["current_step"] == "workspace_ready"
    assert result["workspace_source_type"] == "git"

    workspace = Path(result["workspace_path"])

    assert workspace.exists()
    assert (workspace / "main.py").exists()


def test_invalid_git_repository_is_rejected():
    """An invalid Git repository should fail cleanly."""

    graph = build_graph()

    with pytest.raises(
        RuntimeError,
        match="Failed to clone Git repository",
    ):
        graph.invoke(
            {
                "problem": "My profile page returns 401 after login.",
                "repository_url": (
                    "https://github.com/this-repository"
                    "-should-not-exist-123456789.git"
                ),
            }
        )


# ============================================================
# Workspace Isolation
# ============================================================


def test_different_investigations_get_different_workspaces(tmp_path):
    """Each investigation should receive its own workspace."""

    project = tmp_path / "sample_project"
    project.mkdir()

    (project / "main.py").write_text(
        "print('hello')"
    )

    graph = build_graph()

    input_data = {
        "problem": "The application returns an unexpected response.",
        "project_path": str(project),
    }

    result_one = graph.invoke(input_data)
    result_two = graph.invoke(input_data)

    assert result_one["workspace_id"] != result_two["workspace_id"]

    assert (
        result_one["workspace_path"]
        != result_two["workspace_path"]
    )

    assert Path(result_one["workspace_path"]).exists()
    assert Path(result_two["workspace_path"]).exists()