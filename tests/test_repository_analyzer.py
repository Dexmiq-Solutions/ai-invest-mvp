from pathlib import Path

from app.repository.analyzer import analyze_repository
from app.graph.graph import build_graph


def create_sample_repository(root: Path):
    (root / "src" / "api").mkdir(parents=True)
    (root / "tests").mkdir()

    (root / "src" / "main.py").write_text(
        "from fastapi import FastAPI\n\napp = FastAPI()\n",
        encoding="utf-8",
    )

    (root / "src" / "api" / "routes.py").write_text(
        "from fastapi import APIRouter\n",
        encoding="utf-8",
    )

    (root / "requirements.txt").write_text(
        "fastapi\nuvicorn\n",
        encoding="utf-8",
    )

    (root / "pytest.ini").write_text(
        "[pytest]\n",
        encoding="utf-8",
    )


def test_repository_analysis(tmp_path):
    project = tmp_path / "sample_project"
    project.mkdir()

    create_sample_repository(project)

    summary = analyze_repository(project)

    assert "Python" in summary["languages"]
    assert "FastAPI" in summary["frameworks"]

    assert "src" in summary["important_directories"]
    assert "src/api" in summary["important_directories"]
    assert "tests" in summary["test_directories"]

    assert "requirements.txt" in summary["configuration_files"]
    assert "pytest.ini" in summary["configuration_files"]

    assert "src/main.py" in summary["entry_points"]


def test_repository_analysis_missing_path(tmp_path):
    missing_path = tmp_path / "does-not-exist"

    try:
        analyze_repository(missing_path)
        assert False, "Expected FileNotFoundError"
    except FileNotFoundError:
        pass


def test_phase_3_missing_workspace():
    graph = build_graph()

    result = graph.invoke({
        "problem": "My application returns HTTP 401 after login.",
        "project_path": "does-not-exist",
    })

    assert result["validation_errors"]
    assert any(
        "Workspace setup failed" in error
        for error in result["validation_errors"]
    )

    assert result["current_step"] == "repository_analysis_failed"