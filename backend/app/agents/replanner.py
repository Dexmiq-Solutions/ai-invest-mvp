"""Phase 8 Re-planner Agent.

Compares identified evidence gaps and unanswered questions against the existing
shared state, determines what remains unresolved, and creates only the necessary
additional investigation tasks.
Appends new tasks to the existing plan without replacing previous tasks or findings.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Callable, Dict, List, Optional
from pydantic import BaseModel, Field

logger = logging.getLogger("app.agents.replanner")


class ReplanTaskSchema(BaseModel):
    task_id: str = Field(description="Unique task identifier")
    assigned_agent: str = Field(description="Specialist agent: 'code_investigator', 'dependency_impact_investigator', or 'runtime_error_investigator'")
    objective: str = Field(description="Neutral objective to verify")
    reason: str = Field(description="Why this task is needed based on reviewer feedback")
    instructions: str = Field(description="Actionable inspection instructions")
    scope: List[str] = Field(default_factory=list, description="Target file paths")
    expected_output: str = Field(default="Evidence snippet", description="Expected evidence")
    expected_evidence: str = Field(default="Evidence snippet", description="Expected evidence")
    completion_criteria: str = Field(default="Completed inspection", description="Completion criteria")
    unanswered_question: Optional[str] = Field(default=None, description="Specific open question being resolved")
    target_hypothesis_id: Optional[str] = Field(default=None, description="Affected hypothesis ID")


class ReplanOutputSchema(BaseModel):
    new_tasks: List[ReplanTaskSchema] = Field(default_factory=list, description="List of targeted additional tasks")
    rationale: str = Field(description="Explanation of how the new tasks address the identified evidence gaps")


class Replanner:
    """Creates targeted additional tasks addressing reviewer-identified evidence gaps."""

    def __init__(self, llm_factory: Optional[Callable] = None):
        self.llm_factory = llm_factory
        self.last_replan_mode = "llm"
        self.last_error: Optional[str] = None

    def replan(
        self,
        problem: str,
        shared_state: Dict[str, Any],
        review_result: Dict[str, Any],
        gathered_context: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Determine what remains unresolved and create only the necessary additional tasks.
        Returns a list of new pending tasks (does not overwrite or clear previous tasks).
        """
        self.last_error = None
        # 1. Try structured LLM re-planning if available
        llm_tasks = self._replan_with_llm(
            problem=problem,
            shared_state=shared_state,
            review_result=review_result,
            gathered_context=gathered_context or {},
        )
        if llm_tasks is not None:
            self.last_replan_mode = "llm"
            return llm_tasks

        # 2. Heuristic fallback re-planning
        self.last_replan_mode = "fallback"
        return self._replan_heuristically(
            problem=problem,
            shared_state=shared_state,
            review_result=review_result,
            gathered_context=gathered_context or {},
        )

    def _replan_with_llm(
        self,
        problem: str,
        shared_state: Dict[str, Any],
        review_result: Dict[str, Any],
        gathered_context: Dict[str, Any],
    ) -> Optional[List[Dict[str, Any]]]:
        if not self.llm_factory:
            return None

        try:
            llm = self.llm_factory()
            if not llm:
                return None

            orig_problem = shared_state.get("original_problem") or problem
            completed_tasks = shared_state.get("completed_tasks") or []
            existing_plan = shared_state.get("investigation_plan") or []
            unanswered_questions = review_result.get("unanswered_questions") or shared_state.get("unanswered_questions") or []
            missing_evidence = (
                review_result.get("missing_evidence")
                or review_result.get("evidence_gaps")
                or shared_state.get("missing_evidence")
                or []
            )
            feedback = review_result.get("feedback") or review_result.get("reasoning") or ""
            relevant_files = [
                f if isinstance(f, str) else f.get("path")
                for f in (gathered_context.get("relevant_files") or [])
                if f
            ]

            prompt = (
                "You are the Investigation Re-planner for an AI Software Issue Investigator.\n"
                "The Reviewer has determined that current evidence is insufficient to diagnose the root cause.\n"
                "Your objective is to inspect the identified evidence gaps and unanswered questions against the\n"
                "existing shared state, determine what remains unresolved, and create ONLY the necessary additional tasks.\n\n"
                f"Original Problem:\n{orig_problem}\n\n"
                f"Problem Statement:\n{problem}\n\n"
                f"Reviewer Feedback:\n{feedback}\n\n"
                f"Unanswered Questions:\n{json.dumps(unanswered_questions, indent=2, default=str)}\n\n"
                f"Missing Evidence Gaps:\n{json.dumps(missing_evidence, indent=2, default=str)}\n\n"
                f"Completed Tasks ({len(completed_tasks)} tasks):\n"
                f"{json.dumps([{'task_id': t.get('task_id'), 'agent': t.get('assigned_agent') or t.get('investigator'), 'task': t.get('task')} for t in completed_tasks[:10]], indent=2, default=str)}\n\n"
                f"Relevant Workspace Files:\n{json.dumps(relevant_files[:15], indent=2, default=str)}\n\n"
                "Planning Rules:\n"
                "- Do NOT duplicate previously executed tasks or ineffective retries.\n"
                "- Only select specialist agents needed to resolve the remaining questions ('code_investigator', 'dependency_impact_investigator', or 'runtime_error_investigator').\n"
                "- Assign specific, targeted scopes and actionable instructions.\n"
                "- Formulate objectives neutrally as questions to verify without assuming root cause.\n"
                "- Generate between 1 and 3 focused tasks."
            )

            structured_llm = llm.with_structured_output(ReplanOutputSchema) if hasattr(llm, "with_structured_output") else None
            if structured_llm:
                result = structured_llm.invoke(prompt)
                res_dict = result.model_dump() if hasattr(result, "model_dump") else dict(result)
            else:
                raw_res = llm.invoke(prompt)
                content = getattr(raw_res, "content", str(raw_res))
                res_dict = json.loads(content)

            raw_new_tasks = res_dict.get("new_tasks") or res_dict.get("tasks") or []
            if not isinstance(raw_new_tasks, list) or (not raw_new_tasks and "unexpected" in res_dict):
                self.last_error = "Invalid LLM output: missing new_tasks or tasks"
                return None

            base_idx = len(existing_plan)
            normalized_tasks = []
            for i, t in enumerate(raw_new_tasks):
                t_dict = dict(t) if isinstance(t, dict) else t.model_dump()
                agent = t_dict.get("assigned_agent") or "code_investigator"
                if agent not in ("code_investigator", "dependency_impact_investigator", "runtime_error_investigator"):
                    agent = "code_investigator"
                t_id = t_dict.get("task_id") or f"TASK-REPLAN-{base_idx + i + 1:03d}"
                exp_ev = str(t_dict.get("expected_evidence") or t_dict.get("expected_output") or "Evidence snippet demonstrating logic")
                title_str = str(t_dict.get("title") or t_dict.get("objective") or t_dict.get("task") or "Verify unresolved evidence gap")
                normalized_tasks.append({
                    "task_id": t_id,
                    "step_id": t_id,
                    "title": title_str,
                    "assigned_agent": agent,
                    "investigator": agent,
                    "objective": str(t_dict.get("objective") or title_str),
                    "reason": str(t_dict.get("reason") or feedback[:120]),
                    "instructions": str(t_dict.get("instructions") or "Inspect targeted code for missing evidence"),
                    "task": str(t_dict.get("instructions") or title_str),
                    "scope": list(t_dict.get("scope") or relevant_files[:2]),
                    "input": {
                        "problem": problem,
                        "original_problem": orig_problem,
                        "reviewer_feedback": feedback,
                        "unanswered_question": t_dict.get("unanswered_question"),
                    },
                    "expected_output": exp_ev,
                    "expected_evidence": exp_ev,
                    "completion_criteria": str(t_dict.get("completion_criteria") or "Defect evidence confirmed or refuted"),
                    "unanswered_question": t_dict.get("unanswered_question"),
                    "target_hypothesis_id": t_dict.get("target_hypothesis_id"),
                    "status": "pending",
                    "priority": "critical" if i == 0 else "high",
                })

            # Prevent duplicate tasks for already completed work
            completed_signatures = set()
            for ct in completed_tasks:
                if isinstance(ct, dict):
                    for k in ("title", "objective", "task"):
                        val = str(ct.get(k) or "").strip().lower()
                        if val:
                            completed_signatures.add(val)

            deduped_tasks = []
            for t in normalized_tasks:
                t_title = str(t.get("title") or "").strip().lower()
                t_obj = str(t.get("objective") or "").strip().lower()
                if (t_title and t_title in completed_signatures) or (t_obj and t_obj in completed_signatures):
                    continue
                deduped_tasks.append(t)

            return deduped_tasks
        except Exception as exc:
            logger.debug("[Replanner] LLM replanning fallback triggered: %s", exc)
            self.last_error = str(exc)
            return None

    def _replan_heuristically(
        self,
        problem: str,
        shared_state: Dict[str, Any],
        review_result: Dict[str, Any],
        gathered_context: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """Deterministic heuristic re-planner selecting targeted tasks for unresolved gaps."""
        orig_problem = shared_state.get("original_problem") or problem
        completed_tasks = shared_state.get("completed_tasks") or []
        failed_tasks = shared_state.get("failed_tasks") or []
        existing_plan = shared_state.get("investigation_plan") or []
        hypotheses = shared_state.get("aggregated_hypotheses") or shared_state.get("hypotheses") or []

        # Track previously inspected scopes by agent to avoid ineffective retries
        inspected_by_agent: Dict[str, set[str]] = {
            "code_investigator": set(),
            "dependency_impact_investigator": set(),
            "runtime_error_investigator": set(),
        }
        for t in completed_tasks:
            if isinstance(t, dict):
                ag = t.get("assigned_agent") or t.get("investigator")
                sc = t.get("scope") or t.get("target_files") or []
                if ag in inspected_by_agent:
                    for s in (sc if isinstance(sc, list) else [sc]):
                        inspected_by_agent[ag].add(str(s).lower())

        unanswered = list(review_result.get("unanswered_questions") or shared_state.get("unanswered_questions") or [])
        missing_ev = list(review_result.get("missing_evidence") or shared_state.get("missing_evidence") or [])
        feedback = str(review_result.get("feedback") or review_result.get("reasoning") or "")
        hyp_evals = review_result.get("hypothesis_evaluations") or []

        act_fb = review_result.get("actionable_feedback") or {}
        if not unanswered and act_fb.get("unanswered_questions"):
            unanswered = list(act_fb["unanswered_questions"])
        if not missing_ev and act_fb.get("required_next_steps"):
            missing_ev = list(act_fb["required_next_steps"])

        relevant_files = [
            f if isinstance(f, str) else f.get("path")
            for f in (gathered_context.get("relevant_files") or [])
            if f
        ]

        # Categorize relevant files by architectural layer
        middleware_files = [f for f in relevant_files if any(k in f.lower() for k in ("middleware", "auth", "interceptor", "filter", "token", "guard"))]
        client_files = [f for f in relevant_files if any(k in f.lower() for k in ("client", "api", "request", "http", "service", "frontend"))]
        model_files = [f for f in relevant_files if any(k in f.lower() for k in ("model", "query", "repo", "db", "dao", "entity"))]
        route_files = [f for f in relevant_files if any(k in f.lower() for k in ("route", "controller", "endpoint", "handler", "view"))]

        gaps_to_address = []
        seen_gap_tokens = []
        for raw_gap in unanswered + missing_ev:
            text = str(raw_gap).strip()
            if not text:
                continue
            words = set(re.findall(r"\w+", text.lower()))
            # Filter out boilerplate tokens
            meaningful_words = {w for w in words if len(w) > 3 and w not in ("what", "causes", "reported", "issue", "evidence", "direct", "causal", "defect")}
            is_dup = False
            for prev_words in seen_gap_tokens:
                if meaningful_words and prev_words:
                    overlap = len(meaningful_words & prev_words) / min(len(meaningful_words), len(prev_words))
                    if overlap >= 0.75:
                        is_dup = True
                        break
            if not is_dup:
                seen_gap_tokens.append(meaningful_words)
                gap_type = "question" if raw_gap in unanswered else "evidence_gap"
                gaps_to_address.append({"type": gap_type, "text": text})

        if not gaps_to_address and feedback:
            gaps_to_address.append({"type": "feedback", "text": feedback})

        new_tasks: List[Dict[str, Any]] = []
        base_idx = len(existing_plan)

        existing_signatures = {
            (
                t.get("assigned_agent") or t.get("investigator"),
                str(t.get("objective") or t.get("task") or "").lower()[:60],
                tuple(sorted(str(s).lower() for s in (t.get("scope") or t.get("target_files") or [])))
            )
            for t in existing_plan
            if isinstance(t, dict)
        }
        assigned_scopes = {
            tuple(sorted(str(s).lower() for s in (t.get("scope") or t.get("target_files") or [])))
            for t in existing_plan
            if isinstance(t, dict) and (t.get("scope") or t.get("target_files"))
        }

        for gap_idx, gap in enumerate(gaps_to_address[:3]):
            gap_text = str(gap["text"])
            gap_lower = gap_text.lower()

            target_hyp_id = None
            for he in hyp_evals:
                if isinstance(he, dict):
                    he_stmt = str(he.get("statement") or "").lower()
                    words = [w for w in he_stmt.split() if len(w) > 4]
                    if any(w in gap_lower for w in words):
                        target_hyp_id = he.get("hypothesis_id")
                        break
            if not target_hyp_id and hypotheses:
                for h in hypotheses:
                    if isinstance(h, dict) and h.get("status") != "contradicted":
                        target_hyp_id = h.get("hypothesis_id")
                        break

            if any(k in gap_lower for k in ("middleware", "caller", "cross-component", "dependency", "import", "call flow", "propagation", "interceptor", "router", "registration")):
                agent = "dependency_impact_investigator"
                layer_candidates = [f for f in (middleware_files + route_files) if f.lower() not in inspected_by_agent[agent]]
                all_uninspected_dep = [f for f in relevant_files if f.lower() not in inspected_by_agent[agent]]
                scope = layer_candidates[:2] or all_uninspected_dep[:2] or middleware_files[:2] or route_files[:2] or relevant_files[:2] or ["*"]
                expected_ev = "Caller hierarchy, middleware registration statement, and contract propagation across routes."
                criteria = "Cross-component call flow and route wiring confirmed or refuted."

            elif any(k in gap_lower for k in ("runtime", "probe", "stack", "exception", "traceback", "crash", "500", "error log")):
                agent = "runtime_error_investigator"
                all_uninspected_rt = [f for f in relevant_files if f.lower() not in inspected_by_agent[agent]]
                scope = all_uninspected_rt[:2] or relevant_files[:2] or ["*"]
                expected_ev = "Exception stack frames and error log entries localizing failure origin."
                criteria = "Runtime failure frames and error rejection verified."

            else:
                agent = "code_investigator"
                layer_candidates = [f for f in (model_files + route_files + client_files) if f.lower() not in inspected_by_agent[agent]]
                all_uninspected_code = [f for f in relevant_files if f.lower() not in inspected_by_agent[agent]]
                # Offset candidate selection by gap index to prevent twin tasks with identical scopes
                offset = gap_idx * 2
                offset_candidates = layer_candidates[offset:offset + 2] or all_uninspected_code[offset:offset + 2]
                scope = offset_candidates or layer_candidates[:2] or all_uninspected_code[:2] or model_files[:2] or route_files[:2] or relevant_files[:2] or ["*"]
                expected_ev = "Concrete source code statements demonstrating parameter order, comparison logic, or defect mechanism."
                criteria = "Source-level defect mechanism directly verified or refuted."

            # Ensure scope does not collide with already assigned scopes in this replan cycle
            scope_tuple = tuple(sorted(str(s).lower() for s in scope))
            if scope_tuple in assigned_scopes:
                alt_files = [f for f in relevant_files if f.lower() not in inspected_by_agent[agent] and f not in scope]
                if alt_files:
                    scope = alt_files[:2]
                    scope_tuple = tuple(sorted(str(s).lower() for s in scope))

            if gap["type"] == "question":
                clean_q = gap_text.strip("?. ")
                low_q = clean_q.lower()
                for prefix in ("verify whether ", "determine whether ", "check whether ", "investigate whether "):
                    if low_q.startswith(prefix):
                        clean_q = clean_q[len(prefix):].strip()
                        low_q = clean_q.lower()
                if low_q.startswith(("what ", "which ", "why ", "how ", "where ", "when ", "who ")):
                    objective = f"Investigate {clean_q[0].lower() + clean_q[1:]}"
                elif low_q.startswith(("does ", "is ", "are ", "can ", "could ", "should ", "will ", "whether ")):
                    objective = f"Verify whether {clean_q[0].lower() + clean_q[1:]}"
                else:
                    objective = f"Verify whether {clean_q}"
            else:
                clean_gap = gap_text.strip("?. ")
                for prefix in ("direct causal defect evidence addressing ", "causal defect evidence addressing ", "evidence addressing "):
                    if clean_gap.lower().startswith(prefix):
                        clean_gap = clean_gap[len(prefix):].strip("'\" ")
                objective = f"Investigate source code to resolve: {clean_gap[:100]}"

            instructions = f"Inspect targeted scope {scope} to answer: {gap_text}. Formulate causal evidence or verify whether defect occurs."
            task_id = f"TASK-REPLAN-{base_idx + len(new_tasks) + 1:03d}"

            sig = (agent, objective.lower()[:60], scope_tuple)
            if sig in existing_signatures:
                alt_files = [f for f in relevant_files if f.lower() not in inspected_by_agent[agent] and f not in scope]
                if alt_files:
                    scope = alt_files[:2]
                    scope_tuple = tuple(sorted(str(s).lower() for s in scope))
                instructions = f"Alternative inspection approach: trace callers and usage across {scope} to resolve: {gap_text}"

            task_dict = {
                "task_id": task_id,
                "step_id": task_id,
                "assigned_agent": agent,
                "investigator": agent,
                "objective": objective,
                "reason": f"Reviewer identified evidence gap: {gap_text[:120]}",
                "instructions": instructions,
                "task": instructions,
                "scope": scope,
                "input": {
                    "problem": problem,
                    "original_problem": orig_problem,
                    "reviewer_feedback": feedback,
                    "unanswered_question": gap_text,
                    "target_hypothesis_id": target_hyp_id,
                },
                "expected_output": expected_ev,
                "expected_evidence": expected_ev,
                "completion_criteria": criteria,
                "target_hypothesis_id": target_hyp_id,
                "unanswered_question": gap_text,
                "status": "pending",
                "priority": "critical" if len(new_tasks) == 0 else "high",
            }
            new_tasks.append(task_dict)
            existing_signatures.add((agent, objective.lower()[:60], scope_tuple))
            assigned_scopes.add(scope_tuple)
            inspected_by_agent[agent].update(str(s).lower() for s in scope)

        if not new_tasks:
            agent = "code_investigator" if "code_investigator" not in completed_tasks else "dependency_impact_investigator"
            task_id = f"TASK-REPLAN-{base_idx + 1:03d}"
            new_tasks.append({
                "task_id": task_id,
                "step_id": task_id,
                "assigned_agent": agent,
                "investigator": agent,
                "objective": f"Investigate remaining unanswered questions for '{orig_problem[:80]}'",
                "reason": "Reviewer found evidence inconclusive; targeted follow-up required.",
                "instructions": f"Inspect relevant files to answer open question: {unanswered[0] if unanswered else 'Verify causal root cause'}",
                "task": f"Inspect relevant files to answer open question: {unanswered[0] if unanswered else 'Verify causal root cause'}",
                "scope": relevant_files[:3] or ["*"],
                "input": {"problem": problem, "original_problem": orig_problem, "reviewer_feedback": feedback},
                "expected_output": "Direct source evidence addressing open questions.",
                "expected_evidence": "Direct source evidence addressing open questions.",
                "completion_criteria": "Open questions addressed with evidence.",
                "status": "pending",
                "priority": "normal",
            })

        return new_tasks
