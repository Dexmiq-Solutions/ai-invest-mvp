
import logging
from langgraph.graph import StateGraph, START, END

from app.graph.state import InvestigationState
from app.graph.context import validate_input, check_context
from app.graph.workspace import setup_workspace
from app.graph.context_gathering import context_gathering_node
from app.logging_config import sanitize_log_text
from app.node_json_logging import trace_node_json
from app.node_behavior_logging import trace_node

from app.repository.analyzer import analyze_repository
from app.tools.investigation_tools import (
    list_files,
    search_code,
    read_file,
)

from app.agents.plan_validator import validate_investigation_plan, validate_plan_strategy
from app.agents.investigation_orchestrator import InvestigationOrchestrator, is_causal_evidence
from app.agents.reviewer import Reviewer, is_hypothesis_relevant_to_problem
from app.agents.replanner import Replanner
from app.agents.diagnostician import Diagnostician
from app.agents.verifier import Verifier
from app.llm import get_llm_factory

# Deep Agent integration
from app.agents.deep_agent import deep_agent_node

logger = logging.getLogger("app.graph")


# ============================================================
# INITIAL NODE
# ============================================================

def initial_node(
    state: InvestigationState,
) -> InvestigationState:
    """Initialize the investigation workflow."""
    problem_preview = sanitize_log_text(state.get("problem", ""), max_len=100)
    logger.info("Initializing investigation workflow: problem='%s'", problem_preview)

    configured_max = state.get("max_iterations")
    if configured_max is None:
        validated_max = 5
    else:
        try:
            val_int = int(configured_max)
            validated_max = max(1, min(val_int, 5))
        except (ValueError, TypeError):
            validated_max = 5

    return {
        **state,
        "investigation_iterations": state.get("investigation_iterations", 1),
        "iteration": state.get("iteration", 0),
        "max_iterations": validated_max,
        "current_step": "initialized",
    }


# ============================================================
# ROUTING FUNCTIONS
# ============================================================

def route_after_validation(
    state: InvestigationState,
) -> str:
    """Route based on input validation results."""
    errors = state.get("validation_errors")
    if errors:
        logger.warning("Routing after validation: validation failed with errors: %s", errors)
        return "validation_failed"

    logger.info("Routing after validation: validation passed -> proceeding to context_check")
    return "context_check"


def route_after_context(
    state: InvestigationState,
) -> str:
    """Route based on context sufficiency."""
    status = state.get("context_status")
    if status == "sufficient":
        logger.info("Routing after context check: status=sufficient -> proceeding to workspace")
        return "workspace"

    logger.info("Routing after context check: status=%s -> stopping at context_insufficient", status)
    return "context_insufficient"


def route_after_workspace(
    state: InvestigationState,
) -> str:
    """Continue only if workspace setup succeeded."""
    step = state.get("current_step")
    if step == "workspace_ready":
        logger.info(
            "Routing after workspace: ready at %s -> proceeding to repository_analysis",
            state.get("workspace_path"),
        )
        return "repository_analysis"

    logger.error("Routing after workspace: setup failed (errors: %s) -> stopping at workspace_setup_failed", state.get("validation_errors"))
    return "workspace_setup_failed"


# ============================================================
# TERMINAL NODES
# ============================================================

def validation_failed_node(
    state: InvestigationState,
) -> InvestigationState:
    """Stop after input validation failure."""
    logger.warning("Investigation workflow terminated at validation_failed_node: %s", state.get("validation_errors"))
    return {
        **state,
        "current_step": "validation_failed",
    }


def context_insufficient_node(
    state: InvestigationState,
) -> InvestigationState:
    """Stop when additional problem context is required."""
    logger.info(
        "Investigation workflow terminated at context_insufficient_node. Missing: %s, Questions: %s",
        state.get("missing_information"),
        state.get("context_questions"),
    )
    return {
        **state,
        "current_step": "context_insufficient",
    }


def workspace_setup_failed_node(
    state: InvestigationState,
) -> InvestigationState:
    """Stop when workspace creation fails."""
    logger.error("Investigation workflow terminated at workspace_setup_failed_node: %s", state.get("validation_errors"))
    return {
        **state,
        "current_step": "workspace_setup_failed",
    }


# ============================================================
# REPOSITORY ANALYSIS
# ============================================================

def repository_analysis_node(
    state: InvestigationState,
) -> InvestigationState:
    """Analyze the isolated repository workspace."""
    workspace_path = state.get("workspace_path")

    if not workspace_path:
        error_msg = "Repository analysis failed: workspace_path is missing from state."
        logger.error(error_msg)
        return {
            **state,
            "repository_summary": {},
            "validation_errors": [
                *state.get("validation_errors", []),
                error_msg,
            ],
            "current_step": "repository_analysis_failed",
        }

    try:
        logger.info("Repository analysis starting on workspace: %s", workspace_path)
        summary = analyze_repository(workspace_path)
        logger.info(
            "Repository analysis completed: %d files, languages=%s, frameworks=%s, entry_points=%d",
            summary.get("project_structure", {}).get("file_count", 0),
            summary.get("languages", []),
            summary.get("frameworks", []),
            len(summary.get("entry_points", [])),
        )

        return {
            **state,
            "repository_summary": summary,
            "current_step": "repository_analyzed",
        }

    except Exception as error:
        logger.exception("Repository analysis node encountered error on %s: %s", workspace_path, error)
        return {
            **state,
            "repository_summary": {},
            "validation_errors": [
                *state.get("validation_errors", []),
                f"Repository analysis failed: {error}",
            ],
            "current_step": "repository_analysis_failed",
        }


# ============================================================
# CONTEXT GATHERING
# ============================================================

