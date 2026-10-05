from pathlib import Path
from uuid import uuid4
import shutil
import subprocess
import zipfile

WORKSPACE_ROOT = Path("workspaces")

def create_workspace(
    project_path: str | None = None,
    repository_url: str | None = None,
) -> tuple[str, str, str]:
    """
    Create an isolated workspace from either:
    - a local project directory
    - a ZIP file
    - a Git repository URL
    """

    if not project_path and not repository_url:
        raise ValueError(
            "Either project_path or repository_url must be provided."
        )

    WORKSPACE_ROOT.mkdir(parents=True, exist_ok=True)

    workspace_id = str(uuid4())
    workspace_path = WORKSPACE_ROOT / workspace_id

    # ---------------------------------------------------------
    # Git repository
    # ---------------------------------------------------------
    if repository_url:
        workspace_path.mkdir(parents=True, exist_ok=False)

        repository_path = workspace_path / "repository"

        try:
            subprocess.run(
                [
                    "git",
                    "clone",
                    repository_url,
                    str(repository_path),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
        except FileNotFoundError:
            shutil.rmtree(workspace_path, ignore_errors=True)

            raise RuntimeError(
                "Git is not installed or is not available in PATH."
            )

        except subprocess.CalledProcessError as error:
            shutil.rmtree(workspace_path, ignore_errors=True)

            raise RuntimeError(
                f"Failed to clone Git repository: {error.stderr.strip()}"
            )

        return (
            workspace_id,
            str(repository_path),
            "git",
        )

    # ---------------------------------------------------------
    # Local directory / ZIP
    # ---------------------------------------------------------
    source = Path(project_path).resolve()

    if not source.exists():
        raise FileNotFoundError(
            f"Project source does not exist: {project_path}"
        )

    if source.is_dir():

        workspace_path.mkdir(
            parents=True,
            exist_ok=False,
        )

        destination = workspace_path / source.name

        shutil.copytree(
            source,
            destination,
            ignore=shutil.ignore_patterns(
                ".git",
                ".venv",
                "__pycache__",
                "node_modules",
                "workspaces",
            ),
        )

        source_type = "directory"

    elif source.suffix.lower() == ".zip":

        workspace_path.mkdir(
            parents=True,
            exist_ok=False,
        )

        _extract_zip_safely(
            source,
            workspace_path,
        )

        source_type = "zip"

    else:
        raise ValueError(
            "Unsupported project source. "
            "Provide a project directory, ZIP file, "
            "or Git repository URL."
        )

    return (
        workspace_id,
        str(workspace_path),
        source_type,
    )

def _extract_zip_safely(
    zip_path: Path,
    destination: Path,
) -> None:
    """
    Safely extract a ZIP file without allowing path traversal.
    """

    destination = destination.resolve()

    with zipfile.ZipFile(zip_path, "r") as archive:
        for member in archive.infolist():

            target = (destination / member.filename).resolve()

            if not str(target).startswith(str(destination)):
                raise ValueError(
                    "Unsafe ZIP file: path traversal detected."
                )

        archive.extractall(destination)