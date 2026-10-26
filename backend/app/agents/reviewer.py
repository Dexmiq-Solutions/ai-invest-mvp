"""Phase 8 Reviewer Agent.

Evaluates the existing shared state (accumulated evidence, hypotheses, findings)
using an LLM (with deterministic heuristic fallback) to determine:
1. What symptoms and hypotheses have been identified?
2. What evidence supports or contradicts each hypothesis?
3. Is the evidence sufficient to identify a root cause?
4. What information is missing?
5. Is further investigation necessary?

Evaluates every hypothesis against the original reported problem and expected symptoms,
distinguishes directly relevant causal evidence from unrelated findings and imports,
and requires a plausible, evidence-supported causal explanation before declaring sufficiency.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Callable, Dict, List, Optional, Tuple
from pydantic import BaseModel, Field

from app.agents.investigation_orchestrator import (
    InvestigationReviewer,
    is_causal_evidence,
    is_causal_defect_finding,
    _source_path,
    _component_category,
)

logger = logging.getLogger("app.agents.reviewer")

STOP_WORDS = {
    "the", "a", "an", "in", "on", "of", "to", "for", "with", "at", "by", "from",
    "and", "or", "is", "are", "was", "were", "be", "been", "that", "this", "it",
    "its", "as", "into", "when", "does", "do", "did", "not", "but", "if", "so",
    "can", "could", "should", "would", "may", "might", "must", "than", "then",
    "their", "there", "some", "any", "all", "each", "every", "other", "such",
    "only", "also", "about", "potentially", "resulting", "during", "after",
    "before", "between", "while", "where", "which", "who", "whom", "how",
    "issue", "problem", "error", "bug", "application", "app", "system", "code",
    "file", "function", "method", "class", "module", "component", "model",
    "have", "has", "had", "fails", "failing", "failure", "investigate",
    "identify", "report", "findings", "based", "actual", "database", "db", "due"
}


def extract_stemmed_tokens(text: str) -> set[str]:
    """Extract significant stemmed domain tokens from text for relevance matching."""
    words = re.findall(r"[a-zA-Z0-9_]+", str(text or "").lower())
    tokens = set()
    for w in words:
        if len(w) < 3 or w in STOP_WORDS:
            continue
        tokens.add(w)
        if w.endswith("ing") and len(w) > 5:
            tokens.add(w[:-3])
        elif w.endswith("ed") and len(w) > 4:
            tokens.add(w[:-2])
        elif w.endswith("es") and len(w) > 4:
            tokens.add(w[:-2])
        elif w.endswith("er") and len(w) > 4:
            tokens.add(w[:-2])
        elif w.endswith("s") and len(w) > 3 and not w.endswith("ss"):
            tokens.add(w[:-1])
    return tokens


def is_solely_import_or_relationship(snippet: str) -> bool:
    """Determine whether an evidence snippet only demonstrates import statements, requires, or simple plumbing."""
    clean = (snippet or "").strip()
    if not clean:
        return True
    lines = [ln.strip() for ln in clean.splitlines() if ln.strip()]
    if not lines:
        return True
    for ln in lines:
        s = ln.rstrip(";").strip()
        is_imp = (
            s.startswith("import ")
            or s.startswith("from ")
            or ("require(" in s and any(k in s for k in ("const ", "let ", "var ", "=")))
            or (s.startswith("export ") and ("from " in s or "= require(" in s or s.startswith("export default") or s.startswith("export {")))
            or s.startswith("//") or s.startswith("#") or s.startswith("/*") or s.startswith("*")
            or s in ("{", "}", ");", "};", "});", ")", "}", "};")
            or (s.startswith("router.use(") and s.endswith(")"))
        )
        if not is_imp:
            return False
    return True


def is_hypothesis_relevant_to_problem(
    hypothesis: Dict[str, Any],
    problem: str,
    original_problem: Optional[str] = None,
    expected_behavior: Optional[str] = None,
    problem_context: Optional[Dict[str, Any]] = None,
) -> Tuple[bool, str]:
    """
    Evaluates whether a candidate hypothesis addresses the original reported problem and symptoms.
    Returns (is_relevant, explanation).
    """
    prob_combined = f"{original_problem or ''} {problem or ''} {expected_behavior or ''}"
    if problem_context and isinstance(problem_context, dict):
        prob_combined += " " + " ".join(str(v) for v in problem_context.values() if isinstance(v, (str, list)))

    p_tokens = extract_stemmed_tokens(prob_combined)
    h_text = f"{hypothesis.get('statement', '')} {hypothesis.get('hypothesis_title', '')} {hypothesis.get('title', '')} {hypothesis.get('possible_cause', '')}"
    h_tokens = extract_stemmed_tokens(h_text)

    if not p_tokens:
        return True, "Problem statement is generic; hypothesis evaluated on evidence."

    # Domain classifications
    auth_domain = {"auth", "login", "token", "jwt", "email", "password", "bearer", "401", "unauthorized", "session", "cookie", "profile"}
    task_domain = {"task", "tasks", "todo", "due_date", "duedate", "priority", "assignee", "order", "insertwithdue", "taskmodel", "taskservice"}
    config_domain = {"env", "environ", "environment", "variable", "config", "configuration", "keyerror"}
    port_domain = {"port", "startup", "listen", "server", "valueerror"}
    parser_domain = {"parser", "parse", "typeerror", "null", "none"}

    domains = [auth_domain, task_domain, config_domain, port_domain, parser_domain]
    p_matched_domains = [d for d in domains if p_tokens & d]
    h_matched_domains = [d for d in domains if h_tokens & d]

    # Domain mismatch check: if problem belongs to domain A and hypothesis belongs to domain B != A
    if p_matched_domains and h_matched_domains and not any(d in h_matched_domains for d in p_matched_domains):
        return False, "Hypothesis addresses an unrelated subsystem rather than the reported problem domain"

    # Specific check: if problem is task domain and not auth, but hypothesis is auth and has no task keywords
    if (p_tokens & task_domain) and not (p_tokens & auth_domain) and (h_tokens & auth_domain) and not (h_tokens & task_domain):
        return False, "Hypothesis addresses authentication but the reported problem is task creation"

    # Specific direct token overlap (ignoring generic terms like 'user', 'data')
    specific_overlap = {t for t in p_tokens.intersection(h_tokens) if t not in ("user", "users", "data")}
    if specific_overlap:
        return True, f"Hypothesis matches problem concepts: {sorted(list(specific_overlap))}"

    for pt in p_tokens:
        if pt in ("user", "users", "data"):
            continue
        for ht in h_tokens:
            if ht in ("user", "users", "data"):
                continue
            if (len(pt) >= 4 and pt in ht) or (len(ht) >= 4 and ht in pt):
                return True, f"Hypothesis matches problem concept substring: {pt} ~ {ht}"

    if p_matched_domains and not specific_overlap:
        return False, f"Hypothesis does not mention or address the reported problem concepts ({sorted(list(p_tokens))[:5]})"

    return True, "Hypothesis plausible for problem context"


def is_causal_evidence_for_problem(
    ev: Dict[str, Any],
    problem: str,
    hypothesis: Optional[Dict[str, Any]] = None,
) -> Tuple[bool, str]:
    """
    Determines whether an evidence item represents directly relevant causal evidence
    rather than an import relationship, benign observation, or unrelated snippet.
    """
    if not isinstance(ev, dict):
        return False, "Not a dictionary"

    src = str(ev.get("source") or "")
    if not src or src.startswith("<") or src.startswith("tool_"):
        return False, "Synthetic or tool error source"

    snippet = str(ev.get("matched_text") or ev.get("snippet") or ev.get("statement") or "").strip()
    if not snippet or len(snippet) <= 3:
        return False, "Snippet too short or empty"

    if is_solely_import_or_relationship(snippet):
        return False, "Evidence only demonstrates import or code wiring relationships"

    rel_to_hyp = str(ev.get("relationship_to_hypothesis") or "").lower()
    role = str(ev.get("role") or "").lower()
    if (role == "observation" or rel_to_hyp in ("observational", "observation")) and not ev.get("is_defect_evidence"):
        return False, "Evidence is marked as observational without demonstrating defect mechanism"

    if hypothesis:
        is_rel, rel_reason = is_hypothesis_relevant_to_problem(hypothesis, problem)
        if not is_rel:
            return False, f"Supporting hypothesis is irrelevant to problem: {rel_reason}"

    return True, "Directly relevant causal evidence"


def is_structural_contract_evidence_complete(
    hypothesis: Dict[str, Any],
    evidence_items: List[Dict[str, Any]],
    findings: Optional[List[Dict[str, Any]]] = None,
) -> Tuple[bool, str]:
    """
    For hypotheses alleging parameter mismatches, argument order swapping, or column mapping defects,
    verify that evidence/findings contain both the contract/declaration (e.g., SQL prepared statement or function signature)
    and the invocation/call site (e.g., .run() or function invocation).
    Returns (is_complete, explanation).
    """
    h_text = f"{hypothesis.get('statement', '')} {hypothesis.get('hypothesis_title', '')} {hypothesis.get('possible_cause', '')}".lower()
    is_param_mismatch_hyp = any(k in h_text for k in ("order", "mismatch", "swap", "column", "parameter", "argument", "out of order"))
    if not is_param_mismatch_hyp:
        return True, "Hypothesis is not a contract/parameter mismatch defect"

    snippets = [str(e.get("matched_text") or e.get("snippet") or "") for e in evidence_items]
    if findings:
        for f in findings:
            if isinstance(f, dict):
                stmt = str(f.get("statement") or f.get("finding") or "")
                if stmt:
                    snippets.append(stmt)

    # Check for declaration (e.g. prepared statement, function definition, SQL INSERT declaration, or column order finding)
    has_declaration = any(
        ("insert into" in s.lower() or "prepare(" in s.lower() or "function " in s.lower() or "create table" in s.lower() or "column order" in s.lower() or "sql column" in s.lower())
        for s in snippets
    )

    # Check for call site / invocation passing arguments (e.g. .run(...), .execute(...), function call)
    has_invocation = any(
        (".run(" in s or ".execute(" in s or ".query(" in s or "run(" in s)
        and not ("prepare(" in s.lower() and "insert into" in s.lower())
        and any(k in s.lower() for k in ("insert", "param", "arg", "task.", "data.", "values", ","))
        for s in snippets
    )

    if has_declaration and not has_invocation:
        return False, "Incomplete evidence: contract declaration is present, but invocation/call site is missing"
    if has_invocation and not has_declaration:
        return False, "Incomplete evidence: invocation/call site is present, but contract declaration is missing"

    return True, "Complete structural contract and invocation evidence present"


class HypothesisReview(BaseModel):
    hypothesis_id: str = Field(description="ID of the hypothesis being evaluated")
    statement: str = Field(description="Summary statement or possible cause")
    status: str = Field(description="Status: 'supported', 'contradicted', or 'unverified'")
    supporting_evidence_ids: List[str] = Field(default_factory=list, description="IDs of evidence supporting this hypothesis")
    contradicting_evidence_ids: List[str] = Field(default_factory=list, description="IDs of evidence contradicting this hypothesis")
    evaluation: str = Field(description="Explanation of why this hypothesis is supported, contradicted, or unverified")
    is_relevant_to_problem: bool = Field(default=True, description="Whether this hypothesis addresses the original reported problem")
    relevance_explanation: str = Field(default="", description="Explanation of relevance to original problem")
    is_useful: bool = Field(default=True, description="Whether this hypothesis is useful for developer investigation and debugging")
    usefulness_reason: str = Field(default="", description="Explanation of why this hypothesis is useful or unuseful")


class ReviewResultSchema(BaseModel):
    decision: str = Field(description="Clear decision: 'sufficient', 'insufficient', or 'inconclusive'")
    is_sufficient: bool = Field(description="Whether accumulated evidence is sufficient to identify a root cause")
    confidence: str = Field(description="Confidence rating: 'high', 'medium', or 'low'")
    reasoning: str = Field(description="Detailed explanation and reasoning for the reviewer decision")
    feedback: str = Field(description="Concise feedback summarizing review status or guidance for re-planning")
    identified_symptoms: List[str] = Field(default_factory=list, description="Symptoms and observable anomalies identified")
    hypothesis_evaluations: List[HypothesisReview] = Field(default_factory=list, description="Evaluation of each identified hypothesis")
    useful_hypotheses: List[Dict[str, Any]] = Field(default_factory=list, description="Hypotheses that are relevant and useful to developers even if unconfirmed")
    most_likely_hypothesis: Optional[Dict[str, Any]] = Field(default=None, description="The most likely candidate hypothesis backed by evidence")
    unanswered_questions: List[str] = Field(default_factory=list, description="Key questions that remain unanswered")
    missing_evidence: List[str] = Field(default_factory=list, description="Information or evidence missing from the shared state")
    established_by_evidence: List[str] = Field(default_factory=list, description="Facts and code mechanisms established by verified evidence")
    causal_chain_verified: bool = Field(default=False, description="Whether evidence explains the problem through a coherent causal chain")
    unanswered_objectives: List[str] = Field(default_factory=list, description="Investigation objectives that remain unanswered")
    actionable_feedback: Dict[str, Any] = Field(default_factory=dict, description="Structured actionable feedback for Re-planner when insufficient")
    further_investigation_necessary: bool = Field(default=True, description="Whether further investigation tasks are needed")
    has_conflicting_findings: bool = Field(default=False, description="Whether findings or evidence contain contradictions")
    should_stop: bool = Field(default=False, description="Whether workflow should stop based on review")
    stop_reason: str = Field(default="", description="Stop reason if should_stop is true")
    approved_hypothesis_id: Optional[str] = Field(default=None, description="ID of approved hypothesis if sufficient")


class Reviewer:
    """Evaluates the existing shared state to determine evidence sufficiency and gaps."""

    def __init__(self, llm_factory: Optional[Callable] = None):
        self.llm_factory = llm_factory
        self._orchestrator_reviewer = InvestigationReviewer(llm_factory=llm_factory)

    def review(
        self,
        problem: str,
        evidence: List[Dict[str, Any]],
        findings: List[Dict[str, Any]],
        hypotheses: List[Dict[str, Any]],
        iteration: int = 1,
        max_iterations: int = 3,
        completed_investigators: Optional[set[str] | list[str]] = None,
        completed_tasks: Optional[List[Dict[str, Any]]] = None,
        pending_tasks: Optional[List[Dict[str, Any]]] = None,
        original_problem: Optional[str] = None,
        expected_behavior: Optional[str] = None,
        problem_context: Optional[Dict[str, Any]] = None,
        unanswered_questions: Optional[List[str]] = None,
        missing_evidence: Optional[List[str]] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Evaluate shared state and return structured review dictionary."""
        # 1. Try structured LLM review if LLM factory is available
        llm_eval = self._review_with_llm(
            problem=problem,
            evidence=evidence,
            findings=findings,
            hypotheses=hypotheses,
            iteration=iteration,
            max_iterations=max_iterations,
            original_problem=original_problem,
            expected_behavior=expected_behavior,
            problem_context=problem_context,
            unanswered_questions=unanswered_questions,
            missing_evidence=missing_evidence,
            **kwargs,
        )
        if llm_eval:
            return llm_eval

        # 2. Fall back to deterministic heuristic evaluation
        return self._review_heuristically(
            problem=problem,
            evidence=evidence,
            findings=findings,
            hypotheses=hypotheses,
            iteration=iteration,
            max_iterations=max_iterations,
            completed_investigators=completed_investigators,
            completed_tasks=completed_tasks,
            pending_tasks=pending_tasks,
            original_problem=original_problem,
            expected_behavior=expected_behavior,
            problem_context=problem_context,
            unanswered_questions=unanswered_questions,
            missing_evidence=missing_evidence,
            **kwargs,
        )

    def _review_with_llm(
        self,
        problem: str,
        evidence: List[Dict[str, Any]],
        findings: List[Dict[str, Any]],
        hypotheses: List[Dict[str, Any]],
        iteration: int,
        max_iterations: int,
        original_problem: Optional[str] = None,
        expected_behavior: Optional[str] = None,
        problem_context: Optional[Dict[str, Any]] = None,
        unanswered_questions: Optional[List[str]] = None,
        missing_evidence: Optional[List[str]] = None,
        **kwargs: Any,
    ) -> Optional[Dict[str, Any]]:
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
                    "snippet": (ev.get("snippet") or ev.get("matched_text") or "")[:250],
                    "relationship_to_hypothesis": ev.get("relationship_to_hypothesis", "observational"),
                    "is_import": is_solely_import_or_relationship(str(ev.get("snippet") or ev.get("matched_text") or "")),
                }
                for ev in evidence[:25]
                if isinstance(ev, dict)
            ]

            compact_findings = [
                {
                    "statement": f.get("statement") or f.get("finding") or "",
                    "category": f.get("category", "finding"),
                    "evidence_ids": f.get("evidence_ids", []),
                }
                for f in findings[:20]
                if isinstance(f, dict)
            ]

            compact_hypotheses = [
                {
                    "hypothesis_id": h.get("hypothesis_id"),
                    "hypothesis_title": h.get("hypothesis_title") or h.get("title"),
                    "statement": h.get("statement") or h.get("possible_cause"),
                    "status": h.get("status", "unverified"),
                    "supporting_evidence_ids": h.get("supporting_evidence_ids", []),
                    "contradicting_evidence_ids": h.get("contradicting_evidence_ids", []),
                }
                for h in hypotheses[:15]
                if isinstance(h, dict)
            ]

            prompt = (
                "You are the Lead Investigation Reviewer for an AI Software Issue Investigator.\n"
                "Evaluate the current shared state to determine whether root cause has been proven or more investigation is needed.\n\n"
                f"Original Reported Problem:\n{orig_problem}\n\n"
                f"Problem Statement:\n{problem}\n\n"
                f"Expected Behavior:\n{expected_behavior or 'Expected correct functional behavior without errors'}\n\n"
                f"Accumulated Evidence ({len(evidence)} items):\n{json.dumps(compact_evidence, indent=2, default=str)}\n\n"
                f"Findings ({len(findings)} items):\n{json.dumps(compact_findings, indent=2, default=str)}\n\n"
                f"Candidate Hypotheses ({len(hypotheses)} items):\n{json.dumps(compact_hypotheses, indent=2, default=str)}\n\n"
                f"Investigation Progress: Iteration {iteration} of {max_iterations} maximum.\n\n"
                "Defect Classification & Proof Standards (Approved Option B Policy):\n"
                "1. Distinguish Crash/Exception defects from Silent Semantic or Data-Flow defects:\n"
                "   - Crash / Exception defects: Code halts or throws unhandled exceptions. These require runtime error logs, stack traces, or exception locations.\n"
                "   - Silent Semantic / Data-Flow defects: Code executes without throwing exceptions or generating stack traces, but corrupts data or exhibits erroneous behavior due to structural mismatches "
                "(e.g., parameter/argument order mismatches, swapped function/query arguments, column ordering mismatches, incorrect variable mapping, inverted conditions, or serialization mismatches).\n"
                "   - CRITICAL: For silent semantic or data-flow defects, DO NOT demand nonexistent runtime error logs, stack traces, or crash reports. "
                "Instead, evaluate whether complete, relevant code-contract and invocation evidence establishes the defect mechanism (e.g. comparing the declaration/schema to the invocation/call site).\n\n"
                "2. Conclusiveness & Confidence Standards:\n"
                "   - Medium Confidence ('Likely root cause'): Conclusive. Set decision='sufficient', is_sufficient=True, confidence='medium' when deterministic structural code evidence "
                "completely establishes the defect mechanism (e.g. both declaration and invocation are captured, demonstrating the parameter/column mismatch) and the causal relationship "
                "to the reported symptom is fully explained in reasoning.\n"
                "   - High Confidence ('Verified root cause'): Conclusive. Set decision='sufficient', is_sufficient=True, confidence='high' ONLY when the finding has been empirically verified "
                "through dynamic test probe execution, runtime error traces, or live reproducer execution.\n"
                "   - Low Confidence (Inconclusive / Insufficient): Set decision='insufficient' (or 'inconclusive' if iteration >= max_iterations), is_sufficient=False, confidence='low' "
                "when evidence is incomplete (e.g., declaration seen but call site missing, or ambiguous ordering), when evidence only shows benign imports or wiring, or when hypotheses "
                "are unrelated or weakly supported.\n\n"
                "3. Evaluation Requirements:\n"
                "   a. Evaluate every hypothesis against the original reported problem and expected symptoms.\n"
                "   b. Distinguish directly relevant causal evidence from unrelated findings, imports, and simple code relationships.\n"
                "   c. Import relationships and simple wiring alone MUST NOT establish a root cause.\n"
                "   d. Hypotheses that discuss unrelated components or issues MUST be marked unverified/irrelevant and CANNOT establish sufficiency.\n"
                "   e. In 'reasoning', you MUST explicitly explain the causal chain: how the verified code-level defect mechanism directly produces the observed symptom.\n"
                "   f. If evidence is insufficient, irrelevant, or missing, mark decision='insufficient' and is_sufficient=False so the Re-planner can schedule targeted investigation."
            )

            structured_llm = llm.with_structured_output(ReviewResultSchema) if hasattr(llm, "with_structured_output") else None
            if structured_llm:
                result = structured_llm.invoke(prompt)
                res_dict = result.model_dump() if hasattr(result, "model_dump") else dict(result)
            else:
                raw_res = llm.invoke(prompt)
                content = getattr(raw_res, "content", str(raw_res))
                res_dict = json.loads(content)

            is_suff = bool(res_dict.get("is_sufficient", False))
            approved_hyp_id = res_dict.get("approved_hypothesis_id")
            hyp_evals = res_dict.get("hypothesis_evaluations") or []

            # Programmatic verification safeguard: ensure at least one hypothesis addresses the reported problem
            # with valid causal evidence (not import relationships) and complete structural contract evidence
            ev_map = {
                str(e.get("evidence_id") or e.get("id")): e
                for e in evidence
                if isinstance(e, dict) and (e.get("evidence_id") or e.get("id"))
            }

            has_valid_approved_cause = False
            approved_hyp_obj = None

            for h in hypotheses:
                if not isinstance(h, dict):
                    continue
                h_id = str(h.get("hypothesis_id") or "")
                is_rel, _ = is_hypothesis_relevant_to_problem(h, problem=problem, original_problem=orig_problem, expected_behavior=expected_behavior)
                if not is_rel:
                    continue

                supp_ids = [eid for eid in (h.get("supporting_evidence_ids") or []) if str(eid) in ev_map]
                contra_ids = list(h.get("contradicting_evidence_ids") or [])
                if contra_ids:
                    continue

                valid_causal_ev = [
                    ev_map[str(eid)] for eid in supp_ids
                    if is_causal_evidence_for_problem(ev_map[str(eid)], problem=problem, hypothesis=h)[0]
                ]

                # Verify completeness of structural contract evidence for parameter/column mismatch hypotheses
                is_complete_struct, _ = is_structural_contract_evidence_complete(h, valid_causal_ev, findings=findings)
                if not is_complete_struct:
                    continue

                is_verified = str(h.get("status", "")).lower() == "verified"
                if (valid_causal_ev or is_verified) and (not approved_hyp_id or h_id == str(approved_hyp_id)):
                    has_valid_approved_cause = True
                    approved_hyp_obj = h
                    approved_hyp_id = h_id
                    break

            has_conf = bool(res_dict.get("has_conflicting_findings")) or any(
                h.get("contradicting_evidence_ids") for h in hypotheses if isinstance(h, dict)
            )

            if is_suff and (not has_valid_approved_cause or has_conf):
                is_suff = False
                res_dict["is_sufficient"] = False
                res_dict["confidence"] = "low"
                res_dict["decision"] = "insufficient"
                res_dict["further_investigation_necessary"] = True
                if has_conf:
                    res_dict["reasoning"] = "Findings or evidence contain contradictions; further targeted investigation is required to reconcile them."
                    res_dict["feedback"] = "Contradictions detected in findings; re-planning required."
                else:
                    res_dict["reasoning"] = f"Accumulated evidence does not demonstrate a complete, plausible causal explanation for the reported problem ('{orig_problem[:80]}...')."
                    res_dict["feedback"] = f"Candidate hypotheses or evidence do not establish root cause for '{orig_problem[:80]}...'; re-planning required."

            # Align Reviewer confidence with approved Option B proof policy
            conf_val = str(res_dict.get("confidence") or ("medium" if is_suff else "low")).lower()
            if is_suff:
                is_empirical = (
                    (approved_hyp_obj and str(approved_hyp_obj.get("status", "")).lower() == "verified")
                    or bool(res_dict.get("is_empirically_verified"))
                )
                if not is_empirical and conf_val == "high":
                    conf_val = "medium"
            else:
                conf_val = "low"

            decision = "sufficient" if is_suff else ("inconclusive" if iteration >= max_iterations else "insufficient")
            further_needed = not is_suff and (iteration < max_iterations)

            established_by_evidence = list(res_dict.get("established_by_evidence") or [])
            if not established_by_evidence:
                for ev_item in evidence:
                    if isinstance(ev_item, dict):
                        fact = ev_item.get("observed_fact") or ev_item.get("explanation")
                        src = ev_item.get("source")
                        snip = (ev_item.get("matched_text") or ev_item.get("snippet") or "").strip()
                        if fact:
                            established_by_evidence.append(str(fact))
                        elif src and snip:
                            established_by_evidence.append(f"{src}:{ev_item.get('line') or '?'} - {snip[:80]}")

            unanswered = list(res_dict.get("unanswered_questions") or [])
            missing_ev = list(res_dict.get("missing_evidence") or [])
            actionable_fb = res_dict.get("actionable_feedback") or {
                "unanswered_questions": unanswered,
                "related_hypotheses": [
                    {"hypothesis_id": he.get("hypothesis_id"), "statement": he.get("statement"), "status": he.get("status")}
                    for he in hyp_evals if isinstance(he, dict) and he.get("status") != "contradicted"
                ],
                "existing_evidence": established_by_evidence[:5],
                "required_next_steps": [f"Investigate: {q}" for q in unanswered],
            }

            return {
                "decision": decision,
                "is_sufficient": is_suff,
                "confidence": conf_val,
                "reasoning": str(res_dict.get("reasoning") or res_dict.get("feedback") or "Review completed."),
                "feedback": str(res_dict.get("feedback") or res_dict.get("reasoning") or "Review completed."),
                "identified_symptoms": list(res_dict.get("identified_symptoms") or [problem]),
                "hypothesis_evaluations": list(hyp_evals),
                "unanswered_questions": unanswered,
                "missing_evidence": missing_ev,
                "established_by_evidence": established_by_evidence,
                "causal_chain_verified": is_suff and has_valid_approved_cause,
                "unanswered_objectives": list(res_dict.get("unanswered_objectives") or unanswered),
                "actionable_feedback": actionable_fb,
                "further_investigation_necessary": further_needed,
                "has_conflicting_findings": has_conf,
                "should_stop": is_suff or (iteration >= max_iterations),
                "stop_reason": str(res_dict.get("stop_reason") or ("Evidence sufficiently supports diagnosis" if is_suff else "Maximum iteration limit reached")),
                "approved_hypothesis_id": approved_hyp_id if is_suff else None,
                "approved_hypothesis": approved_hyp_obj if is_suff else None,
            }
        except Exception as exc:
            logger.debug("[Reviewer] LLM review fallback triggered: %s", exc)
            return None

    def _review_heuristically(
        self,
        problem: str,
        evidence: List[Dict[str, Any]],
        findings: List[Dict[str, Any]],
        hypotheses: List[Dict[str, Any]],
        iteration: int,
        max_iterations: int,
        completed_investigators: Optional[set[str] | list[str]] = None,
        completed_tasks: Optional[List[Dict[str, Any]]] = None,
        pending_tasks: Optional[List[Dict[str, Any]]] = None,
        original_problem: Optional[str] = None,
        expected_behavior: Optional[str] = None,
        problem_context: Optional[Dict[str, Any]] = None,
        unanswered_questions: Optional[List[str]] = None,
        missing_evidence: Optional[List[str]] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Deterministic heuristic evaluation ensuring causal sufficiency for the reported problem."""
        orig_problem = original_problem or problem

        # Evidence map
        ev_map = {
            str(e.get("evidence_id") or e.get("id")): e
            for e in evidence
            if isinstance(e, dict) and (e.get("evidence_id") or e.get("id"))
        }

        # Check for conflicting findings
        statements = [
            str(f.get("statement") or f.get("finding") or "").lower()
            for f in findings
        ]
        has_conflict = False
        conflict_description = ""
        for s1 in statements:
            for s2 in statements:
                if (
                    ("does not exist" in s1 and "exists" in s2)
                    or ("fails" in s1 and "succeeds" in s2)
                    or ("returns 401" in s1 and "returns 200" in s2)
                    or ("missing" in s1 and "present" in s2)
                ):
                    has_conflict = True
                    conflict_description = f"Potential contradiction between findings: '{s1[:80]}' vs '{s2[:80]}'"
                    break
            if has_conflict:
                break

        # Initialize question and evidence lists
        unanswered_questions_list: List[str] = list(unanswered_questions or [])
        missing_evidence_list: List[str] = list(missing_evidence or [])
        unanswered_questions = unanswered_questions_list
        missing_evidence = missing_evidence_list

        # Evaluate each hypothesis against problem and evidence
        hypothesis_evaluations: List[Dict[str, Any]] = []
        approved_candidate: Optional[Dict[str, Any]] = None
        has_any_relevant_hypotheses = False
        has_import_only_evidence = False

        for h in hypotheses:
            if not isinstance(h, dict):
                continue
            h_id = str(h.get("hypothesis_id") or "")
            supp_ids = [eid for eid in (h.get("supporting_evidence_ids") or []) if str(eid) in ev_map]
            contra_ids = list(h.get("contradicting_evidence_ids") or [])
            is_verified = str(h.get("status", "")).lower() == "verified"

            # 1. Relevance check against reported problem & expected symptoms
            is_relevant, rel_reason = is_hypothesis_relevant_to_problem(
                h,
                problem=problem,
                original_problem=orig_problem,
                expected_behavior=expected_behavior,
                problem_context=problem_context,
            )

            # 2. Evidence validation
            supp_ev_items = [ev_map[str(eid)] for eid in supp_ids]
            valid_causal_supp_ev = []
            solely_imports_supp_ev = []

            for ev_item in supp_ev_items:
                is_causal, _ = is_causal_evidence_for_problem(ev_item, problem=problem, hypothesis=h)
                snip = str(ev_item.get("matched_text") or ev_item.get("snippet") or "")
                if is_solely_import_or_relationship(snip):
                    solely_imports_supp_ev.append(ev_item)
                elif is_causal:
                    valid_causal_supp_ev.append(ev_item)

            if solely_imports_supp_ev and not valid_causal_supp_ev:
                has_import_only_evidence = True

            # 3. Determine status and evaluation
            if contra_ids:
                h_status = "contradicted"
                eval_text = f"Hypothesis contradicted by evidence: {contra_ids}"
            elif not is_relevant:
                h_status = "unverified"
                eval_text = f"Hypothesis does not address the reported problem ('{orig_problem[:80]}...'). {rel_reason}"
            elif valid_causal_supp_ev:
                has_any_relevant_hypotheses = True
                is_complete_struct, struct_reason = is_structural_contract_evidence_complete(h, valid_causal_supp_ev, findings=findings)
                if not is_complete_struct:
                    h_status = "unverified"
                    eval_text = f"Hypothesis addresses the reported problem, but structural contract evidence is incomplete: {struct_reason}."
                    missing_evidence_list.append(struct_reason)
                else:
                    h_status = "supported"
                    eval_text = f"Hypothesis addresses the reported problem and is supported by confirmed causal evidence: {[e.get('evidence_id') or e.get('id') for e in valid_causal_supp_ev]}"
                    if not approved_candidate:
                        approved_candidate = {
                            **h,
                            "status": "supported",
                            "supporting_evidence_ids": [e.get("evidence_id") or e.get("id") for e in valid_causal_supp_ev],
                        }
            elif is_verified:
                has_any_relevant_hypotheses = True
                h_status = "supported"
                eval_text = "Hypothesis addresses the reported problem and is verified through empirical test probe."
                if not approved_candidate:
                    approved_candidate = h
            elif solely_imports_supp_ev:
                has_any_relevant_hypotheses = True
                h_status = "unverified"
                eval_text = (
                    f"Hypothesis addresses the reported problem, but supporting evidence only demonstrates import or code relationships "
                    f"without establishing a causal defect: {[e.get('evidence_id') or e.get('id') for e in solely_imports_supp_ev]}"
                )
            elif supp_ids:
                has_any_relevant_hypotheses = True
                h_status = "unverified"
                eval_text = f"Hypothesis addresses the reported problem, but candidate evidence {supp_ids} lacks causal defect demonstration."
            else:
                has_any_relevant_hypotheses = True
                h_status = "unverified"
                eval_text = "Hypothesis addresses the reported problem, but lacks supporting causal evidence in the shared state."

            # Evaluate usefulness: a hypothesis is useful if it is relevant to the problem
            # and not contradicted by confirmed evidence, even if full empirical verification remains pending.
            is_useful = is_relevant and (h_status != "contradicted")
            usefulness_reason = ""
            if not is_relevant:
                usefulness_reason = "Not useful: hypothesis does not address the reported problem domain."
            elif h_status == "contradicted":
                usefulness_reason = "Not useful: hypothesis is contradicted by verified evidence in the shared state."
            elif h_status == "supported":
                usefulness_reason = "Highly useful: hypothesis is backed by verified causal defect evidence."
            elif valid_causal_supp_ev or supp_ids:
                usefulness_reason = "Useful: hypothesis is aligned with reported symptoms and supported by initial code observations, though complete verification is pending."
            else:
                usefulness_reason = "Potentially useful: hypothesis provides a plausible candidate cause aligned with the problem context."

            hypothesis_evaluations.append({
                "hypothesis_id": h_id,
                "hypothesis_title": h.get("hypothesis_title") or h.get("title") or "",
                "statement": h.get("statement") or h.get("possible_cause") or "",
                "status": h_status,
                "supporting_evidence_ids": [e.get("evidence_id") or e.get("id") for e in valid_causal_supp_ev] if valid_causal_supp_ev else supp_ids,
                "contradicting_evidence_ids": contra_ids,
                "evaluation": eval_text,
                "is_relevant_to_problem": is_relevant,
                "relevance_explanation": rel_reason,
                "is_useful": is_useful,
                "usefulness_reason": usefulness_reason,
            })

        useful_hypotheses = [
            he for he in hypothesis_evaluations
            if he.get("is_useful")
        ]
        most_likely_hypothesis = approved_candidate
        if not most_likely_hypothesis and useful_hypotheses:
            sorted_useful = sorted(
                useful_hypotheses,
                key=lambda x: len(x.get("supporting_evidence_ids") or []),
                reverse=True,
            )
            most_likely_hypothesis = sorted_useful[0]

        # Decision logic:
        # A sufficient decision is ONLY allowed when:
        # - An approved hypothesis addressing the reported problem with relevant causal evidence exists
        # - No unaddressed contradictions exist
        is_sufficient = False
        confidence = "low"
        feedback = ""
        stop_reason = ""
        reasoning_text = ""

        if has_conflict:
            is_sufficient = False
            confidence = "low"
            feedback = f"Findings or evidence contain conflicting data: {conflict_description}. Further targeted investigation is required to reconcile these contradictions."
            unanswered_questions.append("Which of the conflicting findings accurately reflects the live code behavior?")
            missing_evidence.append("Definitive code path showing whether the condition actually triggers.")

        elif approved_candidate is not None:
            is_sufficient = True
            is_empirical = (
                str(approved_candidate.get("status", "")).lower() == "verified"
                or approved_candidate.get("is_empirically_verified")
                or approved_candidate.get("verification_method") == "empirical_probe"
            )
            # Enforce Option B proof policy:
            # - High confidence ("Verified root cause") ONLY for empirically verified findings
            # - Medium confidence ("Likely root cause") for deterministic structural code evidence
            confidence = "high" if is_empirical else "medium"
            cause_title = approved_candidate.get("statement") or approved_candidate.get("hypothesis_title") or approved_candidate.get("possible_cause")
            if is_empirical:
                feedback = f"Sufficient empirical evidence verifies root cause for '{orig_problem[:80]}': {cause_title}"
                reasoning_text = f"Root cause verified through empirical test probe / runtime execution for '{orig_problem[:80]}': {cause_title}."
                stop_reason = "Verified root cause established by test probe."
            else:
                feedback = f"Sufficient structural code evidence establishes likely root cause for '{orig_problem[:80]}': {cause_title}"
                if any(k in str(cause_title).lower() for k in ("order", "mismatch", "swap", "column", "parameter", "argument")):
                    defect_detail = (
                        "The contract mismatch between the SQL declaration and invocation site causes arguments to be persisted out of order, "
                        "directly producing the reported failure symptom."
                    )
                else:
                    defect_detail = (
                        "Deterministic code inspection verifies the defect mechanism in the inspected source files, "
                        "directly producing the reported failure symptom."
                    )
                reasoning_text = (
                    f"Deterministic structural code evidence establishes the root cause for '{orig_problem[:80]}': {cause_title}. "
                    f"{defect_detail}"
                )
                stop_reason = "Evidence sufficiently supports a plausible root cause for the reported problem."

        else:
            is_sufficient = False
            confidence = "low"
            if hypotheses and not has_any_relevant_hypotheses:
                feedback = (
                    f"Candidate hypotheses do not address the original reported problem ('{orig_problem[:80]}...'). "
                    "Targeted investigation into the actual reported failure mechanism is required."
                )
                unanswered_questions.append(f"What specific code logic causes the reported issue: '{orig_problem}'?")
                missing_evidence.append(f"Direct causal defect evidence addressing '{orig_problem}'.")
            elif has_import_only_evidence:
                feedback = (
                    f"Evidence collected so far only demonstrates import relationships or code structure; "
                    f"no causal defect mechanism has been established for '{orig_problem[:80]}...'. Import relationships alone cannot establish a root cause."
                )
                unanswered_questions.append("Where does the actual failure or parameter mismatch occur in the implementation logic?")
                missing_evidence.append("Causal evidence demonstrating the defect mechanism (e.g. incorrect parameter ordering, query flaw, or unhandled exception) rather than simple imports.")
            elif hypotheses:
                feedback = (
                    f"Candidate hypothesis addresses the problem, but lacks supporting causal evidence in the shared state. "
                    "Further targeted investigation is required."
                )
                unanswered_questions.append(f"What evidence verifies the suspected defect mechanism for '{orig_problem}'?")
                missing_evidence.append("Concrete source code or runtime evidence demonstrating the defect.")
            else:
                feedback = f"No candidate hypotheses have been formulated for '{orig_problem[:80]}...'. Targeted investigation required."
                unanswered_questions.append(f"Where in the codebase does '{orig_problem}' originate?")
                missing_evidence.append("Direct source snippet matching the error or problem description.")

        decision = "sufficient" if is_sufficient else ("inconclusive" if iteration >= max_iterations else "insufficient")
        further_needed = (not is_sufficient) and (iteration < max_iterations)
        should_stop = is_sufficient or (iteration >= max_iterations)
        if not stop_reason:
            if is_sufficient:
                stop_reason = "Evidence threshold reached with confirmed causal root cause"
            elif iteration >= max_iterations:
                stop_reason = "Maximum iteration limit reached"

        identified_symptoms = [orig_problem]
        for f in findings:
            stmt = str(f.get("statement") or f.get("finding") or "")
            if any(k in stmt.lower() for k in ("fails", "error", "401", "mismatch", "crash", "rejection", "unauthorized", "wrong", "swap")):
                if stmt not in identified_symptoms:
                    identified_symptoms.append(stmt)

        # Collect established facts from verified evidence
        established_by_evidence: List[str] = []
        for ev_item in evidence:
            if isinstance(ev_item, dict):
                src = ev_item.get("source") or ev_item.get("path")
                line = ev_item.get("line")
                snip = (ev_item.get("matched_text") or ev_item.get("snippet") or "").strip()
                fact = ev_item.get("observed_fact") or ev_item.get("explanation")
                if fact:
                    established_by_evidence.append(str(fact))
                elif src and snip:
                    established_by_evidence.append(f"{src}:{line or '?'} - {snip[:80]}")
        for f in findings:
            if isinstance(f, dict):
                stmt = f.get("statement") or f.get("finding")
                ev_ids = f.get("evidence_ids") or []
                if stmt and ev_ids and stmt not in established_by_evidence:
                    established_by_evidence.append(str(stmt))

        seen_est = set()
        dedup_established = []
        for item in established_by_evidence:
            if item not in seen_est:
                seen_est.add(item)
                dedup_established.append(item)
        established_by_evidence = dedup_established

        causal_chain_verified = (
            approved_candidate is not None
            and not has_conflict
            and bool(approved_candidate.get("supporting_evidence_ids"))
        )

        unanswered_objectives: List[str] = []
        if completed_tasks:
            for t in completed_tasks:
                if isinstance(t, dict):
                    status = str(t.get("status", "")).lower()
                    if status in ("unresolved", "failed", "partial"):
                        obj = t.get("objective") or t.get("task")
                        if obj and obj not in unanswered_objectives:
                            unanswered_objectives.append(str(obj))
        for uq in unanswered_questions:
            if uq not in unanswered_objectives:
                unanswered_objectives.append(str(uq))

        related_hypotheses_summary = [
            {
                "hypothesis_id": he.get("hypothesis_id"),
                "statement": he.get("statement"),
                "status": he.get("status"),
                "relevance": he.get("relevance_explanation"),
            }
            for he in hypothesis_evaluations
            if he.get("status") != "contradicted"
        ]

        required_next_steps: List[str] = []
        for uq in unanswered_questions:
            required_next_steps.append(f"Investigate: {uq}")
        for me in missing_evidence:
            required_next_steps.append(f"Obtain evidence: {me}")

        actionable_feedback = {
            "unanswered_questions": list(unanswered_questions),
            "related_hypotheses": related_hypotheses_summary,
            "existing_evidence": established_by_evidence[:5],
            "required_next_steps": required_next_steps,
        }

        return {
            "decision": decision,
            "is_sufficient": is_sufficient,
            "confidence": confidence,
            "reasoning": reasoning_text if is_sufficient else feedback,
            "feedback": feedback,
            "identified_symptoms": identified_symptoms,
            "hypothesis_evaluations": hypothesis_evaluations,
            "unanswered_questions": unanswered_questions,
            "missing_evidence": missing_evidence,
            "established_by_evidence": established_by_evidence,
            "causal_chain_verified": causal_chain_verified,
            "unanswered_objectives": unanswered_objectives,
            "actionable_feedback": actionable_feedback,
            "further_investigation_necessary": further_needed,
            "has_conflicting_findings": has_conflict,
            "should_stop": should_stop,
            "stop_reason": stop_reason,
            "approved_hypothesis_id": approved_candidate.get("hypothesis_id") if approved_candidate else None,
            "approved_hypothesis": approved_candidate,
            "useful_hypotheses": useful_hypotheses,
            "most_likely_hypothesis": most_likely_hypothesis,
        }
