"""LangGraph node that runs the Phase 6 investigation orchestrator.

Flow:
Context Gathering -> Dynamic Investigation Plan -> Selected Specialist(s)
-> Findings/Evidence/Hypotheses -> Review/Re-plan -> Diagnosis payload.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from app.agents.investigation_orchestrator import InvestigationOrchestrator
from app.llm import get_llm_factory
from app.logging_config import sanitize_log_text

logger = logging.getLogger("app.agents.deep_agent")

# The orchestrator plans dynamically. Do not invoke all specialists here.
orchestrator = InvestigationOrchestrator(
    max_iterations=5,
    min_evidence=3,
    min_agents=1,
    llm_factory=get_llm_factory(),
)


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def deep_agent_node(state: dict[str, Any]) -> dict[str, Any]:
    """Run dynamic planning and specialist orchestration for the current issue."""
    start_time = time.time()
    state = dict(state or {})
    problem = (
        state.get("problem")
        or state.get("issue")
        or state.get("investigation_task")
        or state.get("problem_description")
        or ""
    )
    problem = str(problem).strip()

    logger.info(
        "[DeepAgent] Node entered for problem: '%s' (workspace: %s)",
        sanitize_log_text(problem, max_len=100),
        state.get("workspace_path"),
    )

    if not problem:
        logger.warning("[DeepAgent] Aborted: No investigation problem provided")
        return {
            **state,
            "investigation_status": "failed",
            "investigation_stop_reason": "No investigation problem was provided.",
            "status_label": "Investigation incomplete",
            "is_conclusive": False,
            "current_step": "investigation_failed",
        }

    if state.get("context_status") in ("insufficient", "incomplete", "context_check_failed"):
        logger.warning("[DeepAgent] Aborted: Context is incomplete (%s)", state.get("context_status"))
        return {
            **state,
            "investigation_status": "incomplete",
            "investigation_stop_reason": (
                "Investigation cannot start while context is incomplete: no observable symptom or behavior provided."
            ),
            "status_label": "Unverified hypothesis",
            "investigation_confidence": "low",
            "current_step": "investigation_incomplete",
        }

    gathered_context = _as_dict(state.get("gathered_context"))
    repository_summary = _as_dict(state.get("repository_summary"))
    shared_context: dict[str, Any] = {
        **gathered_context,
        "repository_summary": repository_summary,
    }

    # Preserve useful context already collected by upstream graph nodes.
    context_fields = (
        "errors", "error", "logs", "runtime_logs", "stack_trace",
        "stack_traces", "api_behavior", "api_request", "api_response",
        "expected_behavior", "actual_behavior", "reproduction_steps",
        "recent_changes", "known_affected_components", "relevant_files",
        "investigation_concepts", "producer_consumer_pairs",
    )
    for field in context_fields:
        if state.get(field) is not None:
            shared_context[field] = state[field]

    try:
        result = orchestrator.investigate(
            investigation_task=problem,
            workspace_path=state.get("workspace_path"),
            workspace_id=state.get("workspace_id"),
            shared_context=shared_context,
            current_state={**state, "problem": problem},
            # Verification remains opt-in and only works if a verifier is configured.
            run_verification=bool(state.get("run_hypothesis_verification", False)),
        )
        if not isinstance(result, dict):
            raise TypeError("InvestigationOrchestrator.investigate() must return a dict")
    except Exception as exc:  # fail closed; do not lose the original LangGraph state
        logger.exception("Phase 6 orchestration failed")
        return {
            **state,
            "investigation_status": "failed",
            "investigation_stop_reason": f"Orchestrator error: {type(exc).__name__}: {exc}",
            "status_label": "Investigation incomplete",
            "is_conclusive": False,
            "current_step": "investigation_failed",
        }

    diagnosis = _as_dict(result.get("diagnosis"))
    status = str(result.get("orchestration_status") or "failed").lower()
    status_label = (
        result.get("status_label")
        or diagnosis.get("status_label")
        or ("Investigation incomplete" if status == "failed" else "Unverified hypothesis")
    )
    is_conclusive = bool(
        result.get("is_conclusive", diagnosis.get("is_conclusive", False))
    )

    elapsed = time.time() - start_time
    findings_list = _as_list(result.get("aggregated_findings"))
    evidence_list = _as_list(result.get("evidence_pool"))
    logger.info(
        "[DeepAgent] Node completed in %.2fs. Status: %s, Findings: %d, Evidence: %d, Conclusive: %s",
        elapsed,
        status,
        len(findings_list),
        len(evidence_list),
        is_conclusive,
    )

    # Keep the existing graph state and write all Phase 6 outputs into it.
    return {
        **state,
        "investigation_plan": _as_list(result.get("investigation_plan")),
        "findings": findings_list,
        "evidence": evidence_list,
        "evidence_chain": _as_list(result.get("evidence_chain")),
        "primary_evidence": _as_list(result.get("primary_evidence")),
        "secondary_evidence": _as_list(result.get("secondary_evidence")),
        "hypotheses": _as_list(result.get("hypotheses")),
        "investigation_history": _as_list(result.get("execution_history")),
        "selected_investigators": _as_list(result.get("selected_investigators")),
        "attempted_investigators": _as_list(result.get("attempted_investigators")),
        "completed_investigators": _as_list(result.get("completed_investigators")),
        "failed_investigators": _as_list(result.get("failed_investigators")),
        "completed_investigation_tasks": _as_list(result.get("completed_tasks")),
        "skipped_investigation_tasks": _as_list(result.get("skipped_tasks")),
        "failed_investigation_tasks": _as_list(result.get("failed_tasks")),
        "plan_validation": _as_dict(result.get("plan_validation") or state.get("plan_validation")),
        "execution_status": result.get("execution_status", "completed" if status in ("completed", "partial") else "failed"),
        "investigation_status": result.get("investigation_status", status),
        "investigation_confidence": result.get("confidence", "low"),
        "confidence": result.get("confidence", "low"),
        "status_label": status_label,
        "is_conclusive": is_conclusive,
        "investigation_stop_reason": result.get("stop_reason", ""),
        "investigation_iterations": result.get("iterations", 0),
        "review_result": _as_dict(result.get("review_result")),
        "review_feedback": result.get("review_feedback", ""),
        "diagnosis": diagnosis,
        "likely_root_cause": result.get("likely_root_cause") or diagnosis.get("likely_root_cause", ""),
        "affected_components": _as_list(result.get("affected_components") or diagnosis.get("affected_components")),
        "suggested_fix": result.get("suggested_fix") or diagnosis.get("suggested_fix", ""),
        "verification_steps": _as_list(result.get("verification_steps") or diagnosis.get("verification_steps")),
        "remaining_uncertainty": result.get("remaining_uncertainty") or diagnosis.get("remaining_uncertainty", ""),
        "current_step": "investigation_failed" if status == "failed" else "investigation_completed",
    }
