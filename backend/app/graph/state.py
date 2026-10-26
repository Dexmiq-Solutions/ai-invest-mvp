

from typing import Any, TypedDict


class InvestigationState(TypedDict, total=False):
    """
    Shared state for the AI Software Issue Investigator.

    The state is passed between LangGraph nodes and contains
    input data, workspace information, gathered context,
    investigation results, and execution status.
    """

    # ========================================================
    # 1. User Input
    # ========================================================

    problem: str
    problem_description: str
    issue: str
    investigation_task: str

    project_path: str
    project_source: str
    repository_url: str

    repo_url: str

    # Optional engineering context
    problem_context: dict[str, Any]
    expected_behavior: str
    actual_behavior: str
    reproduction_steps: list[str]
    known_affected_components: list[str]
    recent_changes: str

    # Errors and diagnostic information
    error: str
    errors: list[str]
    logs: str
    runtime_logs: str
    stack_trace: str
    stack_traces: list[str]
    api_behavior: dict[str, Any]
    api_response: dict[str, Any]

    # ========================================================
    # 2. Validation and Context Check
    # ========================================================

    validation_errors: list[str]
    context_status: str
    validated_problem_context: dict[str, Any]
    missing_context_request: str
    missing_information: list[str]
    context_questions: list[str]
    context_analysis: dict[str, Any]

    # ========================================================
    # 3. Workspace Setup
    # ========================================================

    workspace_path: str
    workspace_id: str
    workspace: str
    workspace_source_type: str
    project_metadata: dict[str, Any]

    # ========================================================
    # 4. Repository Analysis
    # ========================================================

    repository_summary: dict[str, Any]

    # ========================================================
    # 5. Context Gathering
    # ========================================================

    gathered_context: dict[str, Any]

    # ========================================================
    # 6. Investigation Planning
    # ========================================================

    llm_factory: Any
    plan: dict[str, Any]
    plan_id: str
    plan_strategy_validation: dict[str, Any]
    lifecycle_status: str
    investigation_plan: list[dict[str, Any]]
    plan_validation: dict[str, Any]
    selected_investigators: list[str]
    attempted_investigators: list[str]
    completed_investigators: list[str]
    failed_investigators: list[str]
    completed_investigation_tasks: list[dict[str, Any]]
    skipped_investigation_tasks: list[dict[str, Any]]
    failed_investigation_tasks: list[dict[str, Any]]
    completed_tasks: list[dict[str, Any]]
    failed_tasks: list[dict[str, Any]]
    skipped_tasks: list[dict[str, Any]]
    task_results: dict[str, Any]
    evidence_pool: list[dict[str, Any]]
    aggregated_findings: list[dict[str, Any]]
    aggregated_hypotheses: list[dict[str, Any]]
    execution_history: list[dict[str, Any]]
    workflow_status: str
    orchestration_status: str
    stop_reason: str

    # ========================================================
    # 7. Investigation Results
    # ========================================================

    findings: list[dict[str, Any]]
    evidence: list[dict[str, Any]]
    evidence_chain: list[dict[str, Any]]
    hypotheses: list[dict[str, Any]]
    unanswered_questions: list[str]
    missing_evidence: list[str]
    evidence_gaps: list[str]
    established_by_evidence: list[str]
    unanswered_objectives: list[str]
    investigation_history: list[dict[str, Any]]
    replan_history: list[dict[str, Any]]
    unresolved_tasks: list[dict[str, Any]]

    # ========================================================
    # 8. Orchestration Status
    # ========================================================

    execution_status: str
    investigation_status: str
    investigation_confidence: str
    confidence: str
    status_label: str
    is_conclusive: bool
    investigation_stop_reason: str
    investigation_iterations: int
    max_iterations: int

    # ========================================================
    # 9. Review and Diagnosis
    # ========================================================

    review_result: dict[str, Any]
    review_feedback: str
    actionable_feedback: dict[str, Any]
    diagnosis: dict[str, Any]
    likely_root_cause: str
    affected_components: list[str]
    suggested_fix: str
    suggested_fix_details: dict[str, Any]
    verification_steps: list[str]
    verification_summary: dict[str, Any]
    verification_status: str
    remaining_uncertainty: str
    final_report: str
    investigation_report: str
    report_sections: dict[str, Any]
    run_diagnosis: bool

    # ========================================================
    # 10. Context and Workflow Tracking
    # ========================================================

    original_problem: str
    developer_context: list[str]
    context_history: list[dict[str, Any]]
    current_step: str

    # ========================================================
    # 11. Phase 10 Guardrails, Telemetry and Observability
    # ========================================================

    tool_executions: list[dict[str, Any]]
    guardrail_warnings: list[str]
    guardrail_status: str
    is_partial_diagnosis: bool

