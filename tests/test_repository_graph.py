from pathlib import Path
import zipfile

from app.graph.graph import build_graph


def create_sample_project(root: Path):
    project = root / "sample_project"

    (project / "src" / "api").mkdir(parents=True)
    (project / "tests").mkdir(parents=True)

    (project / "src" / "main.py").write_text(
        "from fastapi import FastAPI\n\napp = FastAPI()\n",
        encoding="utf-8",
    )

    (project / "src" / "api" / "routes.py").write_text(
        "from fastapi import APIRouter\n",
        encoding="utf-8",
    )

    (project / "tests" / "test_main.py").write_text(
        "def test_example():\n    assert True\n",
        encoding="utf-8",
    )

    (project / "requirements.txt").write_text(
        "fastapi\nuvicorn\n",
        encoding="utf-8",
    )

    return project

def test_phase_3_repository_analysis(tmp_path):
    project = create_sample_project(tmp_path)

    zip_path = tmp_path / "sample_project.zip"

    with zipfile.ZipFile(zip_path, "w") as archive:
        for file_path in project.rglob("*"):
            if file_path.is_file():
                archive.write(
                    file_path,
                    file_path.relative_to(tmp_path),
                )

    graph = build_graph()

    result = graph.invoke({
        "problem": "My application has an issue.",
        "project_path": str(zip_path),
    })

    assert result["current_step"] == "repository_analyzed"

    summary = result["repository_summary"]

    assert summary
    assert "project_structure" in summary
    assert "languages" in summary
    assert "frameworks" in summary
    assert "important_directories" in summary
    assert "configuration_files" in summary
    assert "entry_points" in summary
    assert "test_directories" in summary
    assert "git_info" in summary

    assert "src" in summary["important_directories"]
    assert "src/api" in summary["important_directories"]
    assert "tests" in summary["test_directories"]
    assert "Python" in summary["languages"]
    assert "FastAPI" in summary["frameworks"]