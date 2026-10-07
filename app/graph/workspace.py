from app.graph.state import InvestigationState
from app.workspace.manager import create_workspace


def setup_workspace(state: InvestigationState) -> InvestigationState:
    project_path = state.get("project_path")
    repository_url = state.get("repository_url")

    try:
        workspace_id, workspace_path, source_type = create_workspace(
            project_path=project_path,
            repository_url=repository_url,
        )

        return {
            **state,
            "workspace_id": workspace_id,
            "workspace_path": workspace_path,
            "workspace_source_type": source_type,
            "current_step": "workspace_ready",
        }

    except FileNotFoundError as error:
        return {
            **state,
            "validation_errors": [
                *state.get("validation_errors", []),
                f"Workspace setup failed: {error}",
            ],
            "current_step": "workspace_setup_failed",
        }