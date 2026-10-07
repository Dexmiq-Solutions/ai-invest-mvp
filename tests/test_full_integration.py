from pathlib import Path
import os
import zipfile

import pytest

from app.graph.graph import build_graph


# ============================================================
# REAL PROJECT CONFIGURATION
# ============================================================

DEV_TENDER_ZIP = Path(
    r"C:\Users\Dellj'\OneDrive\STUDY\DevTender.zip"
)

# Optional:
# Set this only if you have an actual Git URL for DevTender.
#
# PowerShell:
# $env:DEV_TENDER_GIT_URL="https://github.com/your-org/DevTender.git"
#
# The Git tests will be skipped if this is not provided.
DEV_TENDER_GIT_URL = os.getenv("DEV_TENDER_GIT_URL")


VALID_PROBLEM = (
    "The profile API returns a 401 response after the user "
    "successfully logs in."
)


# ============================================================
# HELPER
# ============================================================

def run_graph(
    project_path=None,
    repository_url=None,
    problem=None,
):
    graph = build_graph()

    state = {}

    if project_path is not None:
        state["project_path"] = str(project_path)

    if repository_url is not None:
        state["repository_url"] = repository_url

    if problem is not None:
        state["problem"] = problem

    return graph.invoke(state)


# ============================================================
# 1. REAL PROJECT - ZIP HAPPY PATH
# ============================================================

def test_01_real_dev_tender_zip_happy_path():
    """
    Complete happy path using the real DevTender project:

    ZIP
      -> validation
      -> context check
      -> workspace
      -> repository analysis
      -> final state
    """

    assert DEV_TENDER_ZIP.exists(), (
        f"DevTender ZIP not found: {DEV_TENDER_ZIP}"
    )

    result = run_graph(
        project_path=DEV_TENDER_ZIP,
        problem=VALID_PROBLEM,
    )

    # Context
    assert result["context_status"] == "sufficient"

    # No validation errors
    assert result.get("validation_errors", []) == []

    # Workspace
    assert result.get("workspace_id")
    assert result.get("workspace_path")
    assert result["workspace_source_type"] == "zip"

    # Repository analysis completed
    assert result["current_step"] == "repository_analyzed"

    # Repository summary exists
    summary = result["repository_summary"]

    assert isinstance(summary, dict)
    assert summary

    # Required repository analysis sections
    assert "project_structure" in summary
    assert "languages" in summary
    assert "frameworks" in summary
    assert "important_directories" in summary
    assert "configuration_files" in summary
    assert "entry_points" in summary
    assert "test_directories" in summary
    assert "git_info" in summary


# ============================================================
# 2. REAL PROJECT - ZIP CONTENT VALIDATION
# ============================================================

def test_02_real_dev_tender_repository_details():
    """
    Verify that the analyzer actually understands
    the real DevTender structure.
    """

    assert DEV_TENDER_ZIP.exists()

    result = run_graph(
        project_path=DEV_TENDER_ZIP,
        problem=VALID_PROBLEM,
    )

    summary = result["repository_summary"]

    # Languages
    assert "JavaScript" in summary["languages"]
    assert "HTML" in summary["languages"]
    assert "CSS" in summary["languages"]

    # Frameworks
    assert "Express" in summary["frameworks"]
    assert "Vite" in summary["frameworks"]

    # Backend
    assert any(
        path.startswith("backend/")
        for path in summary["important_directories"]
    )

    # Frontend
    assert any(
        path.startswith("frontend/")
        for path in summary["important_directories"]
    )

    # Package/configuration files
    configuration_files = summary["configuration_files"]

    assert any(
        path.endswith("backend/package.json")
        for path in configuration_files
    )

    assert any(
        path.endswith("frontend/my-app/package.json")
        for path in configuration_files
    )

    # Git information
    git_info = summary["git_info"]

    assert git_info["is_git_repository"] is True
    assert git_info["branch"]
    assert git_info["commit"]


# ============================================================
# 3. REAL PROJECT - INSUFFICIENT CONTEXT
# ============================================================

@pytest.mark.parametrize(
    "problem",
    [
        "My application is not working.",
        "Something is wrong.",
        "There is an issue.",
        "The application has a problem.",
    ],
)
def test_03_real_project_insufficient_context(problem):
    """
    The system must not guess when the problem
    description is too vague.
    """

    assert DEV_TENDER_ZIP.exists()

    result = run_graph(
        project_path=DEV_TENDER_ZIP,
        problem=problem,
    )

    assert result["context_status"] == "insufficient"
    assert result["current_step"] == "context_insufficient"

    assert result.get("context_questions")


# ============================================================
# 4. REAL PROJECT - SUFFICIENT CONTEXT
# ============================================================