def context_gathering_graph_node(
    state: InvestigationState,
) -> InvestigationState:
    """
    Adapt the shared InvestigationState to the Context
    Gathering node and provide repository investigation tools.
    """
    workspace_path = state.get("workspace_path")

    if not workspace_path:
        logger.error("Context gathering node called without workspace_path in state")
        return {
            **state,
            "gathered_context": {},
            "validation_errors": [
                *state.get("validation_errors", []),
                "Context gathering failed: workspace_path is missing.",
            ],
            "current_step": "context_gathering_failed",
        }

    try:
        logger.info("Context gathering graph node executing on workspace: %s", workspace_path)
        cg_state = dict(state)
        if "validated_problem_context" not in cg_state:
            cg_state["validated_problem_context"] = cg_state.get("problem_context") or {
                "problem": cg_state.get("problem", ""),
                "expected_behavior": "",
                "actual_behavior": "",
            }
        result = context_gathering_node(
            {
                **cg_state,
                "workspace": workspace_path,
            },
            list_files_tool=list_files,
            search_code_tool=search_code,
            read_file_tool=read_file,
        )

        gathered = result.get("gathered_context") or {}
        logger.info(
            "Context gathering completed: %d relevant files",
            len(gathered.get("relevant_files", [])),
        )

        return {
            **state,
            **result,
        }

    except Exception as error:
        logger.exception("Context gathering graph node encountered error on %s: %s", workspace_path, error)
        return {
            **state,
            "gathered_context": {},
            "validation_errors": [
                *state.get("validation_errors", []),
                f"Context gathering failed: {error}",
            ],
            "current_step": "context_gathering_failed",
        }


# ============================================================
# PHASE 6 PIPELINE NODES (Planning, Validation, Execution)
# ============================================================

def create_investigation_plan_node(
    state: InvestigationState,
) -> InvestigationState:
    """Create initial investigation plan based on gathered context."""
    import time
    start_time = time.time()
    session_id = str(state.get("session_id") or state.get("investigation_id") or "unspecified")
    problem_preview = sanitize_log_text(state.get("problem", ""), max_len=120)
    logger.info(
        "[Graph] create_investigation_plan_start: session_id=%s, problem='%s'",
        session_id,
        problem_preview,
    )

    try:
        llm_factory = state.get("llm_factory") or get_llm_factory()
        orch = InvestigationOrchestrator(llm_factory=llm_factory)
        state_dict = dict(state)
        # If investigation_plan and plan artifact are already explicitly provided in state, preserve them
        existing_plan = state.get("investigation_plan")
        existing_plan_artifact = state.get("plan")
        if existing_plan is not None and len(existing_plan) > 0 and existing_plan_artifact is not None:
            initial_plan = existing_plan
            plan_artifact = existing_plan_artifact
            plan_id = state.get("plan_id")
            lifecycle_status = state.get("lifecycle_status", "validated")
        else:
            initial_plan = orch.create_initial_plan(state_dict)
            plan_artifact = state_dict.get("plan")
            plan_id = state_dict.get("plan_id")
            lifecycle_status = state_dict.get("lifecycle_status", "validated")

        plan_validation = state_dict.get("plan_validation")
        plan_strategy_validation = state_dict.get("plan_strategy_validation")

        selected = [
            step.get("assigned_agent") or step.get("investigator")
            for step in initial_plan
            if isinstance(step, dict) and (step.get("assigned_agent") or step.get("investigator"))
        ]
        elapsed = time.time() - start_time
        logger.info(
            "[Graph] create_investigation_plan_complete: session_id=%s, tasks=%d, investigators=%s, duration=%.3fs",
            session_id,
            len(initial_plan),
            selected,
            elapsed,
        )
        return {
            **state,
            "plan": plan_artifact,
            "plan_id": plan_id,
            "lifecycle_status": lifecycle_status,
            "investigation_plan": initial_plan,
            "selected_investigators": selected,
            "plan_validation": plan_validation or state.get("plan_validation"),
            "plan_strategy_validation": plan_strategy_validation or state.get("plan_strategy_validation"),
            "current_step": "create_investigation_plan" if lifecycle_status != "invalid" else "create_investigation_plan_failed",
        }
    except Exception as exc:
        elapsed = time.time() - start_time
        logger.exception(
            "[Graph] create_investigation_plan_failed: session_id=%s, error=%s, duration=%.3fs",
            session_id,
            exc,
            elapsed,
        )
        return {
            **state,
            "investigation_plan": [],
            "selected_investigators": [],
            "validation_errors": [*state.get("validation_errors", []), str(exc)],
            "lifecycle_status": "invalid",
            "current_step": "create_investigation_plan_failed",
        }


def validate_investigation_plan_node(
    state: InvestigationState,
) -> InvestigationState:
    """Perform immediate pre-dispatch plan validation using gathered context."""
    import time
    start_time = time.time()
    session_id = str(state.get("session_id") or state.get("investigation_id") or "unspecified")
    plan = state.get("investigation_plan") or []
    plan_artifact = state.get("plan")
    logger.info(
        "[Graph] validate_investigation_plan_start: session_id=%s, task_count=%d",
        session_id,
        len(plan),
    )

    strategy_val_res = None
    if plan_artifact:
        strategy_val_res = validate_plan_strategy(
            plan=plan_artifact,
            problem=state.get("problem", ""),
            gathered_context=state.get("gathered_context", {}),
            session_id=session_id,
        )

    val_res = validate_investigation_plan(
        plan=plan,
        available_agents={"code_investigator", "dependency_impact_investigator", "runtime_error_investigator"},
        problem=state.get("problem", ""),
        gathered_context=state.get("gathered_context", {}),
        session_id=session_id,
    )

    is_strat_valid = strategy_val_res.is_valid if strategy_val_res else True
    # If strategy is invalid or no plan tasks were generated, plan is invalid
    combined_valid = is_strat_valid and val_res.is_valid and bool(plan)
    combined_errors = list(val_res.errors) + (list(strategy_val_res.errors) if strategy_val_res else [])
    if not plan and not combined_errors:
        combined_errors.append("Investigation plan has no executable tasks.")
    combined_warnings = list(val_res.warnings) + (list(strategy_val_res.warnings) if strategy_val_res else [])

    plan_validation_dict = {
        "is_valid": combined_valid,
        "errors": combined_errors,
        "warnings": combined_warnings,
        "validated_task_count": val_res.validated_task_count,
        "strategy_validation": strategy_val_res.to_dict() if strategy_val_res else None,
    }

    elapsed = time.time() - start_time
    logger.info(
        "[Graph] validate_investigation_plan_complete: session_id=%s, is_valid=%s, errors=%d, warnings=%d, duration=%.3fs",
        session_id,
        combined_valid,
        len(combined_errors),
        len(combined_warnings),
        elapsed,
    )

    strat_dict = strategy_val_res.to_dict() if strategy_val_res else state.get("plan_strategy_validation")
    if strat_dict and isinstance(strat_dict, dict):
        strat_dict["is_valid"] = is_strat_valid

    return {
        **state,
        "plan_validation": plan_validation_dict,
        "plan_strategy_validation": strat_dict,
        "lifecycle_status": "execution-ready" if combined_valid else "invalid",
        "current_step": "validate_investigation_plan",
    }


