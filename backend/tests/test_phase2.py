from pathlib import Path
import subprocess
import zipfile

import pytest

from app.graph.graph import build_graph
from app.graph.context import ContextAssessment


VAGUE_PROBLEMS = {
    "my application is not working.",
    "my app is not working.",
    "something is wrong.",
    "there is an issue.",
    "there is a problem.",
    "it doesn't work.",
    "it doesnt work.",
}


class FakeContextLLM:
    """Deterministic replacement for the LLM used by Context Check."""

    def invoke(self, messages):
        # Inspect only the human message. The system prompt itself lists vague
        # examples and must not make every test look vague.
        human_message = messages[-1] if messages else ""
        prompt = str(human_message).lower()

        if any(problem in prompt for problem in VAGUE_PROBLEMS):
            return ContextAssessment(
                decision="insufficient",
                missing_information=["specific_problem"],
                clarifying_question=(
                    "What part of the application is failing, and what happens?"
                ),
            )

        return ContextAssessment(
            decision="sufficient",
            problem_summary="The application has a specific reported issue.",
            target_areas=["application"],
            observable_behavior="The reported behavior differs from expectations.",
            expected_behavior="The application should behave as intended.",
            missing_information=[],
        )


@pytest.fixture
def deterministic_context_check(monkeypatch):
    """Keep tests independent of OpenRouter availability and model variation."""
    monkeypatch.setattr(
        "app.graph.context._get_context_llm",
        lambda: FakeContextLLM(),
    )


def invoke_graph(graph, input_data):
    """Invoke the graph and return its final state."""
    return graph.invoke(input_data)


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------

def test_missing_problem_fails_validation():
    graph = build_graph()

    result = invoke_graph(
        graph,
        {"project_path": "some-project"},
    )

    assert result["current_step"] == "validation_failed"
    assert "Problem description is required." in result["validation_errors"]
    assert "workspace_id" not in result
    assert "workspace_path" not in result


def test_missing_project_source_fails_validation():
    graph = build_graph()

    result = invoke_graph(
        graph,
        {"problem": "My profile page returns 401 after login."},
    )

    assert result["current_step"] == "validation_failed"
    assert (
        "A project path or repository URL is required."
        in result["validation_errors"]
    )
    assert "workspace_id" not in result
    assert "workspace_path" not in result


def test_both_project_path_and_repository_url_fail_validation(tmp_path):
    project = tmp_path / "sample_project"
    project.mkdir()

    graph = build_graph()
    result = invoke_graph(
        graph,
        {
            "problem": "My profile page returns 401 after login.",
            "project_path": str(project),
            "repository_url": "https://github.com/example/project.git",
        },
    )

    assert result["current_step"] == "validation_failed"
    assert (
        "Provide either a project path or repository URL, not both."
        in result["validation_errors"]
    )
    assert "workspace_id" not in result
    assert "workspace_path" not in result


# ---------------------------------------------------------------------------
# Context checking
# ---------------------------------------------------------------------------

def test_insufficient_context_does_not_create_workspace(
    tmp_path, deterministic_context_check
):
    project = tmp_path / "sample_project"
    project.mkdir()

    result = invoke_graph(
        build_graph(),
        {
            "problem": "My application is not working.",
            "project_path": str(project),
        },
    )

    assert result["context_status"] == "insufficient"
    assert result["current_step"] in {"context_insufficient", "context_check"}
    assert result["missing_information"] == ["specific_problem"]
    assert result["context_questions"]
    assert "workspace_id" not in result
    assert "workspace_path" not in result


@pytest.mark.parametrize("problem", sorted(VAGUE_PROBLEMS))
def test_vague_problems_are_rejected(
    tmp_path, problem, deterministic_context_check
):
    project = tmp_path / "sample_project"
    project.mkdir()

    result = invoke_graph(
        build_graph(),
        {
            "problem": problem,
            "project_path": str(project),
        },
    )

    assert result["context_status"] == "insufficient"
    assert result["missing_information"] == ["specific_problem"]
    assert result["context_questions"]
    assert "workspace_id" not in result


def test_specific_problem_is_accepted(
    tmp_path, deterministic_context_check
):
    project = tmp_path / "sample_project"
    project.mkdir()

    result = invoke_graph(
        build_graph(),
        {
            "problem": "My profile page returns 401 after login.",
            "project_path": str(project),
        },
    )

    assert result["context_status"] == "sufficient"
    assert result["missing_information"] == []
    assert result["context_questions"] == []


# ---------------------------------------------------------------------------
# Directory workspace
# ---------------------------------------------------------------------------

