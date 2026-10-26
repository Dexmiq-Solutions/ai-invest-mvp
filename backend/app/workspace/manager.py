import logging
from pathlib import Path
from uuid import uuid4
import shutil
import subprocess
import zipfile

logger = logging.getLogger("app.workspace.manager")

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
        logger.error("Workspace creation failed: Neither project_path nor repository_url provided")
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
        logger.info("[%s] Preparing workspace via Git clone from: %s", workspace_id, repository_url)
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
            logger.info("[%s] Git clone successful into: %s", workspace_id, repository_path)
        except FileNotFoundError:
            shutil.rmtree(workspace_path, ignore_errors=True)
            logger.error("[%s] Git clone failed: Git executable not found in PATH", workspace_id)
            raise RuntimeError(
                "Git is not installed or is not available in PATH."
            )

        except subprocess.CalledProcessError as error:
            shutil.rmtree(workspace_path, ignore_errors=True)
            err_msg = error.stderr.strip() if error.stderr else str(error)
            logger.error("[%s] Git clone failed with error: %s", workspace_id, err_msg)
            raise RuntimeError(
                f"Failed to clone Git repository: {err_msg}"
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
        logger.error("[%s] Project source does not exist: %s", workspace_id, project_path)
        raise FileNotFoundError(
            f"Project source does not exist: {project_path}"
        )

    if source.is_dir():
        logger.info("[%s] Preparing workspace via directory copy: %s -> %s", workspace_id, source, workspace_path)
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
        logger.info("[%s] Directory copy complete: %s", workspace_id, destination)
        source_type = "directory"

    elif source.suffix.lower() == ".zip":
        logger.info("[%s] Preparing workspace via ZIP archive extraction: %s -> %s", workspace_id, source, workspace_path)
        workspace_path.mkdir(
            parents=True,
            exist_ok=False,
        )

        _extract_zip_safely(
            source,
            workspace_path,
        )
        logger.info("[%s] ZIP archive extraction complete: %s", workspace_id, workspace_path)
        source_type = "zip"

    else:
        logger.error("[%s] Unsupported project source format: %s", workspace_id, source.suffix)
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
        members = archive.infolist()
        for member in members:

            target = (destination / member.filename).resolve()

            if not str(target).startswith(str(destination)):
                logger.error(
                    "Security violation: path traversal detected in ZIP archive %s for entry %s (resolves to %s outside %s)",
                    zip_path,
                    member.filename,
                    target,
                    destination,
                )
                raise ValueError(
                    "Unsafe ZIP file: path traversal detected."
                )

        archive.extractall(destination)
        logger.debug("Safely extracted %d members from ZIP %s into %s", len(members), zip_path, destination)