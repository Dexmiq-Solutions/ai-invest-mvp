"""Evidence validation and classification helpers."""

from __future__ import annotations

from pathlib import Path, PurePosixPath
import re
from typing import Any, Dict, List, Optional, Tuple


def is_import_or_plumbing(snippet: str) -> bool:
    """Check if code snippet is merely an import or module plumbing line."""
    if not snippet:
        return False
    s = snippet.strip()
    if re.match(r"^(?:import\s+|from\s+\S+\s+import\s+|const\s+\w+\s*=\s*require\(|require\(|export\s+|package\s+|include\s+)", s):
        return True
    return False


def is_observation_statement(statement: str, rationale: Optional[str] = None) -> bool:
    """Check whether a finding statement is merely an observation of existing code rather than a proven defect."""
    if not statement:
        return True
    s = statement.lower()
    obs_signals = (
        "directly observed",
        "source contains",
        "file contains",
        "function is defined",
        "route is defined",
        "import statement found",
        "presence alone does not prove",
        "observed in file",
        "inspected ",
        "identified ",
        "relevant code statement",
    )
    if any(sig in s for sig in obs_signals):
        return True

    r = (rationale or "").lower()
    if "presence alone does not prove a defect" in r or "directly observed source text" in r:
        return True

    return False


def is_causal_evidence(item: Dict[str, Any]) -> bool:
    """Determine whether an evidence item represents potential causal evidence rather than trivial plumbing."""
    if not isinstance(item, dict):
        return False

    if item.get("role") == "observation":
        return False

    snippet = str(item.get("matched_text") or item.get("snippet") or "").strip()
    if not snippet or is_import_or_plumbing(snippet):
        return False

    # Check for trivial content
    if len(snippet) <= 3:
        return False

    return True


def validate_source_file_and_snippet(
    workspace_path: str,
    source_path: str,
    excerpt: str,
    start_line: Optional[int] = None,
) -> Tuple[bool, str, Optional[int]]:
    """Verify that source_path exists within workspace_path and contains excerpt.

    Returns (is_valid, reason, resolved_line_number).
    """
    if not workspace_path or not source_path:
        return False, "Workspace or source path missing", None

    try:
        ws = Path(workspace_path).resolve()
        candidate = (ws / source_path).resolve()
        candidate.relative_to(ws)
    except Exception as exc:
        return False, f"Path traversal or resolution error: {exc}", None

    if not candidate.exists() or not candidate.is_file():
        return False, f"File does not exist: {source_path}", None

    try:
        content = candidate.read_text(encoding="utf-8", errors="ignore")
    except Exception as exc:
        return False, f"File unreadable: {exc}", None

    if not excerpt or not excerpt.strip():
        return True, "File verified", start_line

    clean_excerpt = excerpt.strip()
    lines = content.splitlines()

    for i, line in enumerate(lines, 1):
        if clean_excerpt in line:
            return True, "Snippet verified", i

    # Fuzzy check: line matches excerpt stripped
    for i, line in enumerate(lines, 1):
        if clean_excerpt.lower() in line.lower():
            return True, "Fuzzy snippet match", i

    return False, "Snippet not found in file", None


def is_causal_defect_finding(finding: Dict[str, Any], evidence_pool: List[Dict[str, Any]]) -> bool:
    """Determine whether a finding represents a causal defect backed by evidence."""
    if not isinstance(finding, dict):
        return False

    category = str(finding.get("category", "")).lower()
    if "observation" in category or "unknown" in category:
        return False

    statement = str(finding.get("statement") or finding.get("finding") or "").lower()
    if is_observation_statement(statement, finding.get("rationale")):
        return False

    # Check if there is attached evidence
    ev_ids = finding.get("evidence_ids") or []
    if ev_ids:
        for ev in evidence_pool:
            if ev.get("evidence_id") in ev_ids and is_causal_evidence(ev):
                return True
        return False

    return "possible" in category or "confirmed" in category
