from typing import TypedDict


class InvestigationState(TypedDict, total=False):
    # Input
    problem: str
    project_path: str
    problem_context: dict
    repository_url: str

    # Context validation
    context_status: str
    missing_information: list
    context_questions: list
    validation_errors: list

    # Workspace
    workspace_id: str
    workspace_path: str
    workspace_source_type: str

    # Investigation
    investigation_plan: list
    current_step: str
    relevant_files: list
    findings: list
    hypotheses: list
    evidence: list
    agents_used: list
    tools_used: list
    validation_results: list
    iteration_count: int
    review_status: str
    final_diagnosis: dict