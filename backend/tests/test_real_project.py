from pathlib import Path

from app.graph.graph import build_graph


# ============================================================
# REAL PROJECT
# ============================================================

# Look for existing project archive (DevTender or TaskFlow), or create temporary archive
DEV_TENDER_ZIP = Path(
    r"C:\Users\Dellj'\OneDrive\STUDY\DevTender.zip"
)
TASKFLOW_ZIP = Path(
    r"C:\Users\Dellj'\Downloads\TaskFlow.zip"
)


def _get_project_zip(tmp_path: Path) -> Path:
    if DEV_TENDER_ZIP.exists():
        return DEV_TENDER_ZIP
    if TASKFLOW_ZIP.exists():
        return TASKFLOW_ZIP
    # Fallback: create sample project archive
    sample_dir = tmp_path / "sample_real_project"
    sample_dir.mkdir(parents=True, exist_ok=True)
    (sample_dir / "src" / "api").mkdir(parents=True, exist_ok=True)
    (sample_dir / "src" / "main.py").write_text("from fastapi import FastAPI\napp = FastAPI()\n", encoding="utf-8")
    (sample_dir / "requirements.txt").write_text("fastapi\nuvicorn\n", encoding="utf-8")
    zip_path = tmp_path / "sample_project.zip"
    import zipfile
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as arc:
        for f in sample_dir.rglob("*"):
            if f.is_file():
                arc.write(f, f.relative_to(tmp_path))
    return zip_path


# ============================================================
# END-TO-END REAL PROJECT TEST
# ============================================================

def test_real_project_full_investigation_start_to_repository_analysis(tmp_path):
    """
    End-to-end test of the current MVP workflow.

    Simulates a real developer providing:
        1. A project ZIP
        2. A natural-language engineering problem

    Then verifies the complete workflow:

        Developer Input
            ↓
        Input Validation
            ↓
        Context Check
            ↓
        Workspace Setup
            ↓
        Repository Analysis
            ↓
        Final Result
    """

    # ========================================================
    # STEP 1 — DEVELOPER PROVIDES PROJECT
    # ========================================================

    project_zip = _get_project_zip(tmp_path)
    assert project_zip.exists(), f"Project archive not found: {project_zip}"

    # ========================================================
    # STEP 2 — DEVELOPER PROVIDES PROBLEM
    # ========================================================

    problem = (
        "The application is not working correctly when "
        "users try to log in."
    )

    # ========================================================
    # STEP 3 — BUILD APPLICATION GRAPH
    # ========================================================

    graph = build_graph()

    # ========================================================
    # STEP 4 — START INVESTIGATION
    # ========================================================

    result = graph.invoke(
        {
            "problem": problem,
            "project_path": str(project_zip),
        }
    )

    # ========================================================
    # PRINT COMPLETE RESULT
    # ========================================================

    print("\n")
    print("=" * 70)
    print("REAL PROJECT END-TO-END INVESTIGATION")
    print("=" * 70)

    print("\n--- DEVELOPER INPUT ---")
    print("Problem:")
    print(result.get("problem"))

    print("\nProject:")
    print(result.get("project_path"))

    print("\n--- VALIDATION ---")
    print("Validation errors:")
    print(result.get("validation_errors"))

    print("\n--- CONTEXT CHECK ---")
    print("Context status:")
    print(result.get("context_status"))

    print("Context analysis:")
    print(result.get("context_analysis"))

    print("Missing information:")
    print(result.get("missing_information"))

    print("Context questions:")
    print(result.get("context_questions"))

    print("\n--- WORKSPACE ---")
    print("Workspace ID:")
    print(result.get("workspace_id"))

    print("Workspace path:")
    print(result.get("workspace_path"))

    print("Source type:")
    print(result.get("workspace_source_type"))

    print("\n--- REPOSITORY ANALYSIS ---")
    print("Repository summary:")
    print(result.get("repository_summary"))

    print("\n--- FINAL STATE ---")
    print("Current step:")
    print(result.get("current_step"))

    print("=" * 70)

    # ========================================================
    # STEP 5 — INPUT VALIDATION
    # ========================================================

    assert result["validation_errors"] == []

    # ========================================================
    # STEP 6 — CONTEXT CHECK
    # ========================================================

    assert result["context_status"] == "sufficient"

    context_analysis = result.get("context_analysis")

    assert context_analysis
    assert context_analysis["decision"] == "sufficient"

    assert context_analysis["problem_summary"]
    assert context_analysis["target_areas"]

    # ========================================================
    # STEP 7 — WORKSPACE SETUP
    # ========================================================

    assert result["workspace_id"]
    assert result["workspace_path"]

    assert Path(
        result["workspace_path"]
    ).exists()

    assert result["workspace_source_type"] == "zip"

    # ========================================================
    # STEP 8 — REPOSITORY ANALYSIS
    # ========================================================

    assert result["repository_summary"]

    summary = result["repository_summary"]

    assert "project_structure" in summary
    assert "languages" in summary
    assert "frameworks" in summary
    assert "important_directories" in summary
    assert "configuration_files" in summary
    assert "entry_points" in summary
    assert "test_directories" in summary
    assert "git_info" in summary

    # ========================================================
    # STEP 9 — VERIFY REAL ANALYSIS CONTENT
    # ========================================================

    assert summary["project_structure"]

    assert summary["languages"]

    assert summary["frameworks"]

    # ========================================================
    # STEP 10 — VERIFY FINAL WORKFLOW STATE
    # ========================================================

    assert result["current_step"] in (
        "repository_analyzed",
        "context_gathered",
        "investigation_completed",
    )