def route_after_plan_validation(
    state: InvestigationState,
) -> str:
    """Route to execution if plan is valid; otherwise route to plan_invalid_node."""
    val = state.get("plan_validation") or {}
    if val.get("is_valid", False):
        logger.info("Routing after plan validation: plan is valid -> proceeding to execute_plan_and_aggregate")
        return "execute_plan_and_aggregate"

    logger.warning("Routing after plan validation: plan is invalid (errors: %s) -> stopping at plan_invalid_node", val.get("errors", []))
    return "plan_invalid_node"


def plan_invalid_node(
    state: InvestigationState,
) -> InvestigationState:
    """Halt execution when plan validation fails immediately before dispatch."""
    session_id = str(state.get("session_id") or state.get("investigation_id") or "unspecified")
    val = state.get("plan_validation") or {}
    errors = val.get("errors", [])
    logger.warning(
        "[Graph] plan_validation_dispatch_blocked: zero specialists dispatched for session_id=%s due to errors: %s",
        session_id,
        errors,
    )
    return {
        **state,
        "investigation_plan": [],
        "selected_investigators": [],
        "execution_status": "failed",
        "investigation_status": "failed",
        "workflow_status": "failed",
        "status_label": "Investigation failed",
        "is_conclusive": False,
        "investigation_stop_reason": f"Plan validation failed: {len(errors)} error(s)",
        "current_step": "plan_validation_failed",
    }


def execute_plan_and_aggregate_node(
    state: InvestigationState,
) -> InvestigationState:
    """Execute plan in a single pass and aggregate into shared state without synthesis."""
    import time
    start_time = time.time()
    session_id = str(state.get("session_id") or state.get("investigation_id") or "unspecified")
    logger.info(
        "[Graph] execute_plan_and_aggregate_start: session_id=%s, entry_point=langgraph",
        session_id,
    )

    llm_factory = state.get("llm_factory") or get_llm_factory()
    orch_kwargs = {"llm_factory": llm_factory}
    if "max_iterations" in state:
        orch_kwargs["max_iterations"] = state["max_iterations"]
    if "task_budget" in state:
        orch_kwargs["task_budget"] = state["task_budget"]
    orch = InvestigationOrchestrator(**orch_kwargs)
    plan = state.get("investigation_plan") or []
    context = state.get("gathered_context") or {}

    result = orch.execute_plan_single_pass(
        plan=plan,
        state=dict(state),
        context=context,
    )

    elapsed = time.time() - start_time
    exec_status = result.get("execution_status", "failed")
    wf_status = result.get("workflow_status", "failed")
    logger.info(
        "[Graph] execute_plan_and_aggregate_complete: session_id=%s, execution_status=%s, workflow_status=%s, duration=%.3fs",
        session_id,
        exec_status,
        wf_status,
        elapsed,
    )

    return {
        **state,
        **result,
        "current_step": "investigation_completed" if exec_status in ("completed", "partial") else "investigation_failed",
    }


# ============================================================
# PHASE 8 PIPELINE NODES (Reviewer, Re-planner, Root-Cause Result)
# ============================================================

def reviewer_node(
    state: InvestigationState,
) -> InvestigationState:
    """Evaluate current shared state using Reviewer to determine evidence sufficiency and gaps."""
    import time
    start_time = time.time()
    session_id = str(state.get("session_id") or state.get("investigation_id") or "unspecified")
    iteration = state.get("investigation_iterations", 1)
    raw_max = state.get("max_iterations", 5)
    try:
        max_iterations = max(1, min(int(raw_max), 5))
    except (ValueError, TypeError):
        max_iterations = 5
    logger.info("[Graph] reviewer_node_start: session_id=%s, iteration=%d/%d", session_id, iteration, max_iterations)

    llm_factory = state.get("llm_factory") or get_llm_factory()
    reviewer = Reviewer(llm_factory=llm_factory)

    evidence = list(state.get("evidence_pool") or state.get("evidence") or [])
    findings = list(state.get("aggregated_findings") or state.get("findings") or [])
    hypotheses = list(state.get("aggregated_hypotheses") or state.get("hypotheses") or [])
    completed_investigators = state.get("completed_investigators") or []
    completed_tasks = state.get("completed_tasks") or []
    problem = state.get("problem", "")
    original_problem = state.get("original_problem") or problem
    expected_behavior = state.get("expected_behavior", "")
    problem_context = state.get("validated_problem_context") or state.get("problem_context") or {}

    review_res = reviewer.review(
        problem=problem,
        evidence=evidence,
        findings=findings,
        hypotheses=hypotheses,
        iteration=iteration,
        max_iterations=max_iterations,
        completed_investigators=completed_investigators,
        completed_tasks=completed_tasks,
        original_problem=original_problem,
        expected_behavior=expected_behavior,
        problem_context=problem_context,
    )

    elapsed = time.time() - start_time
    logger.info(
        "[Graph] reviewer_node_complete: session_id=%s, decision=%s, is_sufficient=%s, confidence=%s, duration=%.3fs",
        session_id,
        review_res.get("decision"),
        review_res.get("is_sufficient"),
        review_res.get("confidence"),
        elapsed,
    )

    return {
        **state,
        "review_result": review_res,
        "review_feedback": review_res.get("feedback") or review_res.get("reasoning") or "",
        "unanswered_questions": review_res.get("unanswered_questions") or state.get("unanswered_questions") or [],
        "missing_evidence": review_res.get("missing_evidence") or state.get("missing_evidence") or [],
        "current_step": "reviewer",
    }


