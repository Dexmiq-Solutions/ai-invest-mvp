"""
Focused tests for verifying whether Replan uses an LLM and consumes its output.

IMPORTANT:
This is a test template because the actual Replan source code/API was not included
with the request. Connect `call_replan_under_test()` to your project's real Replan
function/node before running. Do not implement a fake Replan function here.

Run:
    pytest -q test_replan_llm_usage.py

These tests intentionally mock the LLM; they do not require a real API key or use
API credits.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# PROJECT ADAPTER — EDIT ONLY THIS SECTION TO MATCH YOUR CODEBASE
# ---------------------------------------------------------------------------

def call_replan_under_test(
    state: dict[str, Any],
    *,
    llm_client: Any,
) -> dict[str, Any]:
    """
    Connects the test adapter to the real replanner_node implementation,
    injecting the provided mock LLM client and adapting state keys.
    """
    from app.graph.graph import replanner_node

    # If with_structured_output was mocked as a generic MagicMock, delegate back to llm_client
    if hasattr(llm_client, "with_structured_output"):
        attr = getattr(llm_client, "with_structured_output")
        if isinstance(attr, MagicMock):
            attr.return_value = llm_client

    problem = state.get("problem") or state.get("problem_description", "Investigate issue")
    review_res = dict(state.get("review_result") or state.get("reviewer_feedback") or {})
    if "evidence_gaps" in review_res and "missing_evidence" not in review_res:
        review_res["missing_evidence"] = list(review_res["evidence_gaps"])

    investigation_tasks = state.get("investigation_plan") or state.get("investigation_tasks") or []
    completed = list(state.get("completed_tasks") or [
        t for t in investigation_tasks
        if isinstance(t, dict) and t.get("status") in ("completed", "done")
    ])

    adapted_state = {
        **state,
        "problem": problem,
        "original_problem": problem,
        "review_result": review_res,
        "investigation_plan": list(investigation_tasks),
        "completed_tasks": completed,
        "investigation_iterations": state.get("investigation_iterations", 1),
        "investigation_history": list(state.get("investigation_history") or []),
        "llm_factory": lambda: llm_client,
    }

    return replanner_node(adapted_state)


def make_state(
    *,
    problem: str = "Some API requests return HTTP 401 unexpectedly.",
    findings: list[dict[str, Any]] | None = None,
    previous_tasks: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Minimal generic state; add required fields from your actual state schema."""
    return {
        "problem_description": problem,
        "findings": findings or [
            {
                "id": "finding-1",
                "summary": "The request may reach authentication middleware.",
                "evidence": "Static code observation; runtime cause is not verified.",
            }
        ],
        "investigation_tasks": previous_tasks or [],
        "reviewer_feedback": {
            "decision": "NEEDS_MORE_EVIDENCE",
            "evidence_gaps": [
                "Trace how the authentication token is created, stored, and sent."
            ],
        },
    }


