"""Hypothesis manager for tracking, consolidating, and correlating candidate defect explanations."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class HypothesisManager:
    """Manages working investigation hypotheses without over-committing to an unverified root cause."""

    def __init__(self) -> None:
        self._hypotheses: List[Dict[str, Any]] = []

    def reset(self) -> None:
        """Reset internal hypothesis state."""
        self._hypotheses.clear()

    def get_hypotheses(self) -> List[Dict[str, Any]]:
        """Return tracked hypotheses."""
        return list(self._hypotheses)

    def add_hypothesis(self, hypothesis: Dict[str, Any]) -> None:
        """Add a discovered hypothesis to tracking."""
        if hypothesis and isinstance(hypothesis, dict):
            self._hypotheses.append(hypothesis)

    def add_hypotheses(self, hypotheses: List[Dict[str, Any]]) -> None:
        """Add multiple discovered hypotheses to tracking."""
        for h in hypotheses:
            self.add_hypothesis(h)

    def consolidate(self, raw_hypotheses: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Consolidate duplicate hypotheses across tasks and specialists while preserving
        competing explanations, attribution, and all cited evidence."""
        consolidated: List[Dict[str, Any]] = []

        for h in raw_hypotheses:
            if not isinstance(h, dict):
                continue

            h_stmt = str(h.get("statement") or h.get("possible_cause") or "").strip().lower()
            h_title = str(h.get("hypothesis_title") or "").strip().lower()
            h_id = str(h.get("hypothesis_id") or "")

            match_target = None
            for existing in consolidated:
                ex_stmt = str(existing.get("statement") or existing.get("possible_cause") or "").strip().lower()
                ex_title = str(existing.get("hypothesis_title") or "").strip().lower()
                ex_id = str(existing.get("hypothesis_id") or "")

                # Duplicate condition:
                # 1. Exact same non-empty hypothesis_id, OR
                # 2. Exact same non-empty statement/possible_cause, OR
                # 3. Exact same non-empty title AND same possible_cause
                if (h_id and h_id == ex_id) or (h_stmt and h_stmt == ex_stmt) or (h_title and h_title == ex_title and h_stmt == ex_stmt):
                    match_target = existing
                    break

            if match_target is not None:
                # Duplicate recognized: consolidate contributions without losing attribution or evidence
                producers = match_target.setdefault("contributing_specialists", [match_target.get("produced_by")])
                for p in h.get("contributing_specialists", [h.get("produced_by") or h.get("agent_name")]):
                    if p and p not in producers:
                        producers.append(p)

                # Merge supporting evidence IDs
                supp_ids = match_target.setdefault("supporting_evidence_ids", [])
                for eid in h.get("supporting_evidence_ids", []):
                    if eid not in supp_ids:
                        supp_ids.append(eid)

                # Merge supporting evidence items
                supp_ev = match_target.setdefault("supporting_evidence", [])
                seen_supp = {str(it.get("snippet") if isinstance(it, dict) else it) for it in supp_ev}
                for it in h.get("supporting_evidence", []):
                    snip = str(it.get("snippet") if isinstance(it, dict) else it)
                    if snip not in seen_supp:
                        supp_ev.append(it)
                        seen_supp.add(snip)

                # Merge contradicting evidence IDs
                contra_ids = match_target.setdefault("contradicting_evidence_ids", [])
                for eid in h.get("contradicting_evidence_ids", []):
                    if eid not in contra_ids:
                        contra_ids.append(eid)

                # Merge contradicting evidence items
                contra_ev = match_target.setdefault("contradicting_evidence", [])
                seen_contra = {str(it.get("snippet") if isinstance(it, dict) else it) for it in contra_ev}
                for it in h.get("contradicting_evidence", []):
                    snip = str(it.get("snippet") if isinstance(it, dict) else it)
                    if snip not in seen_contra:
                        contra_ev.append(it)
                        seen_contra.add(snip)

                # Merge unanswered questions
                unans = match_target.setdefault("unanswered_questions", [])
                for q in h.get("unanswered_questions", []):
                    if q not in unans:
                        unans.append(q)

                # Record duplicate tracking
                dups = match_target.setdefault("duplicate_hypothesis_ids", [])
                if h_id and h_id != match_target.get("hypothesis_id") and h_id not in dups:
                    dups.append(h_id)
            else:
                # Competing or distinct hypothesis: retain as separate
                consolidated.append(dict(h))

        return consolidated

    def correlate_cross_specialist(
        self,
        consolidated_hypotheses: List[Dict[str, Any]],
        task_results: Dict[str, Any],
        plan_meta: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """Synthesize cross-specialist correlated hypotheses connecting code implementations,
        dependency flows, and runtime observations."""
        results = list(consolidated_hypotheses)
        plan_meta = plan_meta or {}
        plan_id = plan_meta.get("plan_id") or "PLAN-001"

        specialists_represented = {r.get("agent_name") for r in task_results.values() if r.get("agent_name")}
        if len(specialists_represented) >= 2 and any("code" in s for s in specialists_represented) and any("dependency" in s for s in specialists_represented):
            code_hyps = [h for h in results if "code" in str(h.get("agent_name", "")).lower() or "code" in str(h.get("produced_by", "")).lower()]
            dep_hyps = [h for h in results if "dependency" in str(h.get("agent_name", "")).lower() or "dependency" in str(h.get("produced_by", "")).lower()]
            
            if code_hyps and dep_hyps:
                combined_supp_ids = []
                for ch in code_hyps + dep_hyps:
                    for eid in ch.get("supporting_evidence_ids", []):
                        if eid not in combined_supp_ids:
                            combined_supp_ids.append(eid)

                # Avoid duplicate cross-correlation hypotheses
                if not any(h.get("hypothesis_id") == "HYP-CROSS-001" for h in results):
                    from app.agents.investigator_contract import create_hypothesis
                    primary_code = code_hyps[0]
                    primary_dep = dep_hyps[0]
                    code_title = primary_code.get("hypothesis_title") or primary_code.get("title") or "code implementation"
                    dep_title = primary_dep.get("hypothesis_title") or primary_dep.get("title") or "interface contract"

                    cross_title = f"Cross-component correlation between {dep_title} and {code_title}"
                    cross_cause = (
                        f"Interaction between dependency contract ({primary_dep.get('possible_cause', dep_title)}) "
                        f"and code-level logic ({primary_code.get('possible_cause', code_title)})."
                    )
                    cross_reasoning = (
                        f"Code inspection identified: {primary_code.get('reasoning', 'local implementation details')}. "
                        f"Dependency analysis identified: {primary_dep.get('reasoning', 'module boundaries and call propagation')}."
                    )
                    unans = []
                    for q in primary_code.get("unanswered_questions", []) + primary_dep.get("unanswered_questions", []):
                        if q not in unans:
                            unans.append(q)
                    if not unans:
                        unans = ["Does runtime execution confirm cross-component defect propagation across these boundaries?"]

                    cross_hyp = create_hypothesis(
                        hypothesis_id="HYP-CROSS-001",
                        hypothesis_title=cross_title,
                        produced_by="Cross-Specialist Correlation",
                        possible_cause=cross_cause,
                        evidence="Correlated evidence across code implementation and module dependencies",
                        reasoning=cross_reasoning,
                        supporting_evidence_ids=combined_supp_ids,
                        status="needs_validation",
                        confidence="medium",
                        unanswered_questions=unans,
                        task_id="CROSS-CORRELATION",
                        agent_name="orchestrator",
                    )
                    cross_hyp["contributing_specialists"] = sorted(list(specialists_represented))
                    cross_hyp["plan_id"] = plan_id
                    cross_hyp["objective_id"] = "OBJ-CROSS"
                    cross_hyp["task_objective"] = "Synthesize cross-specialist evidence chain"
                    results.append(cross_hyp)

        return results