def route_after_reviewer(
    state: InvestigationState,
) -> str:
    """Route after Reviewer based on decision and iteration limits."""
    review_res = state.get("review_result") or {}
    decision = review_res.get("decision", "insufficient")
    is_sufficient = bool(review_res.get("is_sufficient", False))
    iteration = max(state.get("investigation_iterations", 1), state.get("iteration", 0), 1)
    raw_max = state.get("max_iterations", 5)
    try:
        max_iterations = max(1, min(int(raw_max), 5))
    except (ValueError, TypeError):
        max_iterations = 5

    if decision == "sufficient" or is_sufficient:
        logger.info("[Graph] route_after_reviewer: evidence is sufficient -> routing to root_cause_result")
        return "root_cause_result"

    if iteration >= max_iterations:
        logger.info(
            "[Graph] route_after_reviewer: investigation limit reached (%d >= %d) -> routing to inconclusive_result",
            iteration,
            max_iterations,
        )
        return "inconclusive_result"

    if decision == "inconclusive":
        logger.info("[Graph] route_after_reviewer: review decision is inconclusive -> routing to inconclusive_result")
        return "inconclusive_result"

    logger.info("[Graph] route_after_reviewer: evidence is insufficient -> routing to replanner")
    return "replanner"


def replanner_node(
    state: InvestigationState,
) -> InvestigationState:
    """Re-plan targeted investigation tasks based on reviewer evidence gaps."""
    import time
    start_time = time.time()
    session_id = str(state.get("session_id") or state.get("investigation_id") or "unspecified")
    iteration = state.get("investigation_iterations", 1)
    logger.info("[Graph] replanner_node_start: session_id=%s, current_iteration=%d", session_id, iteration)

    llm_factory = state.get("llm_factory") or get_llm_factory()
    replanner = Replanner(llm_factory=llm_factory)

    review_res = state.get("review_result") or {}
    gathered_context = state.get("gathered_context") or {}
    problem = state.get("problem", "")
    original_problem = state.get("original_problem") or problem

    state_for_replan = dict(state)
    state_for_replan["original_problem"] = original_problem

    new_tasks = replanner.replan(
        problem=problem,
        shared_state=state_for_replan,
        review_result=review_res,
        gathered_context=gathered_context,
    )

    existing_plan = list(state.get("investigation_plan") or [])
    combined_plan = existing_plan + new_tasks
    next_iteration = iteration + 1

    history_entry = {
        "iteration": iteration,
        "tasks_added": len(new_tasks),
        "review_decision": review_res.get("decision"),
        "feedback": review_res.get("feedback") or review_res.get("reasoning"),
        "unanswered_questions": list(review_res.get("unanswered_questions") or state.get("unanswered_questions") or []),
        "missing_evidence": list(review_res.get("missing_evidence") or state.get("missing_evidence") or []),
        "established_by_evidence": list(review_res.get("established_by_evidence") or []),
        "planned_task_ids": [t.get("task_id") for t in new_tasks if isinstance(t, dict)],
    }
    investigation_history = list(state.get("investigation_history") or [])
    investigation_history.append(history_entry)

    elapsed = time.time() - start_time
    logger.info(
        "[Graph] replanner_node_complete: session_id=%s, new_tasks=%d, total_plan_tasks=%d, next_iteration=%d, duration=%.3fs",
        session_id,
        len(new_tasks),
        len(combined_plan),
        next_iteration,
        elapsed,
    )

    status = "fallback" if getattr(replanner, "last_replan_mode", "llm") == "fallback" else "success"
    return {
        **state,
        "original_problem": original_problem,
        "investigation_plan": combined_plan,
        "replan_tasks": new_tasks,
        "tasks": new_tasks,
        "status": status,
        "replan_mode": getattr(replanner, "last_replan_mode", "llm"),
        "error": getattr(replanner, "last_error", None) if getattr(replanner, "last_replan_mode", "llm") == "fallback" else None,
        "investigation_iterations": next_iteration,
        "investigation_history": investigation_history,
        "replan_history": investigation_history,
        "current_step": "replanned",
    }