def get_plan(result: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Adapt this helper if your graph stores tasks under another key.
    It deliberately does not assume a specific production schema.
    """
    for key in ("follow_up_tasks", "replan_tasks", "planned_tasks", "tasks"):
        value = result.get(key)
        if isinstance(value, list):
            return value
    pytest.fail(
        "Could not find a task list in Replan output. Update get_plan() to use "
        "the actual output field from your implementation."
    )


def make_mock_llm_response(task_text: str) -> dict[str, Any]:
    """Return a controlled response; adapt shape to the model wrapper in use."""
    return {
        "tasks": [
            {
                "title": task_text,
                "objective": task_text,
                "reason": "Addresses an explicitly unresolved evidence gap.",
                "expected_evidence": "A trace showing the token lifecycle.",
            }
        ]
    }


# ---------------------------------------------------------------------------
# TEST CASES
# ---------------------------------------------------------------------------

def test_replan_calls_llm(llm_client: MagicMock) -> None:
    """TC01: A Replan execution must call the configured LLM in an LLM-driven path."""
    llm_client.invoke.return_value = make_mock_llm_response(
        "TRACE_TOKEN_LIFECYCLE_UNIQUE_731"
    )

    call_replan_under_test(make_state(), llm_client=llm_client)

    assert llm_client.invoke.called or llm_client.ainvoke.called, (
        "Replan did not call the mocked LLM. Check whether it uses heuristics, "
        "a different client method, or a different dependency injection path."
    )


@pytest.fixture
def llm_client() -> MagicMock:
    """
    Generic mock supporting common LangChain-style `invoke` and `ainvoke` calls.
    If the application uses a different method (e.g. `complete`), adapt the mock
    and assertions to the real client interface.
    """
    client = MagicMock()
    client.invoke.return_value = make_mock_llm_response(
        "TRACE_TOKEN_LIFECYCLE_UNIQUE_731"
    )
    client.ainvoke.return_value = make_mock_llm_response(
        "TRACE_TOKEN_LIFECYCLE_UNIQUE_731"
    )
    return client


def test_replan_uses_llm_response_in_generated_plan(llm_client: MagicMock) -> None:
    """TC02: The distinctive task returned by the mock must influence the plan."""
    unique_task = "TRACE_TOKEN_LIFECYCLE_UNIQUE_731"
    llm_client.invoke.return_value = make_mock_llm_response(unique_task)
    llm_client.ainvoke.return_value = make_mock_llm_response(unique_task)

    result = call_replan_under_test(make_state(), llm_client=llm_client)
    plan = get_plan(result)

    serialized_plan = repr(plan)
    assert unique_task in serialized_plan, (
        "The LLM was configured to return a unique task, but that task was not "
        "found in Replan's output. Its response may be ignored, transformed "
        "incorrectly, or overridden by heuristic logic."
    )


def test_replan_plan_changes_when_llm_response_changes(
    llm_client: MagicMock,
) -> None:
    """TC03: Distinct controlled LLM outputs should be reflected in distinct plans."""
    first_task = "INVESTIGATE_TOKEN_STORAGE_UNIQUE_A"
    second_task = "INVESTIGATE_AUTH_MIDDLEWARE_UNIQUE_B"

    llm_client.invoke.return_value = make_mock_llm_response(first_task)
    llm_client.ainvoke.return_value = make_mock_llm_response(first_task)
    first_result = call_replan_under_test(make_state(), llm_client=llm_client)
    first_plan = repr(get_plan(first_result))

    llm_client.invoke.return_value = make_mock_llm_response(second_task)
    llm_client.ainvoke.return_value = make_mock_llm_response(second_task)
    second_result = call_replan_under_test(make_state(), llm_client=llm_client)
    second_plan = repr(get_plan(second_result))

    assert first_task in first_plan, "First mocked LLM output was not reflected."
    assert second_task in second_plan, "Second mocked LLM output was not reflected."
    assert first_plan != second_plan, (
        "Changing the LLM response did not change the resulting plan. "
        "Check whether deterministic rules override or ignore model output."
    )


def test_replan_uses_current_reviewer_evidence_gap(llm_client: MagicMock) -> None:
    """TC04: Replan should receive/use the unresolved gap from current state."""
    state = make_state()
    state["reviewer_feedback"]["evidence_gaps"] = [
        "CHECK_DATABASE_TRANSACTION_ROLLBACK_UNIQUE_902"
    ]
    llm_client.invoke.return_value = make_mock_llm_response(
        "CHECK_DATABASE_TRANSACTION_ROLLBACK_UNIQUE_902"
    )
    llm_client.ainvoke.return_value = make_mock_llm_response(
        "CHECK_DATABASE_TRANSACTION_ROLLBACK_UNIQUE_902"
    )

    result = call_replan_under_test(state, llm_client=llm_client)
    plan = repr(get_plan(result))

    assert (
        "CHECK_DATABASE_TRANSACTION_ROLLBACK_UNIQUE_902" in plan
        or "CHECK_DATABASE_TRANSACTION_ROLLBACK_UNIQUE_902"
        in repr(llm_client.invoke.call_args)
        or "CHECK_DATABASE_TRANSACTION_ROLLBACK_UNIQUE_902"
        in repr(llm_client.ainvoke.call_args)
    ), "The current evidence gap was neither reflected in the plan nor passed to the LLM."


def test_replan_does_not_add_duplicate_completed_task(llm_client: MagicMock) -> None:
    """TC05: Existing duplicate-prevention rules should remain effective."""
    existing_task = {
        "id": "task-existing",
        "title": "Trace token lifecycle",
        "objective": "Trace how token is created, stored, and sent",
        "status": "completed",
    }
    state = make_state(previous_tasks=[existing_task])
    llm_client.invoke.return_value = {
        "tasks": [
            {
                "title": "Trace token lifecycle",
                "objective": "Trace how token is created, stored, and sent",
                "reason": "Same evidence gap.",
                "expected_evidence": "Token lifecycle trace.",
            }
        ]
    }
    llm_client.ainvoke.return_value = llm_client.invoke.return_value

    result = call_replan_under_test(state, llm_client=llm_client)
    plan = get_plan(result)

    normalized = [
        (
            str(task.get("title", "")).strip().lower(),
            str(task.get("objective", "")).strip().lower(),
        )
        for task in plan
        if isinstance(task, dict)
    ]
    duplicates = [
        pair for pair in normalized
        if pair[0] == "trace token lifecycle"
        or pair[1] == "trace how token is created, stored, and sent"
    ]
    assert len(duplicates) <= 1, (
        "Replan returned repeated work for an already completed evidence gap."
    )


def test_replan_handles_llm_failure_without_claiming_success(
    llm_client: MagicMock,
) -> None:
    """TC06: LLM failures should be surfaced or handled by the documented fallback."""
    llm_client.invoke.side_effect = RuntimeError("mock LLM unavailable")
    llm_client.ainvoke.side_effect = RuntimeError("mock LLM unavailable")

    try:
        result = call_replan_under_test(make_state(), llm_client=llm_client)
    except RuntimeError:
        # Valid if the project's documented behavior is to propagate the failure.
        return

    # If Replan handles failures internally, it must not silently claim a valid
    # model-generated plan. Adapt these checks to the project's error contract.
    assert isinstance(result, dict), "Failure handling returned an invalid result."
    status = str(result.get("status", "")).lower()
    error = result.get("error") or result.get("errors")
    assert error or status in {"error", "failed", "fallback", "degraded"}, (
        "The LLM failed, but Replan returned no observable error/fallback status."
    )


def test_replan_rejects_invalid_llm_response(llm_client: MagicMock) -> None:
    """TC07: Invalid model output must not become an unchecked investigation plan."""
    llm_client.invoke.return_value = {"unexpected": object()}
    llm_client.ainvoke.return_value = {"unexpected": object()}

    try:
        result = call_replan_under_test(make_state(), llm_client=llm_client)
    except (ValueError, TypeError, KeyError):
        # Valid if the existing contract rejects malformed model output explicitly.
        return

    assert isinstance(result, dict), "Invalid LLM output produced a non-dict result."
    status = str(result.get("status", "")).lower()
    error = result.get("error") or result.get("errors")
    assert error or status in {"error", "failed", "fallback", "degraded"}, (
        "Malformed LLM output was not rejected or reported."
    )
