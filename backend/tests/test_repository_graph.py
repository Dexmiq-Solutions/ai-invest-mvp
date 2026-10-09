from pathlib import Path
import zipfile

from app.graph.graph import build_graph


def create_sample_project(root: Path) -> Path:
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


def create_project_zip(project: Path, zip_path: Path, root: Path) -> None:
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for file_path in project.rglob("*"):
            if file_path.is_file():
                archive.write(
                    file_path,
                    file_path.relative_to(root),
                )


def test_phase_3_repository_analysis(tmp_path: Path):
    project = create_sample_project(tmp_path)
    zip_path = tmp_path / "sample_project.zip"

    create_project_zip(project, zip_path, tmp_path)

    graph = build_graph()

    result = graph.invoke(
        {
            "problem": (
                "The FastAPI application fails to start when I run it."
            ),
            "project_path": str(zip_path),
        }
    )

    # Repository analysis must have completed successfully.
    summary = result.get("repository_summary")

    assert summary, (
        f"Repository analysis did not produce a summary. "
        f"current_step={result.get('current_step')!r}, "
        f"context_status={result.get('context_status')!r}, "
        f"errors={result.get('validation_errors')!r}"
    )

    expected_keys = {
        "project_structure",
        "languages",
        "frameworks",
        "important_directories",
        "configuration_files",
        "entry_points",
        "test_directories",
        "git_info",
    }

    assert expected_keys.issubset(summary.keys())

    assert "src" in summary["important_directories"]
    assert "src/api" in summary["important_directories"]
    assert "tests" in summary["test_directories"]
    assert "Python" in summary["languages"]
    assert "FastAPI" in summary["frameworks"]

