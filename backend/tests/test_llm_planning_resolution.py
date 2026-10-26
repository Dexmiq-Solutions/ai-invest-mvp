"""Regression tests for LLM planning response parsing and agent resolution.

Verifies:
- A valid investigator with an unrecognized assigned_agent (e.g. 'code_investigator' + 'code_specialist')
- A valid registered assigned_agent (with missing or unrecognized investigator)
- A response with neither field resolving to a registered investigator
- End-to-end task parsing in InvestigationPlanner._plan_with_llm preserving valid tasks
"""

from unittest.mock import MagicMock
import pytest

from app.agents.investigation_orchestrator import (
    AVAILABLE_INVESTIGATORS,
    InvestigationPlanner,
    InvestigationStepSchema,
    InvestigationPlanSchema,
)


def test_resolve_investigator_valid_investigator_unrecognized_assigned_agent():
    """Scenario 1: Valid investigator with an unrecognized assigned_agent must resolve to the registered investigator."""
    step = {
        "step_id": "step-1",
        "investigator": "code_investigator",
        "assigned_agent": "code_specialist",
    }
    resolved = InvestigationPlanner._resolve_investigator(step)
    assert resolved == "code_investigator"
    assert resolved in AVAILABLE_INVESTIGATORS

    # Another example: dependency_impact_investigator with an arbitrary agent name
    step2 = {
        "step_id": "step-2",
        "investigator": "dependency_impact_investigator",
        "assigned_agent": "integration_prober",
    }
    resolved2 = InvestigationPlanner._resolve_investigator(step2)
    assert resolved2 == "dependency_impact_investigator"
    assert resolved2 in AVAILABLE_INVESTIGATORS


def test_resolve_investigator_valid_registered_assigned_agent():
    """Scenario 2: Valid registered assigned_agent must resolve to the registered investigator when investigator is missing or unrecognized."""
    # investigator missing/None
    step1 = {
        "step_id": "step-1",
        "investigator": None,
        "assigned_agent": "code_investigator",
    }
    resolved1 = InvestigationPlanner._resolve_investigator(step1)
    assert resolved1 == "code_investigator"

    # investigator unrecognized, assigned_agent valid registered investigator
    step2 = {
        "step_id": "step-2",
        "investigator": "unregistered_crash_finder",
        "assigned_agent": "runtime_error_investigator",
    }
    resolved2 = InvestigationPlanner._resolve_investigator(step2)
    assert resolved2 == "runtime_error_investigator"


def test_resolve_investigator_neither_field_resolves():
    """Scenario 3: Response with neither field resolving to a registered investigator must return None."""
    step = {
        "step_id": "step-1",
        "investigator": "sql_database_checker",
        "assigned_agent": "Bob",
    }
    resolved = InvestigationPlanner._resolve_investigator(step)
    assert resolved is None

    # Empty dictionary or missing fields
    assert InvestigationPlanner._resolve_investigator({}) is None
    assert InvestigationPlanner._resolve_investigator({"investigator": "", "assigned_agent": ""}) is None


def test_plan_with_llm_preserves_valid_tasks_with_alternate_assigned_agent():
    """Verify that _plan_with_llm extracts tasks when LLM provides 'investigator: code_investigator' and 'assigned_agent: code_specialist'."""
    planner = InvestigationPlanner(llm_factory=lambda: None)

    # Mock LLM returning structured InvestigationPlanSchema with code_specialist as assigned_agent
    mock_llm = MagicMock()
    mock_structured_llm = MagicMock()
    mock_llm.with_structured_output.return_value = mock_structured_llm

    mock_plan = InvestigationPlanSchema(
        investigation_goal="Investigate task status discrepancy",
        problem_assessment="In progress tasks do not appear in UI tab",
        selected_investigators=["code_investigator"],
        steps=[
            InvestigationStepSchema(
                step_id="step-1",
                task_id="TASK-CODE-001",
                investigator="code_investigator",
                assigned_agent="code_specialist",  # The exact defect observed in GPT-4o-mini output
                task="Inspect API response handling in api.js",
                objective="Verify task filtering and display logic",
                reason="Determine if status filter matches data",
                instructions="Inspect tasks array filtering logic",
                expected_output="Filtering code snippet",
                expected_evidence="Code statements comparing status",
                completion_criteria="Identified filtering predicate",
                scope=["taskflow/frontend/src/services/api.js"],
                priority="high",
                status="pending",
            )
        ],
        rationale="Focus on code investigator for frontend logic",
    )
    mock_structured_llm.invoke.return_value = mock_plan
    planner.llm_factory = lambda: mock_llm

    candidate_tasks = planner._plan_with_llm(
        problem="Tasks seeded as 'In progress' never appear under tab",
        repository_summary={"languages": ["JavaScript"]},
        gathered_context={"relevant_files": [{"path": "taskflow/frontend/src/services/api.js"}]},
        current_state={},
        reviewer_feedback=None,
        unanswered_questions=None,
        iteration=1,
        attempted=set(),
    )

    # Verify tasks were NOT discarded to empty list
    assert candidate_tasks is not None
    assert len(candidate_tasks) == 1
    task = candidate_tasks[0]
    assert task["assigned_agent"] == "code_investigator"
    assert task["investigator"] == "code_investigator"
    assert task["status"] == "pending"
    assert "scope" in task
    assert "objective" in task