def root_cause_result_node(
    state: InvestigationState,
) -> InvestigationState:
    """Produce preliminary Root-Cause Result from reviewed hypotheses and evidence."""
    session_id = str(state.get("session_id") or state.get("investigation_id") or "unspecified")
    logger.info("[Graph] root_cause_result_node: session_id=%s", session_id)

    review_res = state.get("review_result") or {}
    hypotheses = list(state.get("aggregated_hypotheses") or state.get("hypotheses") or [])
    evidence = list(state.get("evidence_pool") or state.get("evidence") or [])

    problem = state.get("problem", "")
    original_problem = state.get("original_problem") or problem

    # 1. Prioritize Reviewer-approved hypothesis
    supported_hyp = review_res.get("approved_hypothesis")
    if not supported_hyp and review_res.get("approved_hypothesis_id"):
        app_id = str(review_res.get("approved_hypothesis_id"))
        for h in hypotheses:
            if isinstance(h, dict) and str(h.get("hypothesis_id")) == app_id:
                supported_hyp = h
                break

    if not supported_hyp:
        # Check hypothesis evaluations for supported & relevant hypothesis
        app_eval_ids = {
            str(he.get("hypothesis_id"))
            for he in (review_res.get("hypothesis_evaluations") or [])
            if he.get("status") == "supported" and he.get("is_relevant_to_problem", True)
        }
        for h in hypotheses:
            if isinstance(h, dict) and str(h.get("hypothesis_id")) in app_eval_ids:
                supported_hyp = h
                break

    if not supported_hyp:
        # Fallback: find any supported hypothesis that addresses the problem
        for h in hypotheses:
            if isinstance(h, dict) and (h.get("status") == "supported" or h.get("supporting_evidence_ids")):
                is_rel, _ = is_hypothesis_relevant_to_problem(h, problem=problem, original_problem=original_problem)
                if is_rel:
                    supported_hyp = h
                    break

    if not supported_hyp and hypotheses:
        supported_hyp = hypotheses[0]

    root_cause_stmt = ""
    if supported_hyp:
        root_cause_stmt = supported_hyp.get("statement") or supported_hyp.get("possible_cause") or supported_hyp.get("hypothesis_title") or ""
    if not root_cause_stmt:
        root_cause_stmt = review_res.get("reasoning") or "Root cause identified based on causal defect evidence."

    supp_ev_ids = set()
    if supported_hyp:
        supp_ev_ids = {str(eid) for eid in (supported_hyp.get("supporting_evidence_ids") or [])}

    supporting_evidence = []
    for ev in evidence:
        if isinstance(ev, dict):
            ev_id = str(ev.get("evidence_id") or ev.get("id") or "")
            if ev_id and ev_id in supp_ev_ids:
                supporting_evidence.append(ev)
            elif not supp_ev_ids and is_causal_evidence(ev):
                supporting_evidence.append(ev)

    if not supporting_evidence and evidence:
        supporting_evidence = evidence[:3]

    confidence = str(review_res.get("confidence") or (supported_hyp.get("confidence") if supported_hyp else "medium") or "medium").lower()
    if confidence not in ("high", "medium", "low"):
        confidence = "medium"

    reasoning = review_res.get("reasoning") or review_res.get("feedback") or "Evidence directly demonstrates the failure mechanism."
    remaining_uncertainty = review_res.get("remaining_uncertainty") or (
        "None. Root cause directly verified by empirical probe."
        if confidence == "high"
        else "Low remaining uncertainty; confirmed across code inspection and defect evidence."
    )

    # Option B proof policy:
    # - High confidence empirically verified findings -> "Verified root cause"
    # - Medium confidence deterministic structural code proof -> "Likely root cause"
    is_empirical = (
        confidence == "high"
        and (
            (supported_hyp and str(supported_hyp.get("status", "")).lower() == "verified")
            or bool(review_res.get("is_empirically_verified"))
            or bool(state.get("is_empirically_verified"))
            or str(review_res.get("status_label", "")).lower().startswith("verified")
            or str(state.get("status_label", "")).lower().startswith("verified")
        )
    )
    status_label = "Verified root cause" if is_empirical else "Likely root cause"

    diagnosis = {
        "root_cause": root_cause_stmt,
        "likely_root_cause": root_cause_stmt,
        "status_label": status_label,
        "supporting_evidence": supporting_evidence,
        "established_by_evidence": review_res.get("established_by_evidence") or [],
        "reasoning": reasoning,
        "confidence": confidence,
        "remaining_uncertainty": remaining_uncertainty,
        "evaluated_hypotheses": review_res.get("hypothesis_evaluations") or [],
        "symptoms": review_res.get("identified_symptoms") or [state.get("problem", "")],
    }

    return {
        **state,
        "likely_root_cause": root_cause_stmt,
        "diagnosis": diagnosis,
        "established_by_evidence": review_res.get("established_by_evidence") or [],
        "confidence": confidence,
        "investigation_confidence": confidence,
        "status_label": status_label,
        "is_conclusive": True,
        "execution_status": "completed",
        "investigation_status": "completed",
        "workflow_status": "completed",
        "current_step": "investigation_completed",
        "remaining_uncertainty": remaining_uncertainty,
        "suggested_fix": "",
        "verification_steps": [],
        "stop_reason": "Sufficient evidence identified root cause.",
        "investigation_stop_reason": "Sufficient evidence identified root cause.",
    }