def test_directory_project_is_copied_to_workspace(
    tmp_path, deterministic_context_check
):
    project = tmp_path / "sample_project"
    project.mkdir()
    (project / "main.py").write_text("print('hello')", encoding="utf-8")
    (project / "config.py").write_text("DEBUG = True", encoding="utf-8")

    result = invoke_graph(
        build_graph(),
        {
            "problem": "The application returns an unexpected response.",
            "project_path": str(project),
        },
    )

    assert result["workspace_source_type"] == "directory"
    assert result.get("repository_summary")
    workspace = Path(result["workspace_path"])
    copied_project = workspace / "sample_project"

    assert workspace.exists()
    assert (copied_project / "main.py").exists()
    assert (copied_project / "config.py").exists()


def test_original_directory_is_not_modified(
    tmp_path, deterministic_context_check
):
    project = tmp_path / "sample_project"
    project.mkdir()
    original_file = project / "main.py"
    original_content = "print('original')"
    original_file.write_text(original_content, encoding="utf-8")

    result = invoke_graph(
        build_graph(),
        {
            "problem": "The application returns an unexpected response.",
            "project_path": str(project),
        },
    )

    copied_file = Path(result["workspace_path"]) / "sample_project" / "main.py"

    assert copied_file.exists()
    assert copied_file.read_text(encoding="utf-8") == original_content
    assert original_file.read_text(encoding="utf-8") == original_content


# ---------------------------------------------------------------------------
# ZIP workspace and ZIP safety
# ---------------------------------------------------------------------------

def test_zip_project_creates_workspace(tmp_path, deterministic_context_check):
    project = tmp_path / "sample_project"
    project.mkdir()
    (project / "main.py").write_text(
        "print('hello from zip')", encoding="utf-8"
    )

    zip_path = tmp_path / "sample_project.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.write(
            project / "main.py",
            arcname="sample_project/main.py",
        )

    result = invoke_graph(
        build_graph(),
        {
            "problem": "The application returns an unexpected response.",
            "project_path": str(zip_path),
        },
    )

    assert result["context_status"] == "sufficient"
    assert result["workspace_source_type"] == "zip"
    assert result.get("repository_summary")
    assert (
        Path(result["workspace_path"]) / "sample_project" / "main.py"
    ).exists()


def test_unsafe_zip_path_traversal_is_rejected(
    tmp_path, deterministic_context_check
):
    zip_path = tmp_path / "malicious.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr("../../malicious.txt", "malicious content")

    with pytest.raises(ValueError, match="Unsafe ZIP file"):
        invoke_graph(
            build_graph(),
            {
                "problem": "The application returns an unexpected response.",
                "project_path": str(zip_path),
            },
        )


# ---------------------------------------------------------------------------
# Git repository
# ---------------------------------------------------------------------------

def test_git_repository_creates_workspace(
    tmp_path, deterministic_context_check
):
    source_repo = tmp_path / "source_repo"
    source_repo.mkdir()
    (source_repo / "main.py").write_text(
        "print('hello from git')", encoding="utf-8"
    )

    subprocess.run(
        ["git", "init"],
        cwd=source_repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=source_repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
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
        ["git", "commit", "-m", "Initial commit"],
        cwd=source_repo,
        check=True,
        capture_output=True,
    )

    result = invoke_graph(
        build_graph(),
        {
            "problem": "My profile page returns 401 after login.",
            "repository_url": str(source_repo),
            "problem_context": {
                "expected_behavior": "Profile page should open.",
                "actual_behavior": "Profile page returns HTTP 401.",
            },
        },
    )

    assert result["context_status"] == "sufficient"
    assert result["workspace_source_type"] == "git"
    assert result.get("repository_summary")
    workspace = Path(result["workspace_path"])

    assert workspace.exists()
    assert (workspace / "main.py").exists()


# ---------------------------------------------------------------------------
# Workspace isolation
# ---------------------------------------------------------------------------

def test_different_investigations_get_different_workspaces(
    tmp_path, deterministic_context_check
):
    project = tmp_path / "sample_project"
    project.mkdir()
    (project / "main.py").write_text("print('hello')", encoding="utf-8")

    graph = build_graph()
    input_data = {
        "problem": "The application returns an unexpected response.",
        "project_path": str(project),
    }

    result_one = invoke_graph(graph, input_data)
    result_two = invoke_graph(graph, input_data)

    assert result_one["workspace_id"] != result_two["workspace_id"]
    assert result_one["workspace_path"] != result_two["workspace_path"]
    assert Path(result_one["workspace_path"]).exists()
    assert Path(result_two["workspace_path"]).exists()
