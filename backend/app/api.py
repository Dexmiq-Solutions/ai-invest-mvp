import os
import time
import uuid
import tempfile
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional
from fastapi import FastAPI, File, UploadFile, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import json
from app.graph.graph import (
    build_graph,
    initial_node,
    repository_analysis_node,
    context_gathering_graph_node,
    deep_agent_graph_node,
    reviewer_node,
    route_after_reviewer,
    replanner_node,
    root_cause_result_node,
    inconclusive_result_node,
    diagnosis_and_verification_node,
)
from app.graph.context import validate_input, check_context
from app.graph.workspace import setup_workspace
from app.node_json_logging import trace_node_json
from app.tools.investigation_tools import list_files, read_file
from app.agents.deep_agent import orchestrator
from app.agents.plan_validator import (
    validate_investigation_plan,
    validate_plan_strategy,
    PlanValidationResult,
    InvestigationPlan,
)
from app.logging_config import sanitize_log_text
from app.tracing import get_tracing_config

logger = logging.getLogger(__name__)

workflow_graph = build_graph()

app = FastAPI(title="AI Investigator Orchestration API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory session storage
sessions = {}

def _get_sessions_dir() -> Path:
    backend_dir = Path(__file__).resolve().parent.parent
    s_dir = backend_dir / "logs" / "sessions"
    s_dir.mkdir(parents=True, exist_ok=True)
    return s_dir

def _ensure_persisted_sessions_loaded():
    """Load all persisted sessions from individual session files and legacy session files."""
    backend_dir = Path(__file__).resolve().parent.parent
    sessions_dir = _get_sessions_dir()

    # 1. Load from individual session files in logs/sessions/
    for session_file in sessions_dir.glob("*.json"):
        if session_file.name.startswith(".tmp_"):
            continue
        try:
            with open(session_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                sid = data.get("session_id") or session_file.stem
                if sid and sid not in sessions and "state" in data:
                    sessions[sid] = data["state"]
        except Exception as exc:
            logger.warning("Could not read session file %s: %s", session_file, exc)

    # 2. Load and migrate legacy files if not already loaded
    legacy_files = [
        backend_dir / "session-output.json",
        *list((backend_dir / "logs").glob("session_output_*.json")),
    ]
    for p in legacy_files:
        if p.exists():
            try:
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    sid = data.get("session_id")
                    if sid and sid not in sessions and "state" in data:
                        sessions[sid] = data["state"]
                        target_file = sessions_dir / f"{sid}.json"
                        if not target_file.exists():
                            target_file.write_text(
                                json.dumps(
                                    {"session_id": sid, "state": data["state"], "updated_at": time.time()},
                                    indent=2,
                                    default=str,
                                ),
                                encoding="utf-8",
                            )
            except Exception as exc:
                logger.debug("Could not parse legacy session file %s: %s", p, exc)


def _load_sessions() -> dict:
    """Helper to ensure persisted sessions are loaded and return the sessions map."""
    _ensure_persisted_sessions_loaded()
    return sessions


def _persist_session(session_id: str, state: dict):
    """Persist session state into an individual session file and keep session-output.json updated."""
    try:
        backend_dir = Path(__file__).resolve().parent.parent
        sessions_dir = _get_sessions_dir()

        # 1. Atomically write individual session file in logs/sessions/{session_id}.json
        session_file = sessions_dir / f"{session_id}.json"
        temp_file = sessions_dir / f".tmp_{session_id}_{uuid.uuid4().hex[:8]}.json"
        payload = {
            "session_id": session_id,
            "updated_at": time.time(),
            "state": state,
        }
        temp_file.write_text(
            json.dumps(payload, indent=2, default=str),
            encoding="utf-8",
        )
        temp_file.replace(session_file)

        # 2. Also keep session-output.json updated with latest session for legacy/test compatibility
        out_file = backend_dir / "session-output.json"
        existing_data = {}
        if out_file.exists():
            try:
                existing_data = json.loads(out_file.read_text(encoding="utf-8"))
            except Exception:
                pass
        existing_data.update({"session_id": session_id, "state": state})
        out_file.write_text(
            json.dumps(existing_data, indent=2, default=str),
            encoding="utf-8",
        )
    except Exception as log_err:
        logger.warning("[%s] Could not persist session output: %s", session_id, log_err)

def _build_investigation_response(
    session_id: str,
    state: dict,
    status: Optional[str] = None,
    extra: Optional[dict] = None,
) -> dict:
    """Standardized API response contract exposing core execution metadata."""
    step = state.get("current_step")
    derived_status = status or state.get("status") or (
        "INVESTIGATION_COMPLETED" if step in ("investigation_completed", "aggregated")
        else ("INVESTIGATION_FAILED" if step and "failed" in step
        else (step if step else "IN_PROGRESS"))
    )
    resp = {
        "session_id": session_id,
        "status": derived_status,
        "current_step": step,
        "workspace_id": state.get("workspace_id"),
        "workspace_path": state.get("workspace_path"),
        "state": state,
    }
    if extra:
        resp.update(extra)
    return resp

@app.get("/api/investigations")
async def list_investigations():
    _ensure_persisted_sessions_loaded()
    investigation_list = []
    for sid, s in sessions.items():
        review_res = s.get("review_result") or {}
        investigation_list.append({
            "session_id": sid,
            "problem": s.get("problem", "")[:150],
            "current_step": s.get("current_step", "UNKNOWN"),
            "status_label": s.get("status_label", ""),
            "is_conclusive": s.get("is_conclusive", False),
            "confidence": s.get("investigation_confidence") or s.get("confidence", "low"),
            "findings_count": len(s.get("findings", []) or s.get("aggregated_findings", [])),
            "evidence_count": len(s.get("evidence", []) or s.get("evidence_pool", [])),
            "tasks_count": (
                len(s.get("completed_tasks", []) or s.get("completed_investigation_tasks", []))
                + len(s.get("skipped_tasks", []) or s.get("skipped_investigation_tasks", []))
                + len(s.get("unresolved_tasks", []))
            ) or len(s.get("investigation_plan", [])),
            "iteration": s.get("investigation_iterations", 1),
            "review_decision": review_res.get("decision"),
        })
    return {"investigations": investigation_list}

def _run_phase8_loop(state: dict, orch) -> dict:
    """Run Phase 8 reviewer & replanning loop until root cause or inconclusive result."""
    max_iterations = state.get("max_iterations", 3)
    loop_count = 0
    while loop_count < (max_iterations * 2):
        loop_count += 1
        state = reviewer_node(state)
        route = route_after_reviewer(state)
        if route == "root_cause_result":
            state = root_cause_result_node(state)
            if state.get("run_diagnosis", True):
                state = diagnosis_and_verification_node(state)
            break
        elif route == "inconclusive_result":
            state = inconclusive_result_node(state)
            if state.get("run_diagnosis", True):
                state = diagnosis_and_verification_node(state)
            break
        elif route == "replanner":
            state = replanner_node(state)
            plan = state.get("investigation_plan") or []
            context = state.get("gathered_context") or {}
            exec_res = orch.execute_plan_single_pass(
                plan=plan,
                state=state,
                context=context,
            )
            state.update(exec_res)
        else:
            break
    return state

def _record_trace(state: dict, node_name: str, start_time: float, end_time: float, status: str, details: dict | None = None):
    trace = state.setdefault("node_execution_trace", {})
    entry = {
        "node_name": node_name,
        "start_time": start_time,
        "end_time": end_time,
        "duration_seconds": round(end_time - start_time, 3),
        "status": status,
    }
    if details:
        entry.update(details)
    trace[node_name] = entry
    return state

def _find_taskflow_fixture() -> str:
    backend_dir = Path(__file__).resolve().parent.parent
    base = backend_dir / "workspaces"
    if base.exists():
        for c in base.glob("**/taskflow"):
            if (c / "backend" / "src" / "services" / "authService.js").exists():
                return str(c.resolve())
    return ""

@app.post("/api/investigation/start")
async def start_investigation(
    problem: str = Form(...),
    project_zip: UploadFile | None = File(default=None),
    repository_url: str | None = Form(default=None),
    project_path: str | None = Form(default=None),
    auto_run: bool = Form(default=False),
):
    session_id = str(uuid.uuid4())
    logger.info(
        "[%s] Investigation start request received: problem='%s', has_zip=%s, repo_url=%s, project_path=%s, auto_run=%s",
        session_id,
        sanitize_log_text(problem, max_len=100),
        bool(project_zip),
        repository_url,
        project_path,
        auto_run,
    )
    
    # Handle ZIP upload or project path
    zip_path = None
    if project_zip:
        temp_dir = Path(tempfile.gettempdir()) / "ai_investigator_uploads"
        temp_dir.mkdir(parents=True, exist_ok=True)
        safe_filename = Path(project_zip.filename or "uploaded_project.zip").name
        zip_path = temp_dir / f"{session_id}_{safe_filename}"
        with open(zip_path, "wb") as f:
            f.write(await project_zip.read())
        logger.info("[%s] Saved uploaded project ZIP to %s", session_id, zip_path)
    elif project_path and project_path.strip() and project_path.strip() != "default":
        p = Path(project_path.strip())
        if not p.is_absolute():
            backend_dir = Path(__file__).resolve().parent.parent
            workspace_root = backend_dir.parent
            if (backend_dir / p).exists():
                p = backend_dir / p
            elif (workspace_root / p).exists():
                p = workspace_root / p
            elif (Path.cwd() / p).exists():
                p = Path.cwd() / p
        zip_path = str(p.resolve())
    elif project_path and project_path.strip() == "default" and not repository_url:
        # Default to TaskFlow benchmark fixture if explicitly 'default'
        default_tf = _find_taskflow_fixture()
        if default_tf:
            zip_path = default_tf
            logger.info("[%s] Using default TaskFlow benchmark fixture: %s", session_id, zip_path)
            
    # Initial state
    state = {
        "problem": problem,
        "original_problem": problem,
        "developer_context": [],
        "context_history": [],
        "project_path": str(zip_path) if zip_path else "",
        "repository_url": repository_url if repository_url else "",
        "problem_context": {},
        "session_id": session_id,
        "run_diagnosis": True,
    }

    if auto_run:
        problem_summary = sanitize_log_text(problem, max_len=120)
        has_ws = bool(zip_path or repository_url)
        logger.info(
            "[API] investigation_start: session_id=%s, entry_point=langgraph, problem='%s', workspace_present=%s",
            session_id,
            problem_summary,
            has_ws,
        )
        logger.info("[%s] Auto-run enabled: executing compiled LangGraph workflow...", session_id)
        start_time = time.time()
        try:
            tracing_config = get_tracing_config()
            if tracing_config:
                final_state = workflow_graph.invoke(state, config=tracing_config)
            else:
                final_state = workflow_graph.invoke(state)
        except Exception as exc:
            logger.exception("[%s] LangGraph execution failed: %s", session_id, exc)
            state["validation_errors"] = [*state.get("validation_errors", []), str(exc)]
            sessions[session_id] = state
            raise HTTPException(status_code=500, detail=f"Workflow execution failed: {exc}")

        sessions[session_id] = final_state
        _persist_session(session_id, final_state)

        elapsed = time.time() - start_time
        step = final_state.get("current_step")
        if step == "validation_failed":
            return _build_investigation_response(session_id, final_state, "VALIDATION_FAILED")
        if step == "context_insufficient":
            return _build_investigation_response(session_id, final_state, "CONTEXT_INSUFFICIENT")
        if step == "workspace_setup_failed":
            return _build_investigation_response(session_id, final_state, "WORKSPACE_FAILED")
        if step == "plan_validation_failed":
            return _build_investigation_response(
                session_id,
                final_state,
                "INVESTIGATION_FAILED",
                {
                    "execution_status": "failed",
                    "workflow_status": "failed",
                    "investigation_status": "failed",
                    "status_label": "Investigation failed",
                    "is_conclusive": False,
                    "plan_validation": final_state.get("plan_validation"),
                },
            )

        inv_status = final_state.get("investigation_status")
        exec_status = final_state.get(
            "execution_status",
            "completed" if inv_status in ("completed", "partial") else "failed",
        )
        wf_status = final_state.get("workflow_status", "aggregated" if exec_status in ("completed", "partial") else "failed")
        logger.info(
            "[API] langgraph_investigation_complete: session_id=%s, execution_status=%s, workflow_status=%s, duration=%.3fs",
            session_id,
            exec_status,
            wf_status,
            elapsed,
        )
        if inv_status in ("completed", "partial") or step == "investigation_completed":
            return _build_investigation_response(
                session_id,
                final_state,
                "INVESTIGATION_COMPLETED",
                {
                    "execution_status": exec_status,
                    "workflow_status": wf_status,
                    "investigation_status": inv_status or "completed",
                    "status_label": final_state.get("status_label", "Unverified hypothesis"),
                    "is_conclusive": final_state.get("is_conclusive", False),
                    "likely_root_cause": final_state.get("likely_root_cause", ""),
                    "suggested_fix": final_state.get("suggested_fix", ""),
                    "verification_steps": final_state.get("verification_steps", []),
                    "verification_status": final_state.get("verification_status", "not verified"),
                    "remaining_uncertainty": final_state.get("remaining_uncertainty", ""),
                    "final_report": final_state.get("final_report", ""),
                    "report_sections": final_state.get("report_sections", {}),
                    "diagnosis": final_state.get("diagnosis", {}),
                },
            )
        else:
            return _build_investigation_response(
                session_id,
                final_state,
                "INVESTIGATION_FAILED",
                {
                    "execution_status": "failed",
                    "workflow_status": wf_status,
                    "investigation_status": inv_status or "failed",
                    "status_label": final_state.get("status_label", "Investigation failed"),
                    "is_conclusive": False,
                    "plan_validation": final_state.get("plan_validation"),
                },
            )

    # Step-by-step mode (auto_run == False)
    # 1. Initial Node
    state = trace_node_json("initial", initial_node)(state)
    
    # 2. Validation Node
    state = trace_node_json("validate_input", validate_input)(state)
    if state.get("validation_errors"):
        logger.warning("[%s] Input validation failed: %s", session_id, state.get("validation_errors"))
        sessions[session_id] = state
        return _build_investigation_response(session_id, state, "VALIDATION_FAILED")
        
    # 3. Context Check Node
    state = trace_node_json("context_check", check_context)(state)
    sessions[session_id] = state
    
    context_status = state.get("context_status")
    logger.info(
        "[%s] Initial context check complete: status=%s (questions: %s)",
        session_id,
        context_status,
        state.get("context_questions", []),
    )
    
    if context_status == "sufficient":
        return _build_investigation_response(session_id, state, "CONTEXT_SUFFICIENT")
    else:
        return _build_investigation_response(session_id, state, "CONTEXT_INSUFFICIENT")

class ContextAddRequest(BaseModel):
    additional_context: str

@app.post("/api/investigation/{session_id}/context")
async def add_context(session_id: str, req: ContextAddRequest):
    if session_id not in sessions:
        logger.warning("[%s] Context addition attempted for unknown session", session_id)
        raise HTTPException(status_code=404, detail="Session not found")
        
    state = sessions[session_id]
    logger.info(
        "[%s] Additional context supplied: '%s'",
        session_id,
        sanitize_log_text(req.additional_context, max_len=120),
    )
    
    new_context = req.additional_context.strip()
    if not state.get("original_problem"):
        state["original_problem"] = state.get("problem", "").strip()

    # Track developer context list and context history
    dev_contexts = list(state.get("developer_context") or [])
    if new_context and new_context not in dev_contexts:
        dev_contexts.append(new_context)
    state["developer_context"] = dev_contexts

    history = list(state.get("context_history") or [])
    history.append({
        "addition": new_context,
        "timestamp": time.time(),
        "index": len(history) + 1,
    })
    state["context_history"] = history

    # Update structured problem_context without destroying original_problem
    prob_ctx = dict(state.get("problem_context") or {})
    prob_ctx["developer_context"] = dev_contexts
    prob_ctx["additional_context"] = "\n".join(dev_contexts)
    state["problem_context"] = prob_ctx

    # Keep state["problem"] focused on the core problem statement
    state["problem"] = state["original_problem"]
    
    # Re-run Context Check
    state = check_context(state)
    sessions[session_id] = state
    
    context_status = state.get("context_status")
    logger.info("[%s] Updated context check result: status=%s", session_id, context_status)
    if context_status == "sufficient":
        return _build_investigation_response(session_id, state, "CONTEXT_SUFFICIENT")
    else:
        return _build_investigation_response(session_id, state, "CONTEXT_INSUFFICIENT")

@app.post("/api/investigation/{session_id}/workspace")
async def create_workspace(session_id: str):
    if session_id not in sessions:
        logger.warning("[%s] Workspace creation attempted for unknown session", session_id)
        raise HTTPException(status_code=404, detail="Session not found")
        
    state = sessions[session_id]

    if state.get("context_status") != "sufficient":
        logger.warning("[%s] Workspace creation aborted: context is insufficient", session_id)
        error_msg = "Workspace setup cannot start: problem context is insufficient. Please provide additional context first."
        if error_msg not in state.get("validation_errors", []):
            state["validation_errors"] = [*state.get("validation_errors", []), error_msg]
        state["current_step"] = "context_insufficient"
        sessions[session_id] = state
        return _build_investigation_response(session_id, state, "CONTEXT_INSUFFICIENT")

    logger.info("[%s] Setting up isolated workspace...", session_id)
    state = trace_node_json("workspace", setup_workspace)(state)
    sessions[session_id] = state
    
    if state.get("current_step") == "workspace_ready":
        logger.info(
            "[%s] Workspace created successfully at: %s (source_type=%s)",
            session_id,
            state.get("workspace_path"),
            state.get("workspace_source_type"),
        )
        return _build_investigation_response(session_id, state, "WORKSPACE_CREATED")
    else:
        logger.error("[%s] Workspace creation failed: %s", session_id, state.get("validation_errors"))
        return _build_investigation_response(session_id, state, "WORKSPACE_FAILED")

@app.post("/api/investigation/{session_id}/repository-analysis")
async def analyze_repository(session_id: str):
    if session_id not in sessions:
        logger.warning("[%s] Repository analysis attempted for unknown session", session_id)
        raise HTTPException(status_code=404, detail="Session not found")
        
    state = sessions[session_id]

    # 1. Prerequisite: Check context sufficiency
    if state.get("context_status") != "sufficient":
        logger.warning("[%s] Repository analysis aborted: context is insufficient", session_id)
        error_msg = "Repository analysis cannot start: problem context is insufficient. Please provide additional context first."
        if error_msg not in state.get("validation_errors", []):
            state["validation_errors"] = [*state.get("validation_errors", []), error_msg]
        state["current_step"] = "context_insufficient"
        sessions[session_id] = state
        return _build_investigation_response(session_id, state, "CONTEXT_INSUFFICIENT")

    # 2. Prerequisite: Workspace must be ready before analysis
    if not state.get("workspace_path") or state.get("current_step") not in (
        "workspace_ready",
        "repository_analyzed",
        "context_gathering_complete",
        "investigation_completed",
    ):
        logger.info("[%s] Workspace not yet prepared; setting up isolated workspace...", session_id)
        state = setup_workspace(state)
        sessions[session_id] = state
        if state.get("current_step") != "workspace_ready" or not state.get("workspace_path"):
            logger.error(
                "[%s] Workspace setup failed prior to repository analysis: %s",
                session_id,
                state.get("validation_errors"),
            )
            return _build_investigation_response(session_id, state, "WORKSPACE_FAILED")

    # 3. Execute repository analysis
    logger.info("[%s] Executing repository analysis on workspace: %s", session_id, state.get("workspace_path"))
    state = trace_node_json("repository_analysis", repository_analysis_node)(state)
    sessions[session_id] = state
    
    if state.get("current_step") == "repository_analyzed":
        summary = state.get("repository_summary") or {}
        logger.info(
            "[%s] Repository analysis complete: languages=%s, frameworks=%s, entry_points=%d",
            session_id,
            summary.get("languages", []),
            summary.get("frameworks", []),
            len(summary.get("entry_points", [])),
        )
        return _build_investigation_response(session_id, state, "REPOSITORY_ANALYZED", {"repository_summary": summary})
    else:
        logger.error("[%s] Repository analysis failed: %s", session_id, state.get("validation_errors"))
        return _build_investigation_response(session_id, state, "REPOSITORY_ANALYSIS_FAILED")

@app.post("/api/investigation/{session_id}/context-gathering")
async def gather_context(session_id: str):
    if session_id not in sessions:
        logger.warning("[%s] Context gathering attempted for unknown session", session_id)
        raise HTTPException(status_code=404, detail="Session not found")
        
    state = sessions[session_id]

    # 1. Prerequisite: Context
    if state.get("context_status") != "sufficient":
        logger.warning("[%s] Context gathering aborted: context is insufficient", session_id)
        return _build_investigation_response(session_id, state, "CONTEXT_INSUFFICIENT")

    # 2. Prerequisite: Workspace
    if not state.get("workspace_path") or state.get("current_step") not in (
        "workspace_ready",
        "repository_analyzed",
        "context_gathering_complete",
        "investigation_completed",
    ):
        logger.info("[%s] Setting up isolated workspace prior to context gathering...", session_id)
        state = setup_workspace(state)
        sessions[session_id] = state
        if state.get("current_step") != "workspace_ready" or not state.get("workspace_path"):
            logger.error("[%s] Workspace setup failed prior to context gathering: %s", session_id, state.get("validation_errors"))
            return _build_investigation_response(session_id, state, "WORKSPACE_FAILED")

    # 3. Prerequisite: Repository Analysis
    if not state.get("repository_summary") or state.get("current_step") not in (
        "repository_analyzed",
        "context_gathering_complete",
        "investigation_completed",
    ):
        logger.info("[%s] Analyzing repository prior to context gathering...", session_id)
        state = repository_analysis_node(state)
        sessions[session_id] = state
        if state.get("current_step") != "repository_analyzed":
            logger.error("[%s] Repository analysis failed prior to context gathering: %s", session_id, state.get("validation_errors"))
            return _build_investigation_response(session_id, state, "REPOSITORY_ANALYSIS_FAILED")

    start_time = time.time()
    logger.info("[%s] Executing context gathering...", session_id)
    state = trace_node_json("context_gathering", context_gathering_graph_node)(state)
    sessions[session_id] = state
    elapsed = time.time() - start_time
    
    if state.get("current_step") != "context_gathering_failed":
        gathered = state.get("gathered_context") or {}
        rel_files = gathered.get("relevant_files", [])
        logger.info(
            "[%s] Context gathering completed in %.3fs: %d relevant files",
            session_id,
            elapsed,
            len(rel_files),
        )
        return _build_investigation_response(
            session_id,
            state,
            "CONTEXT_GATHERING_COMPLETE",
            {
                "gathered_context": gathered,
                "context_completeness": {
                    "is_sufficient": state.get("context_status") == "sufficient",
                    "context_status": state.get("context_status", "sufficient"),
                    "reason": state.get("context_reason", ""),
                },
            },
        )
    else:
        logger.error("[%s] Context gathering failed in %.3fs: %s", session_id, elapsed, state.get("validation_errors"))
        return _build_investigation_response(session_id, state, "CONTEXT_GATHERING_FAILED")

@app.post("/api/investigation/{session_id}/plan")
@app.get("/api/investigation/{session_id}/plan")
async def get_or_create_plan(session_id: str):
    if session_id not in sessions:
        logger.warning("[%s] Plan requested for unknown session", session_id)
        raise HTTPException(status_code=404, detail="Session not found")
        
    state = sessions[session_id]
    if not state.get("investigation_plan") and state.get("lifecycle_status") != "invalid":
        try:
            logger.info("[%s] Generating investigation plan artifact and tasks...", session_id)
            plan = orchestrator.create_initial_plan(state)
            state["investigation_plan"] = plan
            state["selected_investigators"] = [
                step.get("assigned_agent") or step.get("investigator")
                for step in plan
                if isinstance(step, dict) and (step.get("assigned_agent") or step.get("investigator"))
            ]
            sessions[session_id] = state
            logger.info("[%s] Generated investigation plan with %d steps", session_id, len(plan))
        except Exception as e:
            logger.exception("[%s] Failed to generate investigation plan: %s", session_id, e)
            raise HTTPException(status_code=500, detail=f"Failed to generate investigation plan: {e}")
    elif not state.get("plan"):
        state["plan"] = orchestrator.get_or_create_plan_artifact(state).to_dict()
        sessions[session_id] = state
            
    val_res = validate_investigation_plan(
        plan=state.get("investigation_plan", []),
        available_agents={"code_investigator", "dependency_impact_investigator", "runtime_error_investigator"},
        problem=state.get("problem", ""),
        gathered_context=state.get("gathered_context", {}),
        session_id=session_id,
    )
    strategy_val_res = None
    if state.get("plan"):
        strategy_val_res = validate_plan_strategy(
            plan=state["plan"],
            problem=state.get("problem", ""),
            gathered_context=state.get("gathered_context", {}),
            session_id=session_id,
        )

    is_strat_valid = strategy_val_res.is_valid if strategy_val_res is not None else True
    has_tasks = bool(state.get("investigation_plan"))
    is_overall_valid = is_strat_valid and val_res.is_valid and has_tasks

    unified_errors = list(val_res.errors) + (list(strategy_val_res.errors) if strategy_val_res is not None else [])
    unified_warnings = list(val_res.warnings) + (list(strategy_val_res.warnings) if strategy_val_res is not None else [])

    plan_validation_dict = {
        "is_valid": is_overall_valid,
        "errors": unified_errors,
        "warnings": unified_warnings,
        "validated_task_count": val_res.validated_task_count,
        "strategy_validation": strategy_val_res.to_dict() if strategy_val_res is not None else None,
    }

    state["plan_validation"] = plan_validation_dict
    if strategy_val_res is not None:
        strat_dict = strategy_val_res.to_dict()
        strat_dict["is_valid"] = is_strat_valid
        state["plan_strategy_validation"] = strat_dict

    lifecycle_status = state.get("lifecycle_status", "validated")
    if not is_overall_valid:
        lifecycle_status = "invalid"
        state["lifecycle_status"] = "invalid"
        state["investigation_plan"] = []
    elif lifecycle_status in ("draft", None):
        lifecycle_status = "execution-ready"
        state["lifecycle_status"] = "execution-ready"

    sessions[session_id] = state

    return _build_investigation_response(
        session_id=session_id,
        state=state,
        status="PLAN_READY" if is_overall_valid else "PLAN_INVALID",
        extra={
            "plan_id": state.get("plan_id"),
            "plan": state.get("plan"),
            "lifecycle_status": lifecycle_status,
            "investigation_plan": state.get("investigation_plan", []),
            "selected_investigators": state.get("selected_investigators", []),
            "plan_validation": plan_validation_dict,
            "plan_strategy_validation": state.get("plan_strategy_validation"),
        },
    )

@app.get("/api/investigation/{session_id}/plan/artifact")
async def get_plan_artifact(session_id: str):
    if session_id not in sessions:
        logger.warning("[%s] Plan artifact requested for unknown session", session_id)
        raise HTTPException(status_code=404, detail="Session not found")
        
    state = sessions[session_id]
    if not state.get("plan"):
        try:
            logger.info("[%s] Generating investigation plan artifact on demand...", session_id)
            orchestrator.create_initial_plan(state)
            sessions[session_id] = state
        except Exception as e:
            logger.exception("[%s] Failed to generate plan artifact: %s", session_id, e)
            raise HTTPException(status_code=500, detail=f"Failed to generate plan artifact: {e}")

    strategy_val_res = None
    if state.get("plan"):
        strategy_val_res = validate_plan_strategy(
            plan=state["plan"],
            problem=state.get("problem", ""),
            gathered_context=state.get("gathered_context", {}),
            session_id=session_id,
        )

    return {
        "session_id": session_id,
        "plan_id": state.get("plan_id"),
        "plan": state.get("plan"),
        "lifecycle_status": state.get("lifecycle_status", "validated"),
        "plan_strategy_validation": strategy_val_res.to_dict() if strategy_val_res else state.get("plan_strategy_validation"),
    }

@app.post("/api/investigation/{session_id}/execute")
@app.post("/api/investigation/{session_id}/deep-agent")
@app.post("/api/investigation/{session_id}/investigate")
async def execute_investigation(session_id: str):
    if session_id not in sessions:
        logger.warning("[%s] Investigation execution attempted for unknown session", session_id)
        raise HTTPException(status_code=404, detail="Session not found")
        
    state = sessions[session_id]
    start_time = time.time()
    problem_summary = sanitize_log_text(state.get("problem", ""), max_len=120)
    has_ws = bool(state.get("workspace_path"))
    logger.info(
        "[API] investigation_start: session_id=%s, entry_point=step_rest, problem='%s', workspace_present=%s",
        session_id,
        problem_summary,
        has_ws,
    )

    plan = state.get("investigation_plan")
    if plan is None and state.get("lifecycle_status") != "invalid":
        try:
            logger.info("[%s] Creating initial plan before specialist dispatch...", session_id)
            plan = orchestrator.create_initial_plan(state)
            state["investigation_plan"] = plan
            state["selected_investigators"] = [
                step.get("assigned_agent") or step.get("investigator")
                for step in plan
                if isinstance(step, dict) and (step.get("assigned_agent") or step.get("investigator"))
            ]
        except Exception as exc:
            logger.exception("[%s] Could not create initial investigation plan: %s", session_id, exc)
            state["investigation_status"] = "failed"
            state["execution_status"] = "failed"
            state["workflow_status"] = "failed"
            state["current_step"] = "investigation_failed"
            state["stop_reason"] = f"Plan creation failed: {exc}"
            sessions[session_id] = state
            return {
                "session_id": session_id,
                "state": state,
                "status": "INVESTIGATION_FAILED",
                "execution_status": "failed",
                "workflow_status": "failed",
                "investigation_status": "failed",
            }

    if state.get("lifecycle_status") == "invalid" or not plan:
        val = state.get("plan_validation") or {}
        errors = val.get("errors", ["Plan validation failed"])
        logger.warning("[%s] Dispatch blocked: plan is invalid or has no executable tasks", session_id)
        state["investigation_status"] = "failed"
        state["execution_status"] = "failed"
        state["workflow_status"] = "failed"
        state["current_step"] = "plan_validation_failed"
        state["stop_reason"] = f"Plan validation failed: {'; '.join(errors)}"
        state["investigation_plan"] = []
        state["selected_investigators"] = []
        sessions[session_id] = state
        return {
            "session_id": session_id,
            "state": state,
            "status": "INVESTIGATION_FAILED",
            "execution_status": "failed",
            "workflow_status": "failed",
            "investigation_status": "failed",
        }

    # Re-validate plan with context and execute single-pass aggregation
    result = orchestrator.execute_plan_single_pass(
        plan=plan,
        state=state,
        context=state.get("gathered_context") or {},
    )
    state.update(result)

    execution_status = state.get("execution_status", "failed")
    if state.get("auto_run") and execution_status in ("completed", "partial"):
        state = _run_phase8_loop(state, orchestrator)

    sessions[session_id] = state
    _persist_session(session_id, state)

    elapsed = time.time() - start_time
    inv_status = state.get("investigation_status")
    execution_status = state.get("execution_status", "failed")
    workflow_status = state.get("workflow_status", "failed")
    findings_count = len(state.get("findings", []))
    logger.info(
        "[API] investigation_execute_complete: session_id=%s, execution_status=%s, workflow_status=%s, findings=%d, duration=%.3fs",
        session_id,
        execution_status,
        workflow_status,
        findings_count,
        elapsed,
    )

    status_str = "INVESTIGATION_COMPLETED" if execution_status in ("completed", "partial") else "INVESTIGATION_FAILED"
    return _build_investigation_response(
        session_id=session_id,
        state=state,
        status=status_str,
        extra={
            "execution_status": execution_status,
            "workflow_status": workflow_status,
            "investigation_status": inv_status,
            "status_label": state.get("status_label", "Unverified hypothesis"),
            "is_conclusive": state.get("is_conclusive", False),
            "confidence": state.get("confidence") or state.get("investigation_confidence", "medium"),
            "review_result": state.get("review_result"),
            "investigation_iterations": state.get("investigation_iterations", 1),
            "investigation_history": state.get("investigation_history", []),
            "replan_history": state.get("replan_history", state.get("investigation_history", [])),
            "unresolved_tasks": state.get("unresolved_tasks", []),
            "established_by_evidence": state.get("established_by_evidence", []),
            "unanswered_objectives": state.get("unanswered_objectives", []),
            "evidence_gaps": state.get("evidence_gaps", []),
            "actionable_feedback": state.get("actionable_feedback"),
            "likely_root_cause": state.get("likely_root_cause"),
            "approved_hypothesis_id": state.get("approved_hypothesis_id"),
            "approved_hypothesis": state.get("approved_hypothesis"),
            "supporting_evidence": state.get("supporting_evidence", []),
            "diagnosis": state.get("diagnosis"),
            "suggested_fix": state.get("suggested_fix", ""),
            "suggested_fix_details": state.get("suggested_fix_details", {}),
            "verification_steps": state.get("verification_steps", []),
            "verification_summary": state.get("verification_summary", {}),
            "verification_status": state.get("verification_status", "not verified"),
            "remaining_uncertainty": state.get("remaining_uncertainty", ""),
            "final_report": state.get("final_report", ""),
            "report_sections": state.get("report_sections", {}),
            "plan_validation": state.get("plan_validation"),
            "evidence_pool": state.get("evidence_pool", []),
            "evidence": state.get("evidence", []),
            "aggregated_findings": state.get("aggregated_findings", []),
            "findings": state.get("findings", []),
            "aggregated_hypotheses": state.get("aggregated_hypotheses", []),
            "hypotheses": state.get("hypotheses", []),
            "investigation_plan": state.get("investigation_plan", []),
            "tool_executions": state.get("tool_executions", []),
            "guardrail_status": state.get("guardrail_status", []),
            "guardrail_warnings": state.get("guardrail_warnings", []),
            "is_partial_diagnosis": state.get("is_partial_diagnosis", False),
        },
    )

@app.get("/api/investigation/{session_id}")
async def get_investigation_session(session_id: str):
    if session_id not in sessions:
        _ensure_persisted_sessions_loaded()
    if session_id not in sessions:
        logger.warning("[%s] Session lookup failed: unknown session", session_id)
        raise HTTPException(status_code=404, detail="Session not found")
        
    state = sessions[session_id]
    return _build_investigation_response(session_id, state)

@app.get("/api/investigation/{session_id}/files")
async def get_workspace_files(session_id: str):
    if session_id not in sessions:
        logger.warning("[%s] Files listing failed: unknown session", session_id)
        raise HTTPException(status_code=404, detail="Session not found")
        
    state = sessions[session_id]
    workspace_path = state.get("workspace_path")
    if not workspace_path:
        raise HTTPException(status_code=400, detail="Workspace has not been created yet")
        
    try:
        files_data = list_files(workspace_path)
        logger.info("[%s] Listed files from workspace %s", session_id, workspace_path)
        return {"session_id": session_id, "workspace_path": workspace_path, **files_data}
    except Exception as e:
        logger.exception("[%s] Failed to list files in %s: %s", session_id, workspace_path, e)
        raise HTTPException(status_code=500, detail=f"Failed to list files: {e}")

@app.get("/api/investigation/{session_id}/files/content")
async def get_file_content(session_id: str, path: str):
    if session_id not in sessions:
        logger.warning("[%s] File content read failed: unknown session", session_id)
        raise HTTPException(status_code=404, detail="Session not found")
        
    state = sessions[session_id]
    workspace_path = state.get("workspace_path")
    if not workspace_path:
        raise HTTPException(status_code=400, detail="Workspace has not been created yet")
        
    try:
        content_data = read_file(workspace_path, path)
        logger.info("[%s] Read file content for: %s", session_id, path)
        return content_data
    except Exception as e:
        logger.exception("[%s] Failed to read file %s: %s", session_id, path, e)
        raise HTTPException(status_code=500, detail=f"Failed to read file: {e}")

@app.post("/api/investigation/{session_id}/run-full")
async def run_full_investigation(
    session_id: str,
    auto_review: bool = False,
):
    """Run all remaining stages to completion in one go."""
    if session_id not in sessions:
        logger.warning("[%s] Full run attempted on unknown session", session_id)
        raise HTTPException(status_code=404, detail="Session not found")
        
    state = sessions[session_id]
    start_time = time.time()
    problem_summary = sanitize_log_text(state.get("problem", ""), max_len=120)
    has_ws = bool(state.get("workspace_path"))
    logger.info(
        "[API] investigation_start: session_id=%s, entry_point=run_full, problem='%s', workspace_present=%s",
        session_id,
        problem_summary,
        has_ws,
    )
    
    # 1. Setup workspace if not ready
    if state.get("current_step") not in ("workspace_ready", "repository_analyzed", "context_gathering_complete", "investigation_completed"):
        if state.get("context_status") != "sufficient":
            logger.warning("[%s] Full run aborted: context is insufficient", session_id)
            return {"session_id": session_id, "state": state, "status": "CONTEXT_INSUFFICIENT"}
        logger.info("[%s] Full run: setting up workspace...", session_id)
        state = trace_node_json("workspace", setup_workspace)(state)
        sessions[session_id] = state
        if state.get("current_step") != "workspace_ready":
            logger.error("[%s] Full run: workspace setup failed", session_id)
            return {"session_id": session_id, "state": state, "status": "WORKSPACE_FAILED"}
            
    # 2. Repository analysis if not analyzed
    if state.get("current_step") in ("workspace_ready",):
        logger.info("[%s] Full run: analyzing repository...", session_id)
        state = trace_node_json("repository_analysis", repository_analysis_node)(state)
        sessions[session_id] = state
        if state.get("current_step") != "repository_analyzed":
            logger.error("[%s] Full run: repository analysis failed", session_id)
            return {"session_id": session_id, "state": state, "status": "REPOSITORY_ANALYSIS_FAILED"}
            
    # 3. Context gathering if not gathered
    if state.get("current_step") in ("repository_analyzed",):
        logger.info("[%s] Full run: gathering context...", session_id)
        state = trace_node_json("context_gathering", context_gathering_graph_node)(state)
        sessions[session_id] = state
        if state.get("current_step") == "context_gathering_failed":
            logger.error("[%s] Full run: context gathering failed", session_id)
            return {"session_id": session_id, "state": state, "status": "CONTEXT_GATHERING_FAILED"}
            
    # 4. Plan creation if not present
    plan = state.get("investigation_plan")
    if plan is None:
        try:
            logger.info("[%s] Full run: planning investigation...", session_id)
            plan = orchestrator.create_initial_plan(state)
            state["investigation_plan"] = plan
            state["selected_investigators"] = [
                step.get("assigned_agent") or step.get("investigator")
                for step in plan
                if isinstance(step, dict) and (step.get("assigned_agent") or step.get("investigator"))
            ]
        except Exception as exc:
            logger.exception("Could not create initial investigation plan for session %s", session_id)
            state["plan_generation_error"] = str(exc)
            state["investigation_status"] = "failed"
            state["execution_status"] = "failed"
            state["workflow_status"] = "failed"
            state["current_step"] = "investigation_failed"
            sessions[session_id] = state
            return {
                "session_id": session_id,
                "state": state,
                "status": "INVESTIGATION_FAILED",
                "execution_status": "failed",
                "workflow_status": "failed",
                "investigation_status": "failed",
            }

    # 5. Shared orchestrator single pass execution (validates plan, executes specialists, aggregates state, stops without synthesis)
    logger.info("[%s] Full run: executing shared single pass investigation...", session_id)
    result = orchestrator.execute_plan_single_pass(
        plan=plan,
        state=state,
        context=state.get("gathered_context") or {},
    )
    state.update(result)
    
    execution_status = state.get("execution_status", "failed")
    if (auto_review or state.get("auto_review")) and execution_status in ("completed", "partial"):
        state = _run_phase8_loop(state, orchestrator)

    sessions[session_id] = state
    _persist_session(session_id, state)
    
    elapsed = time.time() - start_time
    inv_status = state.get("investigation_status")
    execution_status = state.get("execution_status", "failed")
    workflow_status = state.get("workflow_status", "failed")
    findings_count = len(state.get("findings", []))
    logger.info(
        "[API] full_run_complete: session_id=%s, execution_status=%s, workflow_status=%s, findings=%d, duration=%.3fs",
        session_id,
        execution_status,
        workflow_status,
        findings_count,
        elapsed,
    )
    status_str = "INVESTIGATION_COMPLETED" if execution_status in ("completed", "partial") else "INVESTIGATION_FAILED"
    return _build_investigation_response(
        session_id=session_id,
        state=state,
        status=status_str,
        extra={
            "execution_status": execution_status,
            "workflow_status": workflow_status,
            "investigation_status": inv_status,
            "status_label": state.get("status_label", "Unverified hypothesis"),
            "is_conclusive": state.get("is_conclusive", False),
            "confidence": state.get("confidence") or state.get("investigation_confidence", "medium"),
            "review_result": state.get("review_result"),
            "investigation_iterations": state.get("investigation_iterations", 1),
            "investigation_history": state.get("investigation_history", []),
            "replan_history": state.get("replan_history", state.get("investigation_history", [])),
            "unresolved_tasks": state.get("unresolved_tasks", []),
            "established_by_evidence": state.get("established_by_evidence", []),
            "unanswered_objectives": state.get("unanswered_objectives", []),
            "evidence_gaps": state.get("evidence_gaps", []),
            "actionable_feedback": state.get("actionable_feedback"),
            "likely_root_cause": state.get("likely_root_cause"),
            "approved_hypothesis_id": state.get("approved_hypothesis_id"),
            "approved_hypothesis": state.get("approved_hypothesis"),
            "supporting_evidence": state.get("supporting_evidence", []),
            "diagnosis": state.get("diagnosis"),
            "suggested_fix": state.get("suggested_fix", ""),
            "suggested_fix_details": state.get("suggested_fix_details", {}),
            "verification_steps": state.get("verification_steps", []),
            "verification_summary": state.get("verification_summary", {}),
            "verification_status": state.get("verification_status", "not verified"),
            "remaining_uncertainty": state.get("remaining_uncertainty", ""),
            "final_report": state.get("final_report", ""),
            "report_sections": state.get("report_sections", {}),
            "plan_validation": state.get("plan_validation"),
            "evidence_pool": state.get("evidence_pool", []),
            "evidence": state.get("evidence", []),
            "aggregated_findings": state.get("aggregated_findings", []),
            "findings": state.get("findings", []),
            "aggregated_hypotheses": state.get("aggregated_hypotheses", []),
            "hypotheses": state.get("hypotheses", []),
            "investigation_plan": state.get("investigation_plan", []),
        },
    )

@app.post("/api/investigation/{session_id}/review")
async def review_investigation_endpoint(session_id: str):
    """Explicitly trigger Reviewer evaluation on current shared state."""
    if session_id not in sessions:
        _ensure_persisted_sessions_loaded()
    if session_id not in sessions:
        raise HTTPException(status_code=404, detail="Session not found")
    
    state = sessions[session_id]
    state = reviewer_node(state)
    route = route_after_reviewer(state)
    if route == "root_cause_result":
        state = root_cause_result_node(state)
    elif route == "inconclusive_result":
        state = inconclusive_result_node(state)
    
    sessions[session_id] = state
    _persist_session(session_id, state)
    return _build_investigation_response(
        session_id=session_id,
        state=state,
        status="REVIEWED",
        extra={
            "review_result": state.get("review_result"),
            "route": route,
            "is_conclusive": state.get("is_conclusive", False),
            "status_label": state.get("status_label", ""),
            "established_by_evidence": state.get("established_by_evidence", []),
            "unanswered_objectives": state.get("unanswered_objectives", []),
            "evidence_gaps": state.get("evidence_gaps", []),
            "actionable_feedback": state.get("actionable_feedback"),
            "likely_root_cause": state.get("likely_root_cause"),
            "approved_hypothesis_id": state.get("approved_hypothesis_id"),
            "approved_hypothesis": state.get("approved_hypothesis"),
            "supporting_evidence": state.get("supporting_evidence", []),
        },
    )

@app.post("/api/investigation/{session_id}/replan")
async def replan_investigation_endpoint(session_id: str):
    """Explicitly trigger Re-planner and execute newly targeted investigation tasks."""
    if session_id not in sessions:
        _ensure_persisted_sessions_loaded()
    if session_id not in sessions:
        raise HTTPException(status_code=404, detail="Session not found")
    
    state = sessions[session_id]
    state = replanner_node(state)
    plan = state.get("investigation_plan") or []
    context = state.get("gathered_context") or {}
    exec_res = orchestrator.execute_plan_single_pass(
        plan=plan,
        state=state,
        context=context,
    )
    state.update(exec_res)
    # Re-evaluate updated shared state with reviewer
    state = reviewer_node(state)
    route = route_after_reviewer(state)
    if route == "root_cause_result":
        state = root_cause_result_node(state)
    elif route == "inconclusive_result":
        state = inconclusive_result_node(state)
    
    sessions[session_id] = state
    _persist_session(session_id, state)
    return _build_investigation_response(
        session_id=session_id,
        state=state,
        status="REPLANNED",
        extra={
            "investigation_iterations": state.get("investigation_iterations"),
            "investigation_history": state.get("investigation_history"),
            "replan_history": state.get("replan_history", state.get("investigation_history", [])),
            "unresolved_tasks": state.get("unresolved_tasks", []),
            "review_result": state.get("review_result"),
            "is_conclusive": state.get("is_conclusive", False),
            "status_label": state.get("status_label", ""),
            "established_by_evidence": state.get("established_by_evidence", []),
            "unanswered_objectives": state.get("unanswered_objectives", []),
            "evidence_gaps": state.get("evidence_gaps", []),
            "actionable_feedback": state.get("actionable_feedback"),
            "likely_root_cause": state.get("likely_root_cause"),
            "approved_hypothesis_id": state.get("approved_hypothesis_id"),
            "approved_hypothesis": state.get("approved_hypothesis"),
            "supporting_evidence": state.get("supporting_evidence", []),
        },
    )

@app.post("/api/investigation/{session_id}/diagnosis")
async def run_diagnosis_endpoint(session_id: str):
    """Trigger Phase 9 diagnosis & verification and final report synthesis."""
    if session_id not in sessions:
        _ensure_persisted_sessions_loaded()
    if session_id not in sessions:
        raise HTTPException(status_code=404, detail="Session not found")

    state = sessions[session_id]
    state["run_diagnosis"] = True
    state = diagnosis_and_verification_node(state)
    sessions[session_id] = state
    _persist_session(session_id, state)
    return _build_investigation_response(
        session_id=session_id,
        state=state,
        status="DIAGNOSIS_COMPLETED",
        extra={
            "diagnosis": state.get("diagnosis", {}),
            "likely_root_cause": state.get("likely_root_cause", ""),
            "status_label": state.get("status_label", ""),
            "confidence": state.get("confidence", "low"),
            "is_conclusive": state.get("is_conclusive", False),
            "suggested_fix": state.get("suggested_fix", ""),
            "suggested_fix_details": state.get("suggested_fix_details", {}),
            "verification_steps": state.get("verification_steps", []),
            "verification_status": state.get("verification_status", "not verified"),
            "remaining_uncertainty": state.get("remaining_uncertainty", ""),
            "final_report": state.get("final_report", ""),
            "report_sections": state.get("report_sections", {}),
        },
    )

@app.get("/api/investigation/{session_id}/report")
async def get_investigation_report_endpoint(session_id: str):
    """Retrieve the Phase 9 Final Investigation Report (7 sections)."""
    if session_id not in sessions:
        _ensure_persisted_sessions_loaded()
    if session_id not in sessions:
        raise HTTPException(status_code=404, detail="Session not found")

    state = sessions[session_id]
    final_report = state.get("final_report")
    if not final_report:
        # Run diagnosis on demand if review result exists
        state["run_diagnosis"] = True
        state = diagnosis_and_verification_node(state)
        sessions[session_id] = state
        _persist_session(session_id, state)

    return _build_investigation_response(
        session_id=session_id,
        state=state,
        status="REPORT_RETRIEVED",
        extra={
            "final_report": state.get("final_report", ""),
            "report_sections": state.get("report_sections", {}),
            "diagnosis": state.get("diagnosis", {}),
            "suggested_fix": state.get("suggested_fix", ""),
            "verification_steps": state.get("verification_steps", []),
            "status_label": state.get("status_label", ""),
            "confidence": state.get("confidence", "low"),
            "is_conclusive": state.get("is_conclusive", False),
        },
    )