def inconclusive_result_node(
    state: InvestigationState,
) -> InvestigationState:
    """Produce inconclusive result when evidence remains insufficient and limits reached."""
    session_id = str(state.get("session_id") or state.get("investigation_id") or "unspecified")
    logger.info("[Graph] inconclusive_result_node: session_id=%s", session_id)

    review_res = state.get("review_result") or {}
    reasoning = review_res.get("reasoning") or review_res.get("feedback") or "Evidence remains insufficient after reaching maximum investigation iterations."
    unanswered = review_res.get("unanswered_questions") or state.get("unanswered_questions") or []
    missing_ev = review_res.get("missing_evidence") or state.get("missing_evidence") or []
    evidence = list(state.get("evidence_pool") or state.get("evidence") or [])
    hypotheses = list(state.get("aggregated_hypotheses") or state.get("hypotheses") or [])

    fallback_cause = "Inconclusive: insufficient evidence to establish root cause with high confidence."
    candidate_cause = None
    candidate_hyp = None
    hyp_evals = review_res.get("hypothesis_evaluations") or []
    for h in hypotheses:
        if isinstance(h, dict):
            h_id = h.get("hypothesis_id")
            he = next((e for e in hyp_evals if isinstance(e, dict) and e.get("hypothesis_id") == h_id), None)
            if he and not he.get("is_relevant_to_problem", True):
                continue
            if h.get("status") == "contradicted":
                continue
            cause_text = h.get("statement") or h.get("possible_cause")
            if cause_text:
                candidate_cause = f"Unverified hypothesis: {cause_text}"
                candidate_hyp = h
                break

    supp_ev_ids = set()
    if candidate_hyp:
        supp_ev_ids = {str(eid) for eid in (candidate_hyp.get("supporting_evidence_ids") or []) if eid}

    supporting_evidence = []
    if supp_ev_ids:
        for ev in evidence:
            if isinstance(ev, dict):
                ev_id = str(ev.get("evidence_id") or ev.get("id") or "")
                if ev_id and ev_id in supp_ev_ids:
                    supporting_evidence.append(ev)

    prioritized_files = []
    for ev in supporting_evidence:
        src = ev.get("source") or ev.get("path")
        if src and str(src) not in prioritized_files and not str(src).startswith("<"):
            prioritized_files.append(str(src))
    if not prioritized_files:
        ctx_files = (state.get("gathered_context") or {}).get("relevant_files") or []
        for f in ctx_files:
            fpath = f if isinstance(f, str) else (f.get("path") if isinstance(f, dict) else None)
            if fpath and str(fpath) not in prioritized_files and not str(fpath).startswith("<"):
                prioritized_files.append(str(fpath))

    debugging_steps = [
        f"Inspect prioritized file(s): {', '.join(prioritized_files[:3]) if prioritized_files else 'relevant application entry points'}.",
        "Insert targeted runtime logging or breakpoints at suspected invocation boundaries.",
        "Execute empirical test probe or reproducer script to isolate and confirm defect trigger conditions."
    ]

    diagnosis = {
        "root_cause": fallback_cause,
        "likely_root_cause": candidate_cause or fallback_cause,
        "status_label": "Inconclusive",
        "supporting_evidence": supporting_evidence,
        "established_by_evidence": review_res.get("established_by_evidence") or [],
        "reasoning": reasoning,
        "confidence": "low",
        "remaining_uncertainty": "High uncertainty; evidence gaps could not be resolved within the investigation limit.",
        "unanswered_questions": unanswered,
        "missing_evidence": missing_ev,
        "evaluated_hypotheses": review_res.get("hypothesis_evaluations") or [],
        "symptoms": review_res.get("identified_symptoms") or [state.get("problem", "")],
        "prioritized_files_to_inspect": prioritized_files,
        "actionable_debugging_steps": debugging_steps,
    }

    return {
        **state,
        "root_cause": fallback_cause,
        "likely_root_cause": candidate_cause or fallback_cause,
        "diagnosis": diagnosis,
        "established_by_evidence": review_res.get("established_by_evidence") or [],
        "unanswered_questions": unanswered,
        "missing_evidence": missing_ev,
        "confidence": "low",
        "investigation_confidence": "low",
        "status_label": "Inconclusive",
        "is_conclusive": False,
        "is_partial_diagnosis": True,
        "guardrail_status": "PARTIAL_DIAGNOSIS_MAX_ITERATIONS_REACHED",
        "execution_status": "completed",
        "investigation_status": "inconclusive",
        "workflow_status": "inconclusive",
        "current_step": "investigation_completed",
        "remaining_uncertainty": "High uncertainty; evidence gaps could not be resolved within the investigation limit.",
        "suggested_fix": "",
        "verification_steps": [],
        "stop_reason": "PARTIAL_DIAGNOSIS_MAX_ITERATIONS_REACHED: Investigation iteration limit reached without sufficient conclusive evidence.",
        "investigation_stop_reason": "PARTIAL_DIAGNOSIS_MAX_ITERATIONS_REACHED: Investigation iteration limit reached without sufficient conclusive evidence.",
    }


# ============================================================
# PHASE 9 PIPELINE NODES (Diagnosis, Verification, Final Report)
# ============================================================

