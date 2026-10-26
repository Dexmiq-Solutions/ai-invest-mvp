"""Target resolution helpers for investigation tasks."""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def resolve_consumer_call_targets(
    gathered_context: Optional[Dict[str, Any]] = None,
    problem: str = "",
) -> List[str]:
    """Resolve consumer call targets from context and problem, prioritized by relevance."""
    targets: List[str] = []
    ctx = gathered_context or {}

    pairs = ctx.get("producer_consumer_pairs") or []
    for pair in pairs:
        if isinstance(pair, dict) and pair.get("consumer_file"):
            c_file = str(pair["consumer_file"])
            if c_file not in targets:
                targets.append(c_file)

    p_words = {w.lower() for w in problem.split() if len(w) > 3}

    scored_files: List[tuple[str, int]] = []
    for rf in ctx.get("relevant_files") or []:
        path = rf.get("path") if isinstance(rf, dict) else str(rf) if rf else ""
        if not path or path in targets or path.startswith("<"):
            continue
        p_low = path.lower()
        if any(ign in p_low for ign in ("lock", "seed", "fixture", "test", ".md", ".json")):
            continue
        score = sum(2 for w in p_words if w in p_low)
        scored_files.append((path, score))

    scored_files.sort(key=lambda x: x[1], reverse=True)
    for p, _ in scored_files:
        targets.append(p)

    return targets[:10]