@pytest.mark.parametrize(
    "problem",
    [
        "The profile API returns a 401 response after login.",
        "The login succeeds but the profile request returns 401.",
        "Authenticated requests are returning 401 responses.",
    ],
)
def test_04_real_project_sufficient_context(problem):
    """
    Different realistic problem descriptions should
    allow the investigation to continue.
    """

    assert DEV_TENDER_ZIP.exists()

    result = run_graph(
        project_path=DEV_TENDER_ZIP,
        problem=problem,
    )

    assert result["context_status"] == "sufficient"
    assert result["current_step"] == "repository_analyzed"


# ============================================================
# 5. MISSING PROJECT SOURCE
# ============================================================

def test_05_missing_project_source():
    """
    Neither ZIP/directory nor Git repository is provided.
    """

    result = run_graph(
        problem=VALID_PROBLEM,
    )

    assert result["current_step"] == "validation_failed"
    assert result.get("validation_errors")


# ============================================================
# 6. MISSING PROBLEM
# ============================================================

def test_06_missing_problem():
    """
    Project exists but problem description is missing.
    """

    assert DEV_TENDER_ZIP.exists()

    result = run_graph(
        project_path=DEV_TENDER_ZIP,
    )

    assert result["current_step"] == "validation_failed"
    assert result.get("validation_errors")


# ============================================================
# 7. EMPTY PROBLEM
# ============================================================

def test_07_empty_problem():
    """
    Empty problem description should fail validation.
    """

    assert DEV_TENDER_ZIP.exists()

    result = run_graph(
        project_path=DEV_TENDER_ZIP,
        problem="",
    )

    assert result["current_step"] == "validation_failed"
    assert result.get("validation_errors")


# ============================================================
# 8. NON-EXISTENT PROJECT PATH
# ============================================================

def test_08_nonexistent_project_path():
    """
    A project path that does not exist should be handled
    without crashing the graph.
    """

    result = run_graph(
        project_path=(
            r"C:\this\project\definitely\does\not\exist"
        ),
        problem=VALID_PROBLEM,
    )

    assert result["current_step"] == "workspace_setup_failed"

    assert result.get("validation_errors")


# ============================================================
# 9. UNSUPPORTED FILE TYPE
# ============================================================

def test_09_unsupported_project_type(tmp_path):
    """
    A normal unsupported file should be rejected.
    """

    unsupported_file = tmp_path / "project.txt"
    unsupported_file.write_text(
        "This is not a supported project source."
    )

    with pytest.raises(ValueError):
        run_graph(
            project_path=unsupported_file,
            problem=VALID_PROBLEM,
        )


# ============================================================
# 10. UNSAFE ZIP
# ============================================================

def test_10_unsafe_zip_path_traversal(tmp_path):
    """
    ZIP path traversal must be rejected.
    """

    malicious_zip = tmp_path / "malicious.zip"

    with zipfile.ZipFile(malicious_zip, "w") as archive:
        archive.writestr(
            "../../outside.txt",
            "malicious content",
        )

    with pytest.raises(
        ValueError,
        match="Unsafe ZIP file",
    ):
        run_graph(
            project_path=malicious_zip,
            problem=VALID_PROBLEM,
        )


# ============================================================
# 11. INVALID GIT URL
# ============================================================

def test_11_invalid_git_repository():
    """
    Invalid Git repository should fail safely.
    """

    with pytest.raises(
        RuntimeError,
        match="Failed to clone Git repository",
    ):
        run_graph(
            repository_url=(
                "https://invalid.example.com/"
                "this-repository-does-not-exist.git"
            ),
            problem=VALID_PROBLEM,
        )


# ============================================================
# 12. REAL PROJECT - GIT HAPPY PATH
# ============================================================

@pytest.mark.skipif(
    not DEV_TENDER_GIT_URL,
    reason=(
        "Set DEV_TENDER_GIT_URL to the real DevTender "
        "Git repository URL to run this test."
    ),
)
def test_12_real_dev_tender_git_happy_path():
    """
    Complete happy path using the real DevTender Git repository:

    Git URL
      -> validation
      -> context check
      -> clone
      -> workspace
      -> repository analysis
      -> final state
    """

    result = run_graph(
        repository_url=DEV_TENDER_GIT_URL,
        problem=VALID_PROBLEM,
    )

    # Context
    assert result["context_status"] == "sufficient"

    # Validation
    assert result.get("validation_errors", []) == []

    # Workspace
    assert result.get("workspace_id")
    assert result.get("workspace_path")
    assert result["workspace_source_type"] == "git"

    # Repository analysis
    assert result["current_step"] == "repository_analyzed"

    summary = result["repository_summary"]

    assert isinstance(summary, dict)
    assert summary

    assert "project_structure" in summary
    assert "languages" in summary
    assert "frameworks" in summary
    assert "important_directories" in summary
    assert "configuration_files" in summary
    assert "entry_points" in summary
    assert "test_directories" in summary
    assert "git_info" in summary


