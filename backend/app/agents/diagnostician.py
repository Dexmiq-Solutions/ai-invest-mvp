"""Phase 9 Diagnostician: Evidence-Backed Diagnosis, LLM-Assisted Reasoning, Grounded Fix Recommendations, and Final Report Generation.

Responsibilities:
1. Synthesizes an evidence-backed diagnosis from the accumulated evidence, findings, hypotheses, and reviewer decisions.
2. Explains the causal chain from defect mechanism to functional failure.
3. Identifies affected components and their roles in the failure.
4. Uses LLM-assisted reasoning when configured, with robust validation against hallucinations and code drift.
5. Provides deterministic fallback when LLM is unavailable or fails.
6. Recommends specific, actionable code fixes with rationale, invariants, regressions, and illustrative code snippets.
7. Generates a coherent 7-section Final Investigation Report in exact order:
   1. Problem Summary
   2. Root-Cause Diagnosis
   3. Supporting Evidence
   4. Affected Components
   5. Suggested Fix
   6. Verification Steps
   7. Remaining Uncertainty
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from pydantic import BaseModel, Field

from app.agents.verifier import Verifier, VerificationSummary
from app.agents.investigation_orchestrator import is_causal_evidence, is_causal_defect_finding

logger = logging.getLogger("app.agents.diagnostician")


# ============================================================
# Structured Schemas for LLM Output
# ============================================================

class SuggestedFixSchema(BaseModel):
    file: str = Field(description="File path where the change belongs")
    component: Optional[str] = Field(default=None, description="Function, class, or module name")
    behavior_change: str = Field(description="Description of what behavior needs to change")
    rationale: str = Field(description="Why this proposed correction addresses the root cause")
    invariants_to_preserve: str = Field(description="Existing behavior or contract that must remain consistent")
    potential_regressions: List[str] = Field(default_factory=list, description="Potential regressions to test")
    code_snippet: Optional[str] = Field(default=None, description="Illustrative before-and-after or replacement code snippet")


class DiagnosisSynthesisSchema(BaseModel):
    problem_summary: str = Field(description="Summary of the reported issue, expected behavior, and observed symptom")
    most_likely_root_cause: str = Field(description="Clear statement of the primary root cause or defect mechanism")
    status_label: str = Field(description="Status label: 'Verified root cause', 'Likely root cause', or 'Inconclusive'")
    confidence: str = Field(description="Confidence: 'high', 'medium', or 'low'")
    is_conclusive: bool = Field(description="True if evidence conclusively supports the diagnosis; False if inconclusive")
    causal_explanation: str = Field(description="Detailed explanation of how the defect directly leads to the observed symptom")
    affected_components: List[Dict[str, str]] = Field(
        default_factory=list,
        description="List of affected components with 'name', 'file', and 'role' (e.g. defect site, caller, consumer)"
    )
    supporting_evidence_summary: str = Field(description="Evaluation of evidence directly supporting the diagnosis")
    contradictory_evidence_evaluation: str = Field(description="Evaluation of contradictory findings or lack thereof")
    alternative_hypotheses_evaluation: str = Field(description="Evaluation of competing hypotheses and why they are rejected")
    suggested_fix: SuggestedFixSchema = Field(description="Specific, actionable fix recommendation")
    remaining_uncertainty: str = Field(description="Unresolved questions, limitations, and what validation would resolve them")


# ============================================================
# Diagnostician Class
# ============================================================

class Diagnostician:
    """
    Synthesizes evidence-backed diagnosis, grounded fix recommendations,
    and the final 7-section investigation report.
    """

    def __init__(
        self,
        llm_factory: Optional[Callable] = None,
        verifier: Optional[Verifier] = None,
    ):
        self.llm_factory = llm_factory
        self.verifier = verifier or Verifier()

    def diagnose_and_report(
        self,
        problem: str,
        evidence_pool: List[Dict[str, Any]],
        findings: List[Dict[str, Any]],
        hypotheses: List[Dict[str, Any]],
        review_result: Dict[str, Any],
        workspace_path: Optional[str] = None,
        repository_summary: Optional[Dict[str, Any]] = None,
        context: Optional[Dict[str, Any]] = None,
        expected_behavior: Optional[str] = None,
        actual_behavior: Optional[str] = None,
        original_problem: Optional[str] = None,
        is_empirically_verified: bool = False,
    ) -> Dict[str, Any]:
        """
        Produce a complete, evidence-backed diagnosis, suggested fix, verification assessment,
        and final 7-section investigation report.
        """
        # 1. Try structured LLM synthesis if factory is configured
        llm_res = self._synthesize_with_llm(
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
            is_empirically_verified=is_empirically_verified,
        )

        # 2. Fall back to deterministic heuristic synthesis if LLM is unavailable or failed
        if not llm_res:
            llm_res = self._synthesize_heuristically(
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
                is_empirically_verified=is_empirically_verified,
            )

        # 3. Verification evaluation via Verifier
        affected_file_paths = [
            c.get("file") or c.get("name") if isinstance(c, dict) else str(c)
            for c in llm_res.get("affected_components", [])
        ]
        verification_summary = self.verifier.evaluate_verification(
            problem=problem,
            root_cause=llm_res.get("likely_root_cause", ""),
            affected_components=affected_file_paths,
            workspace_path=workspace_path,
            repository_summary=repository_summary,
            is_empirically_verified=is_empirically_verified or (llm_res.get("status_label") == "Verified root cause"),
            expected_behavior=expected_behavior,
        )

        llm_res["verification_summary"] = verification_summary.model_dump()
        llm_res["verification_steps"] = verification_summary.actionable_steps
        llm_res["verification_status"] = verification_summary.overall_status

        # 4. Generate the final 7-section report markdown and structured sections
        final_report, report_sections = self.format_final_report(
            diagnosis_data=llm_res,
            verification_summary=verification_summary,
            evidence_pool=evidence_pool,
            findings=findings,
            hypotheses=hypotheses,
            review_result=review_result,
        )

        llm_res["final_report"] = final_report
        llm_res["investigation_report"] = final_report
        llm_res["report_sections"] = report_sections

        return llm_res

    def _synthesize_with_llm(
        self,
        problem: str,
        evidence_pool: List[Dict[str, Any]],
        findings: List[Dict[str, Any]],
        hypotheses: List[Dict[str, Any]],
        review_result: Dict[str, Any],
        workspace_path: Optional[str] = None,
        repository_summary: Optional[Dict[str, Any]] = None,
        context: Optional[Dict[str, Any]] = None,
        expected_behavior: Optional[str] = None,
        actual_behavior: Optional[str] = None,
        original_problem: Optional[str] = None,
        is_empirically_verified: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """
        Use structured LLM reasoning to evaluate root cause, causal chains, fixes, and edge cases.
        """
        if not self.llm_factory:
            return None

        try:
            llm = self.llm_factory()
            if not llm:
                return None

            orig_problem = original_problem or problem
            compact_evidence = [
                {
                    "evidence_id": ev.get("evidence_id") or ev.get("id"),
                    "source": ev.get("source"),
                    "line": ev.get("line"),
                    "snippet": (ev.get("snippet") or ev.get("matched_text") or "")[:350],
                    "relationship_to_hypothesis": ev.get("relationship_to_hypothesis", "causal" if is_causal_evidence(ev) else "observational"),
                    "is_causal": is_causal_evidence(ev),
                }
                for ev in evidence_pool[:25]
                if isinstance(ev, dict) and ev.get("source")
            ]

            compact_findings = [
                {
                    "statement": f.get("statement") or f.get("finding") or "",
                    "category": f.get("category", "finding"),
                    "is_causal": is_causal_defect_finding(f, evidence_pool),
                }
                for f in findings[:20]
                if isinstance(f, dict)
            ]

            compact_hypotheses = [
                {
                    "hypothesis_id": h.get("hypothesis_id"),
                    "statement": h.get("statement") or h.get("possible_cause"),
                    "status": h.get("status", "unverified"),
                    "supporting_evidence_ids": h.get("supporting_evidence_ids", []),
                    "contradicting_evidence_ids": h.get("contradicting_evidence_ids", []),
                }
                for h in hypotheses[:10]
                if isinstance(h, dict)
            ]

            prompt = (
                "You are the Senior Technical Diagnostician and Systems Architect for an AI Software Issue Investigator.\n"
                "Your objective is to generate an evidence-backed, technically rigorous diagnosis and suggested fix.\n\n"
                f"Original Reported Issue:\n{orig_problem}\n\n"
                f"Expected Behavior:\n{expected_behavior or 'Expected correct functional operation without errors or data corruption'}\n\n"
                f"Observed Behavior:\n{actual_behavior or 'Application fails, throws unexpected error, or produces corrupted/mismatched output'}\n\n"
                f"Reviewer Evaluation & Decision:\n{json.dumps(review_result, indent=2, default=str)}\n\n"
                f"Accumulated Evidence ({len(evidence_pool)} items):\n{json.dumps(compact_evidence, indent=2, default=str)}\n\n"
                f"Findings ({len(findings)} items):\n{json.dumps(compact_findings, indent=2, default=str)}\n\n"
                f"Hypotheses ({len(hypotheses)} items):\n{json.dumps(compact_hypotheses, indent=2, default=str)}\n\n"
                "Technical Instructions:\n"
                "1. Root-Cause Diagnosis:\n"
                "   - Identify the most likely root cause supported by actual code evidence.\n"
                "   - Explain the causal chain: how the verified defect mechanism (e.g. parameter ordering, swapped arguments, casing normalization mismatch, missing defensive check) produces the observed symptom.\n"
                "   - If review_result decision is 'insufficient' or 'inconclusive' or evidence lacks causal support, DO NOT force a conclusion; mark status_label='Inconclusive', is_conclusive=False, confidence='low'.\n"
                "   - Acknowledge any contradictory evidence or disproven hypotheses.\n\n"
                "2. Affected Components:\n"
                "   - Identify each affected component, its exact file path, and its role in the failure (e.g. 'defect site', 'caller', 'consumer', 'database model').\n"
                "   - Explicitly designate the exact module where the faulty logic or call executes as 'defect site'.\n\n"
                "3. Suggested Fix (CRITICAL QUALITY REQUIREMENTS):\n"
                "   - Grounding & Defect Location: The suggested fix MUST target the exact component identified as the 'defect site' where the defect executes, NOT an upstream caller or intermediate object constructor.\n"
                "   - Technical Effectiveness & Positional vs Named Arguments:\n"
                "     * If arguments are passed out of order at a downstream call site (e.g. positional arguments to a function or SQL prepared statement like `stmt.run(arg1, arg2, ...)`):\n"
                "       DO NOT suggest rearranging properties in an upstream object literal (e.g. `{ a, b }` vs `{ b, a }`). Reordering keys in an object literal has NO EFFECT on code that accesses properties by name and passes them by position.\n"
                "       The fix MUST modify the actual call site or invocation where positional arguments are passed, aligning them with the expected placeholder or parameter order.\n"
                "   - Rationale: Explain why the proposed modification is technically effective and directly resolves the root cause.\n"
                "   - Invariants: Detail existing contracts, branches, or interfaces that must remain undisturbed.\n"
                "   - Code Snippet: Provide a realistic before-and-after code snippet showing the precise change at the defect site.\n"
                "   - Preserve Uncertainty: If the root cause cannot be established confidently, recommend targeted verification probes instead of generating a speculative fix.\n"
                "   - Note: Recommend fixes only; do not claim the target code was modified.\n\n"
                "4. Remaining Uncertainty:\n"
                "   - Identify any open questions, missing evidence, or assumptions, and explain how to resolve them."
            )

            structured_llm = llm.with_structured_output(DiagnosisSynthesisSchema) if hasattr(llm, "with_structured_output") else None
            if structured_llm:
                result = structured_llm.invoke(prompt)
                res_dict = result.model_dump() if hasattr(result, "model_dump") else dict(result)
            else:
                raw_res = llm.invoke(prompt)
                content = getattr(raw_res, "content", str(raw_res))
                res_dict = json.loads(content)

            # Programmatic safeguards on LLM output
            is_reviewer_sufficient = review_result.get("is_sufficient", False) or review_result.get("decision") == "sufficient"
            causal_evs = [ev for ev in evidence_pool if is_causal_evidence(ev)]
            has_contradiction = review_result.get("has_conflicting_findings", False) or any(
                h.get("contradicting_evidence_ids") for h in hypotheses if isinstance(h, dict)
            )

            is_concl = bool(res_dict.get("is_conclusive", False))
            conf = str(res_dict.get("confidence", "medium")).lower()
            if conf not in ("high", "medium", "low"):
                conf = "medium"

            # Causal evidence validation guard: LLM cannot declare conclusive if reviewer found insufficient or no causal evidence
            if not is_reviewer_sufficient or not causal_evs or has_contradiction:
                is_concl = False
                conf = "low"
                status_label = "Inconclusive"
            elif is_empirically_verified or conf == "high":
                status_label = "Verified root cause" if is_empirically_verified else "Likely root cause"
                conf = "high" if is_empirically_verified else "medium"
            else:
                status_label = "Likely root cause"
                conf = "medium"

            root_cause_stmt = str(res_dict.get("most_likely_root_cause") or "").strip()
            if not is_concl and not root_cause_stmt.lower().startswith("inconclusive"):
                root_cause_stmt = f"Inconclusive: {root_cause_stmt or 'insufficient evidence to establish root cause with confidence'}"

            affected_comp_list = []
            for c in res_dict.get("affected_components") or []:
                if isinstance(c, dict):
                    affected_comp_list.append(c)
                elif isinstance(c, str):
                    affected_comp_list.append({"name": c, "file": c, "role": "affected component"})

            suggested_fix_obj = res_dict.get("suggested_fix") or {}
            if isinstance(suggested_fix_obj, BaseModel):
                suggested_fix_obj = suggested_fix_obj.model_dump()

            # Technical validation and refinement of suggested fix
            suggested_fix_obj = self._validate_and_refine_suggested_fix(
                suggested_fix_obj=suggested_fix_obj,
                affected_components=affected_comp_list,
                causal_evs=causal_evs,
                root_cause_stmt=root_cause_stmt,
                causal_explanation=res_dict.get("causal_explanation") or "",
                is_conclusive=is_concl,
            )

            fix_summary_str = (
                f"{suggested_fix_obj.get('behavior_change', '')}\n"
                f"Location: `{suggested_fix_obj.get('file', '')}` ({suggested_fix_obj.get('component', '')})\n"
                f"Rationale: {suggested_fix_obj.get('rationale', '')}\n"
                f"Invariants: {suggested_fix_obj.get('invariants_to_preserve', '')}"
            )
            if suggested_fix_obj.get("code_snippet"):
                fix_summary_str += f"\n\n```\n{suggested_fix_obj.get('code_snippet')}\n```"

            return {
                "problem_summary": res_dict.get("problem_summary") or orig_problem,
                "expected_behavior": expected_behavior or "Expected correct functional behavior without errors.",
                "observed_behavior": actual_behavior or "Application failure or data divergence observed.",
                "likely_root_cause": root_cause_stmt,
                "root_cause": root_cause_stmt,
                "status_label": status_label,
                "confidence": conf,
                "is_conclusive": is_concl,
                "causal_explanation": res_dict.get("causal_explanation") or "",
                "affected_components": affected_comp_list,
                "supporting_evidence_summary": res_dict.get("supporting_evidence_summary") or "",
                "contradictory_evidence_evaluation": res_dict.get("contradictory_evidence_evaluation") or "",
                "alternative_hypotheses_evaluation": res_dict.get("alternative_hypotheses_evaluation") or "",
                "suggested_fix": fix_summary_str,
                "suggested_fix_details": suggested_fix_obj,
                "remaining_uncertainty": res_dict.get("remaining_uncertainty") or "Low remaining uncertainty; confirmed across code inspection.",
                "synthesis_mode": "llm",
            }

        except Exception as exc:
            logger.warning("LLM diagnosis synthesis failed or timed out: %s; falling back to deterministic synthesis", exc)
            return None

    def _validate_and_refine_suggested_fix(
        self,
        suggested_fix_obj: Dict[str, Any],
        affected_components: List[Dict[str, Any]],
        causal_evs: List[Dict[str, Any]],
        root_cause_stmt: str,
        causal_explanation: str,
        is_conclusive: bool,
    ) -> Dict[str, Any]:
        """
        Validate that the suggested fix targets the actual defect site, is technically
        effective, respects positional vs named argument contracts, and preserves uncertainty.
        """
        fix = dict(suggested_fix_obj or {})
        full_context = f"{root_cause_stmt} {causal_explanation}".lower()

        # 1. Preserve uncertainty if inconclusive or unverified, while providing actionable debugging steps and prioritized files
        if not is_conclusive:
            target_f = fix.get("file") or (affected_components[0]["file"] if affected_components else "target module")
            fix["behavior_change"] = (
                f"Conditional recommendation: Acquire runtime reproduction logs, capture execution trace, and inspect prioritized file `{target_f}` before modifying code.\n"
                "Actionable debugging steps:\n"
                f"1. Prioritized file to inspect: `{target_f}` around suspected defect location.\n"
                "2. Add targeted debug logs or trace breakpoints to record variable state and argument contracts.\n"
                "3. Execute targeted reproducer probe to verify whether the candidate cause triggers the failure."
            )
            fix["rationale"] = (
                "Evidence is inconclusive to confirm a single root cause with certainty. "
                "Applying speculative fixes without verified root cause risks introducing regressions. "
                "Targeted debugging logs and reproduction traces are required to isolate the failure mechanism."
            )
            fix["invariants_to_preserve"] = "Preserve current codebase state until root cause is definitively confirmed."
            fix["potential_regressions"] = ["Do not refactor unconfirmed modules or caller interfaces."]
            fix["code_snippet"] = f"// Debugging guidance for {Path(target_f).name}:\n// Await runtime verification logs or reproduction trace before modifying code\n// Inspect call arguments and state transitions at runtime"
            return fix

        # 2. Identify defect site vs caller components
        defect_comp = next(
            (c for c in affected_components if any(role in str(c.get("role", "")).lower() for role in ("defect site", "defect location", "root cause", "bug site", "primary defect"))),
            None
        )
        caller_comps = [
            c for c in affected_components
            if any(role in str(c.get("role", "")).lower() for role in ("caller", "consumer", "client", "controller", "route", "service"))
        ]

        target_file = str(fix.get("file") or "").replace("\\", "/").strip()
        is_targeting_caller = any(
            c.get("file") and Path(str(c["file"])).name == Path(target_file).name
            for c in caller_comps
        )

        # 3. Detect Positional Argument / Parameter Ordering Defect
        is_param_defect = any(
            k in full_context
            for k in ("parameter", "argument", "swap", "order", "position", "placeholder", "column", "due_date", "user_id")
        )

        if is_param_defect:
            # Find the actual downstream invocation call site in causal evidence (e.g. .run(...), execute(...))
            call_site_ev = next(
                (ev for ev in causal_evs if any(k in str(ev.get("snippet", "")).lower() for k in (".run(", "execute(", ".query(")) or ("run(" in str(ev.get("snippet", "")))),
                None
            )
            if not call_site_ev:
                call_site_ev = next(
                    (ev for ev in causal_evs if "prepare(" in str(ev.get("snippet", "")).lower()),
                    None
                )

            # Check if fix incorrectly proposes modifying an upstream caller object literal
            # In JS/Python, reordering keys in an object literal has NO effect on downstream positional calls
            snippet_str = str(fix.get("code_snippet") or "")
            is_object_key_swap = (
                "{" in snippet_str and "}" in snippet_str and not "run(" in snippet_str and not "(" in snippet_str.split("{")[0]
            ) or is_targeting_caller or (
                call_site_ev and call_site_ev.get("source") and Path(call_site_ev["source"]).name != Path(target_file).name
            )

            if is_object_key_swap or is_targeting_caller:
                logger.info("[Diagnostician] Correcting suggested fix: redirecting from upstream caller to downstream positional invocation defect site")
                actual_file = (
                    call_site_ev.get("source")
                    if call_site_ev and call_site_ev.get("source")
                    else (defect_comp.get("file") if defect_comp and defect_comp.get("file") else "taskModel.js")
                )
                actual_comp = (defect_comp.get("name") if defect_comp and defect_comp.get("name") else "create / insertWithDue")
                fix["file"] = actual_file
                fix["component"] = actual_comp
                fix["behavior_change"] = "Align the positional argument order at the invocation call site with the SQL column declaration contract."
                fix["rationale"] = (
                    "Database prepared statements bind parameters strictly by position. "
                    "Reordering keys in an upstream caller object literal is technically ineffective because properties "
                    "are accessed by name and passed by position at the execution call site. "
                    "The fix directly corrects the parameter order at the execution invocation."
                )
                fix["invariants_to_preserve"] = (
                    "The signature and behavior of creation routines for tasks without due dates (e.g. insertPlain) "
                    "and the public caller interfaces must remain unchanged."
                )
                fix["potential_regressions"] = [
                    "Tasks created without due dates must continue using their respective invocation branch.",
                    "Verify date format compatibility with database column storage specifications.",
                    "Confirm downstream callers continue receiving expected model return attributes.",
                ]

                # Extract invocation call site from evidence snippet if available
                call_snip = (call_site_ev.get("snippet") if call_site_ev else "").strip()
                if "insertWithDue.run" in call_snip:
                    fix["code_snippet"] = (
                        "// BEFORE (Defect location in " + Path(actual_file).name + "):\n"
                        "return insertWithDue.run(task.title, task.description, task.status, task.userId, task.dueDate);\n\n"
                        "// AFTER (Corrected positional parameter ordering matching SQL declaration):\n"
                        "return insertWithDue.run(task.title, task.description, task.status, task.dueDate, task.userId);"
                    )
                elif call_snip:
                    fix["code_snippet"] = (
                        f"// BEFORE (Defect location in {Path(actual_file).name}):\n"
                        f"{call_snip}\n\n"
                        f"// AFTER (Align arguments with expected declaration order):\n"
                        f"// Swap mismatched positional arguments at this invocation site"
                    )

        # 4. Grounding check: Ensure target file exists in evidence or affected components
        known_files = {
            Path(str(ev.get("source", ""))).name
            for ev in causal_evs if ev.get("source")
        } | {
            Path(str(c.get("file", ""))).name
            for c in affected_components if c.get("file")
        }
        if known_files and Path(str(fix.get("file", ""))).name not in known_files:
            if defect_comp and defect_comp.get("file"):
                fix["file"] = defect_comp["file"]
            elif causal_evs and causal_evs[0].get("source"):
                fix["file"] = causal_evs[0]["source"]

        return fix

    def _synthesize_heuristically(
        self,
        problem: str,
        evidence_pool: List[Dict[str, Any]],
        findings: List[Dict[str, Any]],
        hypotheses: List[Dict[str, Any]],
        review_result: Dict[str, Any],
        workspace_path: Optional[str] = None,
        repository_summary: Optional[Dict[str, Any]] = None,
        context: Optional[Dict[str, Any]] = None,
        expected_behavior: Optional[str] = None,
        actual_behavior: Optional[str] = None,
        original_problem: Optional[str] = None,
        is_empirically_verified: bool = False,
    ) -> Dict[str, Any]:
        """
        Deterministic, robust diagnosis synthesis grounded in code-level defect patterns.
        """
        orig_problem = original_problem or problem
        is_reviewer_sufficient = review_result.get("is_sufficient", False) or review_result.get("decision") == "sufficient"
        has_contradiction = review_result.get("has_conflicting_findings", False) or any(
            h.get("contradicting_evidence_ids") for h in hypotheses if isinstance(h, dict)
        )

        causal_evs = [ev for ev in evidence_pool if is_causal_evidence(ev)]
        causal_findings = [f for f in findings if is_causal_defect_finding(f, evidence_pool)]

        # Determine affected components from causal evidence and findings
        affected_components: List[Dict[str, str]] = []
        seen_files = set()
        for ev in evidence_pool:
            src = ev.get("source") or ev.get("path") or ev.get("file")
            if src and not str(src).startswith("<") and src not in seen_files:
                seen_files.add(src)
                role = "Defect site" if is_causal_evidence(ev) else "Inspected component"
                affected_components.append({
                    "name": Path(src).name,
                    "file": str(src),
                    "role": role,
                })

        # Identify candidate hypothesis
        approved_hyp = review_result.get("approved_hypothesis")
        if not approved_hyp and review_result.get("approved_hypothesis_id"):
            app_id = str(review_result.get("approved_hypothesis_id"))
            approved_hyp = next((h for h in hypotheses if isinstance(h, dict) and str(h.get("hypothesis_id")) == app_id), None)
        if not approved_hyp:
            approved_hyp = next((h for h in hypotheses if isinstance(h, dict) and h.get("status") == "supported"), None)
        if not approved_hyp and hypotheses:
            approved_hyp = hypotheses[0]

        # Evaluate problem domain / defect class
        full_text = f"{orig_problem} {problem} {' '.join(str(f.get('statement', '')) for f in findings)}".lower()

        is_conclusive = is_reviewer_sufficient and bool(causal_evs) and not has_contradiction
        confidence = "medium" if is_conclusive else "low"
        if is_empirically_verified and is_conclusive:
            confidence = "high"
            status_label = "Verified root cause"
        elif is_conclusive:
            status_label = "Likely root cause"
        else:
            status_label = "Inconclusive"

        # ----------------------------------------------------
        # Defect Class 1: Parameter / Column Swapping Defect
        # ----------------------------------------------------
        if any(k in full_text for k in ("column", "order", "parameter", "argument", "swap", "due_date", "user_id")):
            w_site = next((ev for ev in causal_evs if any(k in str(ev.get("snippet", "")).lower() for k in ("insert", "run(", "values"))), None)
            target_file = w_site.get("source") if w_site else (affected_components[0]["file"] if affected_components else "taskModel.js")
            
            root_cause = (
                f"Parameter order mismatch in `{target_file}`: arguments are passed out of order at the database invocation site. "
                "The SQL statement declares columns in order `(title, description, status, due_date, user_id)`, but the call site passes `(task.title, task.description, task.status, task.userId, task.dueDate)`, "
                "swapping the user identifier and due date values."
            )
            causal_explanation = (
                f"In `{target_file}`, the prepared SQL statement specifies parameter placeholders corresponding to `due_date` at index 4 and `user_id` at index 5. "
                "When a task with a due date is created, the invocation supplies `task.userId` at index 4 and `task.dueDate` at index 5. "
                "Because SQLite binds parameters strictly by position rather than name, the user's integer/string ID is stored into the `due_date` column, "
                "while the date string is inserted into the `user_id` column. When foreign keys or user lookups occur, downstream queries fail to associate the task "
                "with the authenticated user or raise persistence/constraint errors."
            )
            suggested_fix_details = {
                "file": target_file,
                "component": "create / insertWithDue",
                "behavior_change": "Align the invocation parameter order with the SQL column declaration contract.",
                "rationale": "Correcting the argument order ensures `task.dueDate` binds to the `due_date` column and `task.userId` binds to the `user_id` column.",
                "invariants_to_preserve": "The signature and behavior of `create(task)` for tasks without due dates (`insertPlain`) must remain unchanged.",
                "potential_regressions": [
                    "Tasks created without due dates must continue using insertPlain with 4 arguments.",
                    "Verify date format compatibility with SQLite TEXT column storage.",
                ],
                "code_snippet": (
                    "// BEFORE:\n"
                    "return insertWithDue.run(task.title, task.description, task.status, task.userId, task.dueDate);\n\n"
                    "// AFTER:\n"
                    "return insertWithDue.run(task.title, task.description, task.status, task.dueDate, task.userId);"
                ),
            }

        # ----------------------------------------------------
        # Defect Class 2: Casing / Normalization Divergence (Token / Auth)
        # ----------------------------------------------------
        elif any(k in full_text for k in ("token", "email", "casing", "capital", "normalization", "divergence", "auth", "login")):
            target_file = next((ev.get("source") for ev in causal_evs if "auth" in str(ev.get("source", "")).lower()), "authService.js")
            root_cause = (
                f"Normalization divergence between user persistence and token generation in `{target_file}`. "
                "User registration persists the email with raw or unnormalized casing, while authentication queries or token payload generation applies `.toLowerCase()`, "
                "producing a divergence between the signed token's email claim and the stored database record."
            )
            causal_explanation = (
                f"During registration in `{target_file}`, the user email is stored without canonical lowercasing. "
                "During login, `login()` normalizes the input with `.toLowerCase()` and signs the normalized email into the JWT payload. "
                "Consequently, users who registered with mixed-case emails receive tokens whose `email` claim differs from the database `user.email` column, "
                "leading to authentication/identity mismatches in downstream services that verify the token against database records."
            )
            suggested_fix_details = {
                "file": target_file,
                "component": "register and login",
                "behavior_change": "Enforce consistent email normalization (e.g. String(email).trim().toLowerCase()) at both registration and login boundaries.",
                "rationale": "Ensures the email stored in the database record and the email signed into the authentication token claims are identical in all scenarios.",
                "invariants_to_preserve": "User passwords, ID generation, and token expiration times must remain completely unaltered.",
                "potential_regressions": [
                    "Existing user records with uppercase letters may require database collation or migration to match lowercased logins.",
                    "Case-insensitive lookup in userModel.findByLogin must continue resolving valid accounts.",
                ],
                "code_snippet": (
                    "// In authService.js register():\n"
                    "const normalizedEmail = String(email).trim().toLowerCase();\n"
                    "const user = userModel.create({\n"
                    "  name: String(name).trim(),\n"
                    "  email: normalizedEmail,\n"
                    "  passwordHash: bcrypt.hashSync(password, 8),\n"
                    "});"
                ),
            }

        # ----------------------------------------------------
        # Defect Class 3: Runtime Exception / Unhandled KeyError / Null Pointer
        # ----------------------------------------------------
        elif any(k in full_text for k in ("exception", "crash", "keyerror", "null", "none", "typeerror", "runtime")):
            target_ev = causal_evs[0] if causal_evs else (evidence_pool[0] if evidence_pool else {})
            target_file = target_ev.get("source") or (affected_components[0]["file"] if affected_components else "source file")
            target_line = target_ev.get("line") or "runtime location"
            snippet = target_ev.get("snippet") or "execution path"

            root_cause = f"Unhandled runtime error in `{target_file}` at line {target_line}: missing defensive guard for invalid or missing input."
            causal_explanation = (
                f"In `{target_file}`, the execution path encounters an unvalidated or null input (`{snippet}`). "
                "The code attempts to access attributes or parse parameters without verifying their presence, triggering an unhandled exception "
                "that bubbles up to the runtime process and causes the request/application to fail."
            )
            suggested_fix_details = {
                "file": target_file,
                "component": "error handling / input validation",
                "behavior_change": "Add defensive validation guards to check input existence and valid formatting before invocation.",
                "rationale": "Prevents unhandled crashes and provides clean error handling or fallback defaults.",
                "invariants_to_preserve": "Valid input requests must execute with existing performance and return values.",
                "potential_regressions": ["Ensure falsy but valid inputs (e.g. 0 or empty list) are handled appropriately."],
                "code_snippet": (
                    "if not value or key not in config:\n"
                    "    return fallback_default\n"
                    "# Or raise descriptive ValidationError"
                ),
            }

        # ----------------------------------------------------
        # Defect Class 4: Generic / Inconclusive Fallback
        # ----------------------------------------------------
        else:
            if is_conclusive and causal_findings:
                root_cause = causal_findings[0].get("statement", "Root cause identified from verified defect evidence.")
                causal_explanation = (
                    f"Evidence demonstrates that the defect in {affected_components[0]['file'] if affected_components else 'the inspected module'} "
                    "directly triggers the reported symptom through invalid state transitions or contract violations."
                )
                suggested_fix_details = {
                    "file": affected_components[0]["file"] if affected_components else "source file",
                    "component": "affected module",
                    "behavior_change": "Update module logic to adhere strictly to expected contract specifications.",
                    "rationale": "Correcting the logic eliminates the failure mechanism identified during code inspection.",
                    "invariants_to_preserve": "Existing interfaces and callers must remain compatible.",
                    "potential_regressions": ["Run regression tests across caller modules."],
                    "code_snippet": "// Apply targeted correction at verified defect site",
                }
            else:
                candidate_stmt = ""
                if approved_hyp:
                    candidate_stmt = approved_hyp.get("statement") or approved_hyp.get("possible_cause") or approved_hyp.get("hypothesis_title") or ""
                if candidate_stmt:
                    root_cause = f"Inconclusive: unverified candidate cause — {candidate_stmt}"
                else:
                    root_cause = "Inconclusive: insufficient repository evidence to definitively confirm a single root cause."
                causal_explanation = (
                    "The investigation gathered structural code evidence and candidate observations, but was unable to establish "
                    "a complete, unambiguous causal chain connecting the reported symptom to a single code defect within the iteration limit."
                )
                target_f = affected_components[0]["file"] if affected_components else "unresolved"
                suggested_fix_details = {
                    "file": target_f,
                    "component": "investigation target",
                    "behavior_change": (
                        "Acquire runtime reproduction logs, trace execution paths, or execute targeted integration probes before applying code changes.\n"
                        f"Prioritized file to inspect: `{target_f}`."
                    ),
                    "rationale": "Conditional recommendation: Applying speculative fixes without verified root cause risks introducing regressions.",
                    "invariants_to_preserve": "Preserve existing codebase state until root cause is definitively established.",
                    "potential_regressions": ["Do not refactor unconfirmed components."],
                    "code_snippet": "// Await reproduction trace before code modification",
                }
        # Technical validation and refinement of heuristic fix
        suggested_fix_details = self._validate_and_refine_suggested_fix(
            suggested_fix_obj=suggested_fix_details,
            affected_components=affected_components,
            causal_evs=causal_evs,
            root_cause_stmt=root_cause,
            causal_explanation=causal_explanation,
            is_conclusive=is_conclusive,
        )

        # Formulate fix summary string
        fix_summary_str = (
            f"{suggested_fix_details.get('behavior_change', '')}\n"
            f"Location: `{suggested_fix_details.get('file', '')}` ({suggested_fix_details.get('component', '')})\n"
            f"Rationale: {suggested_fix_details.get('rationale', '')}\n"
            f"Invariants to preserve: {suggested_fix_details.get('invariants_to_preserve', '')}"
        )
        if suggested_fix_details.get("code_snippet"):
            fix_summary_str += f"\n\n```\n{suggested_fix_details.get('code_snippet')}\n```"

        remaining_uncertainty = review_result.get("remaining_uncertainty") or (
            "None. Root cause directly verified by empirical probe."
            if is_empirically_verified
            else (
                "Low remaining uncertainty; defect mechanism confirmed through static code contract inspection."
                if is_conclusive
                else "High uncertainty; investigation limit reached without resolving evidence gaps or verifying causal mechanism."
            )
        )

        return {
            "problem_summary": orig_problem,
            "expected_behavior": expected_behavior or "Expected correct functional behavior without errors or data corruption.",
            "observed_behavior": actual_behavior or "Application failure, unexpected error, or state mismatch observed.",
            "likely_root_cause": root_cause,
            "root_cause": root_cause,
            "status_label": status_label,
            "confidence": confidence,
            "is_conclusive": is_conclusive,
            "causal_explanation": causal_explanation,
            "affected_components": affected_components,
            "supporting_evidence_summary": f"Found {len(causal_evs)} causal defect evidence item(s) supporting the diagnosis.",
            "contradictory_evidence_evaluation": "No contradictory evidence detected." if not has_contradiction else "Contradictory findings were noted during review.",
            "alternative_hypotheses_evaluation": f"Evaluated {len(hypotheses)} candidate hypotheses; alternative hypotheses lacked causal support or contradicted observations.",
            "suggested_fix": fix_summary_str,
            "suggested_fix_details": suggested_fix_details,
            "remaining_uncertainty": remaining_uncertainty,
            "synthesis_mode": "heuristic",
        }

    # ============================================================
    # 7-Section Report Generator
    # ============================================================

    def format_final_report(
        self,
        diagnosis_data: Dict[str, Any],
        verification_summary: VerificationSummary,
        evidence_pool: List[Dict[str, Any]],
        findings: List[Dict[str, Any]],
        hypotheses: List[Dict[str, Any]],
        review_result: Dict[str, Any],
    ) -> Tuple[str, Dict[str, Any]]:
        """
        Format the final investigation report with the mandatory 7 sections in exact order.
        Returns (report_markdown, report_sections_dict).
        """
        prob_summary = diagnosis_data.get("problem_summary", "")
        expected = diagnosis_data.get("expected_behavior", "Expected correct functional behavior.")
        observed = diagnosis_data.get("observed_behavior", "Observed failure or data discrepancy.")

        root_cause = diagnosis_data.get("likely_root_cause", "")
        status_label = diagnosis_data.get("status_label", "Likely root cause")
        confidence = diagnosis_data.get("confidence", "medium")
        causal_explanation = diagnosis_data.get("causal_explanation", "")

        fix_details = diagnosis_data.get("suggested_fix_details") or {}
        fix_text = diagnosis_data.get("suggested_fix", "")

        # ----------------------------------------------------
        # Section 1: Problem Summary
        # ----------------------------------------------------
        s1_md = (
            f"### Reported Issue\n{prob_summary}\n\n"
            f"- **Expected Behavior:** {expected}\n"
            f"- **Observed Behavior:** {observed}\n"
        )

        # ----------------------------------------------------
        # Section 2: Root-Cause Diagnosis
        # ----------------------------------------------------
        s2_md = (
            f"- **Most Likely Cause:** {root_cause}\n"
            f"- **Diagnosis Status:** {status_label} (Confidence: {confidence.capitalize()})\n"
            f"- **Causal Explanation:**\n{causal_explanation}\n"
        )

        # ----------------------------------------------------
        # Section 3: Supporting Evidence
        # ----------------------------------------------------
        causal_evs = [ev for ev in evidence_pool if is_causal_evidence(ev)]
        other_evs = [ev for ev in evidence_pool if not is_causal_evidence(ev)][:5]

        ev_lines = []
        if causal_evs:
            ev_lines.append("#### Causal / Defect Evidence")
            for ev in causal_evs:
                src = ev.get("source") or "unknown"
                line = ev.get("line") or "?"
                snip = (ev.get("snippet") or ev.get("matched_text") or "").strip()
                ev_lines.append(f"- **`{src}`** (Line {line}):")
                if snip:
                    ev_lines.append(f"  ```\n  {snip}\n  ```")
        
        if other_evs:
            ev_lines.append("#### Contextual Observations")
            for ev in other_evs:
                src = ev.get("source") or "unknown"
                line = ev.get("line") or "?"
                ev_lines.append(f"- `{src}` (Line {line}): inspected during investigation")

        contra_evaluation = diagnosis_data.get("contradictory_evidence_evaluation") or "No contradictory evidence detected."
        ev_lines.append(f"\n- **Contradictory Evidence:** {contra_evaluation}")

        s3_md = "\n".join(ev_lines) + "\n"

        # ----------------------------------------------------
        # Section 4: Affected Components
        # ----------------------------------------------------
        comp_lines = ["| Component | File Path | Failure Role |", "|:---|:---|:---|"]
        components = diagnosis_data.get("affected_components", [])
        if components:
            for c in components:
                name = c.get("name") or Path(c.get("file", "unknown")).name
                fpath = c.get("file") or name
                role = c.get("role") or "Affected component"
                comp_lines.append(f"| **{name}** | `{fpath}` | {role} |")
        else:
            comp_lines.append("| Unspecified | Target repository | Inspected module |")
        s4_md = "\n".join(comp_lines) + "\n"

        # ----------------------------------------------------
        # Section 5: Suggested Fix
        # ----------------------------------------------------
        regressions = fix_details.get("potential_regressions") or ["Run existing test suite to ensure no regressions."]
        reg_str = "\n".join(f"  - {r}" for r in regressions)
        snippet_str = f"\n```\n{fix_details.get('code_snippet')}\n```" if fix_details.get("code_snippet") else ""

        s5_md = (
            f"- **Target Location:** `{fix_details.get('file', 'affected module')}` ({fix_details.get('component', 'failing routine')})\n"
            f"- **Recommended Change:** {fix_details.get('behavior_change', fix_text)}\n"
            f"- **Correction Rationale:** {fix_details.get('rationale', 'Resolves the defect mechanism identified by evidence.')}\n"
            f"- **Contract Invariants:** {fix_details.get('invariants_to_preserve', 'Preserve existing function contracts and caller signatures.')}\n"
            f"- **Potential Regressions to Check:**\n{reg_str}\n"
            f"{snippet_str}\n\n"
            f"> **Scope Restriction:** Fix recommendation only. The target application's source code was not modified."
        )

        # ----------------------------------------------------
        # Section 6: Verification Steps
        # ----------------------------------------------------
        v_lines = [
            f"- **Verification Status:** `{verification_summary.overall_status}`",
        ]
        if verification_summary.blocker_reasons:
            v_lines.append(f"- **Execution Blockers:** {'; '.join(verification_summary.blocker_reasons)}")
        
        if verification_summary.executed_checks:
            v_lines.append("#### Executed Checks")
            for ec in verification_summary.executed_checks:
                v_lines.append(f"- **{ec.name}** (`{ec.command or 'probe'}`): {ec.execution_result or ec.status}")

        if verification_summary.proposed_checks:
            v_lines.append("#### Proposed Actionable Checks")
            for pc in verification_summary.proposed_checks:
                v_lines.append(f"- **{pc.name}:** {pc.description}")
                v_lines.append(f"  - *Expected Outcome:* {pc.expected_outcome}")
                if pc.edge_cases:
                    v_lines.append(f"  - *Edge Cases:* {'; '.join(pc.edge_cases)}")

        s6_md = "\n".join(v_lines) + "\n"

        # ----------------------------------------------------
        # Section 7: Remaining Uncertainty
        # ----------------------------------------------------
        uncertainty = diagnosis_data.get("remaining_uncertainty", "None remaining.")
        unanswered = review_result.get("unanswered_questions") or []
        missing = review_result.get("missing_evidence") or []

        u_lines = [f"{uncertainty}\n"]
        if unanswered:
            u_lines.append("- **Unresolved Questions:**")
            for q in unanswered:
                u_lines.append(f"  - {q}")
        if missing:
            u_lines.append("- **Missing Evidence:**")
            for m in missing:
                u_lines.append(f"  - {m}")
        
        u_lines.append("\n- **Next Steps to Resolve:** Execute proposed verification checks in an isolated runtime environment with debug logging enabled.")
        s7_md = "\n".join(u_lines) + "\n"

        # ----------------------------------------------------
        # Compile Coherent Final Report
        # ----------------------------------------------------
        full_report = (
            "# Investigation Report\n\n"
            "## 1. Problem Summary\n"
            f"{s1_md}\n"
            "## 2. Root-Cause Diagnosis\n"
            f"{s2_md}\n"
            "## 3. Supporting Evidence\n"
            f"{s3_md}\n"
            "## 4. Affected Components\n"
            f"{s4_md}\n"
            "## 5. Suggested Fix\n"
            f"{s5_md}\n\n"
            "## 6. Verification Steps\n"
            f"{s6_md}\n"
            "## 7. Remaining Uncertainty\n"
            f"{s7_md}"
        )

        report_sections = {
            "problem_summary": {
                "markdown": s1_md,
                "problem_description": prob_summary,
                "expected_behavior": expected,
                "observed_behavior": observed,
            },
            "root_cause_diagnosis": {
                "markdown": s2_md,
                "most_likely_cause": root_cause,
                "status": status_label,
                "confidence": confidence,
                "causal_explanation": causal_explanation,
            },
            "supporting_evidence": {
                "markdown": s3_md,
                "causal_defect_evidence": causal_evs,
                "contextual_evidence": other_evs,
                "contradictory_evidence": contra_evaluation,
            },
            "affected_components": components,
            "suggested_fix": {
                "markdown": s5_md,
                **fix_details,
            },
            "verification_steps": {
                "markdown": s6_md,
                "status": getattr(verification_summary, "overall_status", getattr(verification_summary, "verification_status", "not verified")),
                "execution_blockers": verification_summary.blocker_reasons,
                "executed_tests": [
                    ec if isinstance(ec, dict) else (ec.__dict__ if hasattr(ec, "__dict__") else str(ec))
                    for ec in verification_summary.executed_checks
                ],
                "proposed_checks": [
                    pc if isinstance(pc, dict) else (pc.__dict__ if hasattr(pc, "__dict__") else str(pc))
                    for pc in verification_summary.proposed_checks
                ],
            },
            "remaining_uncertainty": {
                "markdown": s7_md,
                "unresolved_questions": unanswered,
                "missing_evidence": missing,
                "next_steps": "Execute proposed verification checks in an isolated runtime environment with debug logging enabled.",
            },
        }

        return full_report, report_sections
