"""Guards, filters, and ranking helpers for investigation evidence and diagnosis."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional


def is_trivial_line(line: Any) -> bool:
    """Return True if a line is whitespace, comment, trivial syntax or boilerplate."""
    if line is None:
        return True
    s = str(line).strip()
    if not s or len(s) == 0:
        return True
    # Comments
    if s.startswith(("//", "#", "/*", "*", "'''", '"""')):
        return True
    # Single brackets, braces, semicolons, empty blocks
    if s in {"{", "}", "(", ")", "[", "]", ";", ",", ":", "{};", "};", "):", "pass", "return;", "break;", "continue;"}:
        return True
    if len(s) <= 2 and s in {"{", "}", "(", ")", "[", "]", ";", ",", ":", '""', "''"}:
        return True
    return False


def diagnosis_label(hypotheses: Optional[List[Dict[str, Any]]]) -> str:
    """Determine a status label based on the state of hypotheses."""
    if not hypotheses:
        return "Investigation incomplete"

    for h in hypotheses:
        if isinstance(h, dict) and str(h.get("status", "")).strip().lower() == "verified":
            return "Verified defect"

    return "Unverified hypothesis"


def mechanism_statement(
    property_name: str = "token claim",
    write_site: Optional[Dict[str, Any]] = None,
    read_site: Optional[Dict[str, Any]] = None,
    query_site: Optional[Dict[str, Any]] = None,
    is_verified: bool = False,
) -> str:
    """Construct a grounded causal mechanism statement."""
    w_src = write_site.get("source") or write_site.get("path") if write_site else "write site"
    w_ln = f" (line {write_site.get('line')})" if write_site and write_site.get("line") else ""
    r_src = read_site.get("source") or read_site.get("path") if read_site else "read site"
    r_ln = f" (line {read_site.get('line')})" if read_site and read_site.get("line") else ""

    q_info = ""
    if query_site:
        q_src = query_site.get("source") or query_site.get("path") or ""
        q_ln = f" (line {query_site.get('line')})" if query_site.get("line") else ""
        if q_src:
            q_info = f", and queried with exact match at {q_src}{q_ln}"

    status = "Verified" if is_verified else "Hypothesized"
    return (
        f"{status} causal mechanism: {property_name} set at {w_src}{w_ln} "
        f"differs from expected validation format at {r_src}{r_ln}{q_info}."
    )


def rank_key_operations(
    evidence_pool: List[Dict[str, Any]],
    consumer_call_chain: Optional[List[str]] = None,
    max_items: int = 5,
) -> List[Dict[str, Any]]:
    """Rank evidence items prioritizing those relevant to the consumer call chain or key roles."""
    if not evidence_pool:
        return []

    chain = set(consumer_call_chain or [])

    def score(item: Dict[str, Any]) -> int:
        pts = 0
        src = str(item.get("source") or item.get("path") or "").replace("\\", "/")
        if any(c in src for c in chain):
            pts += 10
        role = str(item.get("role") or "").lower()
        if role in ("query_site", "consumer_site", "producer_site", "symptom_site"):
            pts += 5
        if not is_trivial_line(item.get("matched_text") or item.get("snippet") or ""):
            pts += 2
        return pts

    ranked = sorted(evidence_pool, key=score, reverse=True)
    return ranked[:max_items]
