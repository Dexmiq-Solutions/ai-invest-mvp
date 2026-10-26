import logging
from app.graph.state import InvestigationState
from app.workspace.manager import create_workspace

logger = logging.getLogger("app.graph.workspace")


def setup_workspace(state: InvestigationState) -> InvestigationState:
    project_path = state.get("project_path")
    repository_url = state.get("repository_url")
    logger.info("Setting up workspace: project_path=%s, repository_url=%s", project_path, repository_url)

    try:
        workspace_id, workspace_path, source_type = create_workspace(
            project_path=project_path,
            repository_url=repository_url,
        )
        logger.info(
            "Workspace created successfully: id=%s, path=%s, source_type=%s",
            workspace_id,
            workspace_path,
            source_type,
        )

        return {
            **state,
            "workspace_id": workspace_id,
            "workspace_path": workspace_path,
            "workspace_source_type": source_type,
            "current_step": "workspace_ready",
        }

    except FileNotFoundError as error:
        logger.error("Workspace setup failed: FileNotFoundError: %s", error)
        return {
            **state,
            "validation_errors": [
                *state.get("validation_errors", []),
                f"Workspace setup failed: {error}",
            ],
            "current_step": "workspace_setup_failed",
        }
    except Exception as error:
        logger.error("Workspace setup failed with exception: %s", error)
        raise