# ============================================================
# 13. GIT + INSUFFICIENT CONTEXT
# ============================================================

@pytest.mark.skipif(
    not DEV_TENDER_GIT_URL,
    reason=(
        "Set DEV_TENDER_GIT_URL to the real DevTender "
        "Git repository URL to run this test."
    ),
)
def test_13_real_dev_tender_git_insufficient_context():
    """
    Git input should follow the same context rules as ZIP input.
    """

    result = run_graph(
        repository_url=DEV_TENDER_GIT_URL,
        problem="My application is not working.",
    )

    assert result["context_status"] == "insufficient"
    assert result["current_step"] == "context_insufficient"

    assert result.get("context_questions")


# ============================================================
# 14. GIT + SUFFICIENT CONTEXT
# ============================================================

@pytest.mark.skipif(
    not DEV_TENDER_GIT_URL,
    reason=(
        "Set DEV_TENDER_GIT_URL to the real DevTender "
        "Git repository URL to run this test."
    ),
)
def test_14_real_dev_tender_git_sufficient_context():
    """
    Git input with a specific engineering problem should
    reach repository analysis.
    """

    result = run_graph(
        repository_url=DEV_TENDER_GIT_URL,
        problem=VALID_PROBLEM,
    )

    assert result["context_status"] == "sufficient"
    assert result["current_step"] == "repository_analyzed"


# ============================================================
# 15. FINAL STATE CONSISTENCY - ZIP
# ============================================================

def test_15_final_state_consistency():
    """
    Verify that the final state contains all information
    required by the current MVP workflow.
    """

    assert DEV_TENDER_ZIP.exists()

    result = run_graph(
        project_path=DEV_TENDER_ZIP,
        problem=VALID_PROBLEM,
    )

    # Core state
    assert result.get("problem") == VALID_PROBLEM
    assert result.get("project_path")
    assert result.get("context_status") == "sufficient"

    # Workspace state
    assert result.get("workspace_id")
    assert result.get("workspace_path")
    assert result.get("workspace_source_type") == "zip"

    # Repository state
    assert isinstance(
        result.get("repository_summary"),
        dict,
    )

    # Execution state
    assert result.get("current_step") == "repository_analyzed"

    # Error state
    assert isinstance(
        result.get("validation_errors", []),
        list,
    )


# ============================================================
# 16. WORKSPACE ISOLATION
# ============================================================

def test_16_workspace_is_created_separately():
    """
    The original DevTender ZIP must not be used as the
    investigation workspace.
    """

    assert DEV_TENDER_ZIP.exists()

    result = run_graph(
        project_path=DEV_TENDER_ZIP,
        problem=VALID_PROBLEM,
    )

    workspace_path = Path(
        result["workspace_path"]
    ).resolve()

    original_path = DEV_TENDER_ZIP.resolve()

    assert workspace_path.exists()
    assert workspace_path != original_path


# ============================================================
# 17. WORKSPACE DOES NOT MODIFY ORIGINAL ZIP
# ============================================================

def test_17_original_project_still_exists():
    """
    Running the investigation must not delete or replace
    the original project ZIP.
    """

    assert DEV_TENDER_ZIP.exists()

    original_size = DEV_TENDER_ZIP.stat().st_size

    result = run_graph(
        project_path=DEV_TENDER_ZIP,
        problem=VALID_PROBLEM,
    )

    assert result["current_step"] == "repository_analyzed"

    assert DEV_TENDER_ZIP.exists()

    assert DEV_TENDER_ZIP.stat().st_size == original_size


# ============================================================
# 18. REPEATED REAL PROJECT RUN
# ============================================================

def test_18_real_project_can_run_multiple_times():
    """
    Two independent investigations of the same project
    should create separate workspaces.
    """

    assert DEV_TENDER_ZIP.exists()

    result_1 = run_graph(
        project_path=DEV_TENDER_ZIP,
        problem=VALID_PROBLEM,
    )

    result_2 = run_graph(
        project_path=DEV_TENDER_ZIP,
        problem=VALID_PROBLEM,
    )

    assert result_1["current_step"] == "repository_analyzed"
    assert result_2["current_step"] == "repository_analyzed"

    assert result_1["workspace_id"]
    assert result_2["workspace_id"]

    assert (
        result_1["workspace_id"]
        != result_2["workspace_id"]
    )

    assert (
        result_1["workspace_path"]
        != result_2["workspace_path"]
    )