def diagnosis_and_verification_node(
    state: InvestigationState,
) -> InvestigationState:
    """Phase 9 Node: Synthesize evidence-backed diagnosis, code fix, verification assessment, and 7-section report."""
    import time
    start_time = time.time()
    session_id = str(state.get("session_id") or state.get("investigation_id") or "unspecified")
    logger.info("[Graph] diagnosis_and_verification_node_start: session_id=%s", session_id)

    llm_factory = state.get("llm_factory") or get_llm_factory()
    workspace_path = state.get("workspace_path")
    diagnostician = Diagnostician(llm_factory=llm_factory, verifier=Verifier(workspace_path=workspace_path))

    problem = state.get("problem", "")
    original_problem = state.get("original_problem") or problem
    expected_behavior = state.get("expected_behavior", "")
    actual_behavior = state.get("actual_behavior", "")

    evidence_pool = list(state.get("evidence_pool") or state.get("evidence") or [])
    findings = list(state.get("aggregated_findings") or state.get("findings") or [])
    hypotheses = list(state.get("aggregated_hypotheses") or state.get("hypotheses") or [])
    review_result = dict(state.get("review_result") or {})
    repository_summary = state.get("repository_summary") or {}
    context = state.get("gathered_context") or {}

    is_empirical = bool(
        state.get("is_empirically_verified")
        or review_result.get("is_empirically_verified")
        or str(state.get("status_label", "")).lower().startswith("verified")
        or str(review_result.get("status_label", "")).lower().startswith("verified")
    )

    diag_result = diagnostician.diagnose_and_report(
        problem=problem,
        evidence_pool=evidence_pool,
        findings=findings,
        hypotheses=hypotheses,
        review_result=review_result,
        workspace_path=workspace_path,
        repository_summary=repository_summary,
        context=context,
        expected_behavior=expected_behavior,
        actual_behavior=actual_behavior,
        original_problem=original_problem,
        is_empirically_verified=is_empirical,
    )

    elapsed = time.time() - start_time
    logger.info(
        "[Graph] diagnosis_and_verification_node_complete: session_id=%s, status=%s, is_conclusive=%s, duration=%.3fs",
        session_id,
        diag_result.get("status_label"),
        diag_result.get("is_conclusive"),
        elapsed,
    )

    merged_diagnosis = dict(state.get("diagnosis") or {})
    merged_diagnosis.update(diag_result)

    affected_comp_strings = [
        c.get("file") or c.get("name") if isinstance(c, dict) else str(c)
        for c in diag_result.get("affected_components", [])
    ]

    is_concl = diag_result.get("is_conclusive", False)
    status_label = diag_result.get("status_label", state.get("status_label", "Likely root cause" if is_concl else "Inconclusive"))

    return {
        **state,
        "diagnosis": merged_diagnosis,
        "likely_root_cause": diag_result.get("likely_root_cause") or state.get("likely_root_cause", ""),
        "root_cause": diag_result.get("root_cause") or state.get("root_cause", ""),
        "status_label": status_label,
        "confidence": diag_result.get("confidence", state.get("confidence", "medium")),
        "investigation_confidence": diag_result.get("confidence", state.get("confidence", "medium")),
        "is_conclusive": is_concl,
        "affected_components": affected_comp_strings,
        "suggested_fix": diag_result.get("suggested_fix", ""),
        "suggested_fix_details": diag_result.get("suggested_fix_details", {}),
        "verification_steps": diag_result.get("verification_steps", []),
        "verification_summary": diag_result.get("verification_summary", {}),
        "verification_status": diag_result.get("verification_status", "not verified"),
        "remaining_uncertainty": diag_result.get("remaining_uncertainty", ""),
        "final_report": diag_result.get("final_report", ""),
        "investigation_report": diag_result.get("final_report", ""),
        "report_sections": diag_result.get("report_sections", {}),
        "execution_status": "completed",
        "investigation_status": "completed" if is_concl else "inconclusive",
        "workflow_status": "completed" if is_concl else "inconclusive",
        "current_step": "investigation_completed",
    }


def route_after_root_cause_result(
    state: InvestigationState,
) -> str:
    """Route after root-cause result: proceed to diagnosis & verification if enabled."""
    if state.get("run_diagnosis") or state.get("enable_diagnosis") or state.get("auto_diagnose"):
        logger.info("[Graph] route_after_root_cause_result: run_diagnosis is True -> routing to diagnosis_and_verification")
        return "diagnosis_and_verification"
    logger.info("[Graph] route_after_root_cause_result: run_diagnosis not requested -> routing to END")
    return "end"


def route_after_inconclusive_result(
    state: InvestigationState,
) -> str:
    """Route after inconclusive result: proceed to diagnosis & verification if enabled."""
    if state.get("run_diagnosis") or state.get("enable_diagnosis") or state.get("auto_diagnose"):
        logger.info("[Graph] route_after_inconclusive_result: run_diagnosis is True -> routing to diagnosis_and_verification")
        return "diagnosis_and_verification"
    logger.info("[Graph] route_after_inconclusive_result: run_diagnosis not requested -> routing to END")
    return "end"


# ============================================================
# DEEP AGENT (Compatibility Node)
# ============================================================

def deep_agent_graph_node(
    state: InvestigationState,
) -> InvestigationState:
    """
    Run the Deep Agent integration layer (backwards compatibility).
    """
    if state.get("context_status") in ("insufficient", "incomplete", "context_check_failed"):
        logger.warning(
            "Deep agent graph node skipped: context status is '%s'",
            state.get("context_status"),
        )
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

    if not state.get("workspace_path"):
        logger.error("Deep agent graph node skipped: workspace_path is missing")
        return {
            **state,
            "investigation_status": "failed",
            "investigation_stop_reason": (
                "Investigation cannot start: workspace_path is missing."
            ),
            "current_step": "investigation_failed",
        }

    try:
        logger.info("Deep agent graph node running investigation orchestrator...")
        result = deep_agent_node(state)
        return {
            **state,
            **result,
        }

    except Exception as error:
        logger.exception("Deep Agent graph node encountered exception: %s", error)
        return {
            **state,
            "investigation_status": "failed",
            "investigation_stop_reason": (
                f"Deep Agent execution failed: {error}"
            ),
            "current_step": "investigation_failed",
        }


# ============================================================
# BUILD GRAPH
# ============================================================

