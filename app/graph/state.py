from typing import TypedDict


class InvestigationState(TypedDict, total=False):
    problem: str
    project_context: dict
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