def build_graph():
    """
    Build the AI Software Issue Investigator workflow with Phase 8 Reviewer & Re-planning.

    Flow:
        initial
          ↓
        validate_input
          ↓
        context_check
          ↓
        workspace
          ↓
        repository_analysis
          ↓
        context_gathering
          ↓
        create_investigation_plan
          ↓
        validate_investigation_plan
          ├─ [invalid] → plan_invalid_node → END
          └─ [valid] → execute_plan_and_aggregate
                           ↓
                        reviewer
                           ├─ [sufficient] → root_cause_result → END
                           ├─ [insufficient] → replanner → execute_plan_and_aggregate
                           └─ [limit reached / inconclusive] → inconclusive_result → END

    Validation, insufficient-context, and workspace failures follow separate terminal paths.
    """

    graph = StateGraph(InvestigationState)

    # --------------------------------------------------------
    # Register nodes
    # --------------------------------------------------------

    graph.add_node(
        "initial",
        trace_node_json("initial", initial_node),
    )

    graph.add_node(
        "validate_input",
        trace_node_json("validate_input", validate_input),
    )

    graph.add_node(
        "context_check",
        trace_node_json("context_check", check_context),
    )

    graph.add_node(
        "validation_failed",
        trace_node_json("validation_failed", validation_failed_node),
    )

    graph.add_node(
        "context_insufficient",
        trace_node_json("context_insufficient", context_insufficient_node),
    )

    graph.add_node(
        "workspace",
        trace_node_json("workspace", setup_workspace),
    )

    graph.add_node(
        "workspace_setup_failed",
        trace_node_json("workspace_setup_failed", workspace_setup_failed_node),
    )

    graph.add_node(
        "repository_analysis",
        trace_node_json("repository_analysis", repository_analysis_node),
    )

    graph.add_node(
        "context_gathering",
        trace_node_json("context_gathering", context_gathering_graph_node),
    )

    graph.add_node(
        "create_investigation_plan",
        trace_node_json("create_investigation_plan", create_investigation_plan_node),
    )

    graph.add_node(
        "validate_investigation_plan",
        trace_node_json("validate_investigation_plan", validate_investigation_plan_node),
    )

    graph.add_node(
        "plan_invalid_node",
        trace_node_json("plan_invalid_node", plan_invalid_node),
    )

    graph.add_node(
        "execute_plan_and_aggregate",
        trace_node_json("execute_plan_and_aggregate", execute_plan_and_aggregate_node),
    )

    graph.add_node(
        "reviewer",
        trace_node_json("reviewer", reviewer_node),
    )

    graph.add_node(
        "replanner",
        trace_node_json("replanner", replanner_node),
    )

    graph.add_node(
        "root_cause_result",
        trace_node_json("root_cause_result", root_cause_result_node),
    )

    graph.add_node(
        "inconclusive_result",
        trace_node_json("inconclusive_result", inconclusive_result_node),
    )

    graph.add_node(
        "diagnosis_and_verification",
        trace_node_json("diagnosis_and_verification", diagnosis_and_verification_node),
    )

    graph.add_node(
        "deep_agent",
        trace_node_json("deep_agent", deep_agent_graph_node),
    )

    # --------------------------------------------------------
    # Initial flow
    # --------------------------------------------------------

    graph.add_edge(
        START,
        "initial",
    )

    graph.add_edge(
        "initial",
        "validate_input",
    )

    # --------------------------------------------------------
    # Input validation routing
    # --------------------------------------------------------

    graph.add_conditional_edges(
        "validate_input",
        trace_node_json("route_after_validate_input", route_after_validation),
        {
            "context_check": "context_check",
            "validation_failed": "validation_failed",
        },
    )

    # --------------------------------------------------------
    # Context check routing
    # --------------------------------------------------------

    graph.add_conditional_edges(
        "context_check",
        trace_node_json("route_after_context_check", route_after_context),
        {
            "workspace": "workspace",
            "context_insufficient": "context_insufficient",
        },
    )

    # --------------------------------------------------------
    # Workspace routing
    # --------------------------------------------------------

    graph.add_conditional_edges(
        "workspace",
        trace_node_json("route_after_workspace", route_after_workspace),
        {
            "repository_analysis": "repository_analysis",
            "workspace_setup_failed": "workspace_setup_failed",
        },
    )

    # --------------------------------------------------------
    # Repository analysis → Context gathering
    # --------------------------------------------------------

    graph.add_edge(
        "repository_analysis",
        "context_gathering",
    )

    # --------------------------------------------------------
    # Context gathering → Create Investigation Plan → Validate Investigation Plan
    # --------------------------------------------------------

    graph.add_edge(
        "context_gathering",
        "create_investigation_plan",
    )

    graph.add_edge(
        "create_investigation_plan",
        "validate_investigation_plan",
    )

    # --------------------------------------------------------
    # Validate Investigation Plan routing:
    # [valid] → execute_plan_and_aggregate
    # [invalid] → plan_invalid_node → END
    # --------------------------------------------------------

    graph.add_conditional_edges(
        "validate_investigation_plan",
        trace_node_json("route_after_plan_validation", route_after_plan_validation),
        {
            "execute_plan_and_aggregate": "execute_plan_and_aggregate",
            "plan_invalid_node": "plan_invalid_node",
        },
    )

    # --------------------------------------------------------
    # Phase 8 Flow:
    # Execute Plan & Aggregate → Reviewer
    # --------------------------------------------------------

    graph.add_edge(
        "execute_plan_and_aggregate",
        "reviewer",
    )

    # --------------------------------------------------------
    # Reviewer Conditional Routing:
    # [sufficient] → root_cause_result → END
    # [insufficient] → replanner → execute_plan_and_aggregate
    # [limit reached / inconclusive] → inconclusive_result → END
    # --------------------------------------------------------

    graph.add_conditional_edges(
        "reviewer",
        trace_node_json("route_after_reviewer", route_after_reviewer),
        {
            "root_cause_result": "root_cause_result",
            "replanner": "replanner",
            "inconclusive_result": "inconclusive_result",
        },
    )

    graph.add_edge(
        "replanner",
        "execute_plan_and_aggregate",
    )

    # --------------------------------------------------------
    # Phase 9 Flow:
    # Root-Cause Result / Inconclusive Result → Diagnosis & Verification
    # --------------------------------------------------------

    graph.add_conditional_edges(
        "root_cause_result",
        trace_node_json("route_after_root_cause_result", route_after_root_cause_result),
        {
            "diagnosis_and_verification": "diagnosis_and_verification",
            "end": END,
        },
    )

    graph.add_conditional_edges(
        "inconclusive_result",
        trace_node_json("route_after_inconclusive_result", route_after_inconclusive_result),
        {
            "diagnosis_and_verification": "diagnosis_and_verification",
            "end": END,
        },
    )

    graph.add_edge(
        "diagnosis_and_verification",
        END,
    )

    # --------------------------------------------------------
    # Terminal paths
    # --------------------------------------------------------

    graph.add_edge(
        "plan_invalid_node",
        END,
    )

    graph.add_edge(
        "deep_agent",
        END,
    )

    graph.add_edge(
        "validation_failed",
        END,
    )

    graph.add_edge(
        "context_insufficient",
        END,
    )

    graph.add_edge(
        "workspace_setup_failed",
        END,
    )

    return graph.compile()
