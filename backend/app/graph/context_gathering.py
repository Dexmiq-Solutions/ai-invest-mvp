"""Generic context gathering for the software-issue investigator.

This module gathers repository evidence only.  It does not diagnose a root cause.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import PurePosixPath
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

MAX_BROAD_SEARCHES = 8
MAX_TARGETED_SEARCHES = 12
MAX_INITIAL_FILES_TO_READ = 10
MAX_TOTAL_FILES_TO_READ = 20
MAX_METADATA_FILES_TO_READ = 8
MAX_TRACE_DEPTH = 3
MAX_EVIDENCE = 30

IGNORED_DIRECTORIES = {".git", ".venv", "venv", "env", "node_modules", "__pycache__", ".next", "dist", "build", "coverage", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox"}
CODE_EXTENSIONS = {".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".go", ".rs", ".php", ".rb", ".cs", ".swift", ".kt", ".kts", ".vue", ".svelte", ".c", ".cpp", ".h", ".hpp"}
MANIFEST_DOC_NAMES = {"package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "requirements.txt", "pyproject.toml", "poetry.lock", "pom.xml", "build.gradle", "cargo.toml", "go.mod", "readme.md", "readme", "dockerfile", "docker-compose.yml", "docker-compose.yaml"}
# Only these files are needed for dependency detection. Lockfiles and docs are
# intentionally not loaded just to identify frameworks/dependencies.
DEPENDENCY_MANIFEST_NAMES = {"package.json", "requirements.txt", "pyproject.toml", "pom.xml", "build.gradle", "cargo.toml", "go.mod"}

STOP_WORDS = {"the", "and", "that", "this", "with", "from", "after", "before", "when", "then", "into", "have", "has", "been", "being", "does", "did", "not", "but", "for", "are", "was", "were", "its", "page", "application", "project", "problem", "issue", "error", "working", "works", "using", "used", "should", "would", "could", "where", "which", "what", "how", "why", "their", "there", "they", "them", "some", "also", "only", "than", "while", "through", "instead", "expected", "observed", "returns", "return", "opening", "open", "my", "your", "our", "an", "a", "to", "of", "in", "on", "is", "it", "as"}

# Generic patterns only.  Nothing here names a project-specific function/file/SQL expression.
STATUS_RE = re.compile(r"\b(?:1|2|3|4|5)\d{2}\b")
QUOTED_RE = re.compile(r"(['\"])(.{1,120}?)\1")
ROUTE_RE = re.compile(r"[\"'`](/(?:[A-Za-z0-9_:\-{}.$]+/?)+)[\"'`]")
IDENT_RE = re.compile(r"\b[A-Za-z_$][A-Za-z0-9_$]*\b")
CALL_RE = re.compile(r"\b([A-Za-z_$][A-Za-z0-9_$]*)\s*\(")
PROPERTY_RE = re.compile(r"(?:\.([A-Za-z_$][A-Za-z0-9_$]*)|[\"']([A-Za-z_$][A-Za-z0-9_$]*)[\"']\s*:|[\"']([A-Za-z_$][A-Za-z0-9_$]*)[\"']\s*[,}\]])")


def create_empty_gathered_context() -> Dict[str, Any]:
    return {
        "problem_understanding": {"problem_summary": "", "observed_behavior": "", "expected_behavior": "", "target_areas": []},
        "investigation_concepts": [], "relevant_files": [], "technical_terms": [],
        "related_components": [], "relationships": [], "implementation_context": [],
        "evidence_candidates": [], "search_history": [], "context_summary": "",
        # Extended context fields used by the Investigation Planner and dashboard.
        "trace_flows": [], "symptom_sites": [], "frameworks_detected": [],
        "context_status": "incomplete",
    }


def _text(v: Any) -> str:
    if v is None: return ""
    return v if isinstance(v, str) else str(v)


def _norm(p: str) -> str:
    return _text(p).replace("\\", "/").strip("/")


def _ignored(p: str) -> bool:
    return bool(set(_norm(p).split("/")) & IGNORED_DIRECTORIES)


def _is_manifest_or_doc(p: str) -> bool:
    return _norm(p).rsplit("/", 1)[-1].lower() in MANIFEST_DOC_NAMES


def _is_source(p: str) -> bool:
    return PurePosixPath(_norm(p)).suffix.lower() in CODE_EXTENSIONS


def _unique(xs: Iterable[str]) -> List[str]:
    out, seen = [], set()
    for x in xs:
        x = _text(x).strip()
        if x and x.lower() not in seen:
            out.append(x); seen.add(x.lower())
    return out


def _line_at(content: str, line: int) -> str:
    lines = content.splitlines()
    return lines[line - 1].strip() if 1 <= line <= len(lines) else ""


def _call_tool(fn: Any, *args: Any, **kwargs: Any) -> Any:
    return fn(*args, **kwargs)


def _result_items(value: Any) -> List[Dict[str, Any]]:
    if value is None: return []
    if isinstance(value, dict):
        for key in ("results", "files", "items", "matches"):
            if isinstance(value.get(key), list): return [x if isinstance(x, dict) else {"match": _text(x)} for x in value[key]]
        return [value]
    if isinstance(value, list): return [x if isinstance(x, dict) else {"match": _text(x)} for x in value]
    return []


def _path_from_result(r: Dict[str, Any]) -> str:
    return _norm(r.get("path") or r.get("file") or r.get("source") or r.get("filename") or "")


def _match_from_result(r: Dict[str, Any]) -> str:
    return _text(r.get("match") or r.get("matched_text") or r.get("line") or r.get("text") or r.get("snippet") or "").strip()


def _line_from_result(r: Dict[str, Any]) -> Optional[int]:
    for k in ("line", "line_number", "lineno"):
        try:
            if r.get(k) is not None: return int(r[k])
        except (TypeError, ValueError): pass
    return None


def _problem_values(ctx: Dict[str, Any]) -> Tuple[str, str, str, List[str]]:
    return (_text(ctx.get("problem_summary") or ctx.get("problem_description") or ctx.get("problem")),
            _text(ctx.get("observed_behavior") or ctx.get("actual_behavior")),
            _text(ctx.get("expected_behavior")),
            [*_text_list(ctx.get("target_areas"))])


def _text_list(v: Any) -> List[str]:
    if isinstance(v, list): return [_text(x).strip() for x in v if _text(x).strip()]
    return [_text(v).strip()] if _text(v).strip() else []


def understand_problem(ctx: Dict[str, Any]) -> Dict[str, Any]:
    p, observed, expected, targets = _problem_values(ctx)
    return {"problem_summary": p, "observed_behavior": observed, "expected_behavior": expected, "target_areas": targets}


def _problem_tokens(text: str) -> List[str]:
    words = re.findall(r"[A-Za-z][A-Za-z0-9_$.-]{2,}|\b\d{3}\b", text)
    return _unique([w for w in words if w.lower() not in STOP_WORDS])


def _symptom_terms(problem: Dict[str, Any]) -> List[str]:
    text = " ".join(_text(problem.get(k)) for k in ("problem_summary", "observed_behavior", "expected_behavior"))
    terms: List[str] = []
    terms.extend(STATUS_RE.findall(text))
    terms.extend([m.group(2) for m in QUOTED_RE.finditer(text)])
    terms.extend([m.group(1) for m in ROUTE_RE.finditer(text)])
    terms.extend([x for x in re.findall(r"\b[A-Z][A-Za-z0-9_-]{2,}\b", text) if x.lower() not in STOP_WORDS])
    terms.extend(_problem_tokens(text))
    # Generic control-flow nouns are useful search concepts but are too broad
    # to identify a symptom-producing line by themselves.
    symptom_blocked = {"request", "response", "status", "authentication", "authorization", "login", "opening", "returns", "profile"}
    # Keep profile only when it appears as a route/quoted/UI literal; generic
    # problem wording should not turn every function declaration into a site.
    terms = [x for x in terms if x.lower() not in {"request", "response", "status", "authentication", "authorization", "login", "opening", "returns"}]
    return _unique(terms)


def identify_investigation_concepts(problem_understanding: Dict[str, Any], repository_summary: Dict[str, Any]) -> List[str]:
    text = " ".join(_text(problem_understanding.get(k)) for k in ("problem_summary", "observed_behavior", "expected_behavior")) + " " + " ".join(_text_list(problem_understanding.get("target_areas")))
    blocked = {"api", "http", "request", "response", "error", "status", "user", "page", "application", "project", "problem", "issue", "service", "model", "repository", "component"}
    # Keep multi-word phrases only as concepts; search logic later decomposes them.
    concepts = []
    for phrase in ("authentication", "authorization", "login", "logout", "token", "session", "permission", "database", "query", "validation", "status code", "exception", "request", "response", "route", "profile"):
        if re.search(rf"\b{re.escape(phrase)}\b", text, re.I): concepts.append(phrase)
    concepts.extend(_symptom_terms(problem_understanding))
    concepts = [x for x in _unique(concepts) if x.lower() not in blocked]
    return concepts[:20]


def _broad_queries(problem: Dict[str, Any], concepts: List[str]) -> List[str]:
    # Search single tokens, plus literal symptom terms.  Multi-word concepts are split.
    terms: List[str] = []
    for c in concepts:
        parts = re.findall(r"[A-Za-z0-9_/$:-]+", c)
        terms.extend(parts if len(parts) > 1 else [c])
    terms.extend(_symptom_terms(problem))
    return _unique(terms)[:MAX_BROAD_SEARCHES]


def _fallback_words(query: str) -> List[str]:
    parts = re.findall(r"[A-Za-z0-9_/$:-]+", query)
    return _unique(parts)


def _search_with_fallback(workspace: Any, query: str, search_tool: Any, history: List[Dict[str, Any]], stage: str) -> List[Dict[str, Any]]:
    try:
        raw = _call_tool(search_tool, workspace, query)
        results = _result_items(raw)
    except Exception as exc:
        history.append({"query": query, "stage": stage, "status": "failed", "error": type(exc).__name__})
        return []
    history.append({"query": query, "stage": stage, "status": "completed", "result_count": len(results)})
    if results or " " not in query.strip(): return results
    fallback = _fallback_words(query)
    if len(fallback) <= 1: return results
    for word in fallback:
        try:
            raw2 = _call_tool(search_tool, workspace, word)
            r2 = _result_items(raw2)
            history.append({"query": word, "stage": f"{stage}_fallback", "status": "completed", "result_count": len(r2)})
            results.extend(r2)
        except Exception as exc:
            history.append({"query": word, "stage": f"{stage}_fallback", "status": "failed", "error": type(exc).__name__})
    return results


def _list_files(workspace: Any, fn: Any) -> List[str]:
    try: raw = _call_tool(fn, workspace)
    except Exception: return []
    out = []
    for r in _result_items(raw):
        p = _path_from_result(r) or _text(r.get("name"))
        if p and not _ignored(p): out.append(p)
    return _unique(out)


def _read(workspace: Any, path: str, fn: Any) -> str:
    try:
        raw = _call_tool(fn, workspace, path)
    except Exception:
        return ""
    if isinstance(raw, str): return raw[:50000]
    if isinstance(raw, dict): return _text(raw.get("content") or raw.get("text") or raw.get("data"))[:50000]
    return _text(raw)[:50000]


def _file_priority(path: str) -> int:
    if _is_manifest_or_doc(path): return -20
    if _is_source(path): return 10
    return 0


def _rank_files(paths: Iterable[str], hits: List[Dict[str, Any]], symptom_terms: List[str]) -> List[str]:
    scores = defaultdict(int)
    hit_count = defaultdict(int)
    for r in hits:
        p = _path_from_result(r)
        if not p or _ignored(p): continue
        scores[p] += 2
        hit_count[p] += 1
    for p in paths:
        scores[p] += _file_priority(p)
        low = p.lower()
        for t in symptom_terms:
            if t.lower() in low: scores[p] += 4
    return sorted(scores, key=lambda p: (scores[p], hit_count[p], -len(p)), reverse=True)


def _enclosing_function(lines: List[str], line_no: int) -> str:
    start = max(0, line_no - 1)
    patterns = [r"\b(?:def|async\s+def)\s+([A-Za-z_$][A-Za-z0-9_$]*)", r"\b(?:function)\s+([A-Za-z_$][A-Za-z0-9_$]*)", r"\b(?:const|let|var)\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*(?:async\s*)?\([^)]*\)\s*=>", r"\bclass\s+([A-Za-z_$][A-Za-z0-9_$]*)"]
    for i in range(start, -1, -1):
        for pat in patterns:
            m = re.search(pat, lines[i])
            if m: return m.group(1)
    return ""


def _status_line(line: str) -> bool:
    return bool(STATUS_RE.search(line) or re.search(r"\b(?:sendStatus|HTTPException|HttpResponse|abort|raise|throw)\b", line, re.I))


def _symptom_site_candidates(contents: Dict[str, str], problem: Dict[str, Any]) -> List[Dict[str, Any]]:
    terms = _symptom_terms(problem)
    sites = []
    for path, content in contents.items():
        if _is_manifest_or_doc(path): continue
        lines = content.splitlines()
        for i, line in enumerate(lines, 1):
            lower = line.lower()
            hit = _status_line(line)
            matched = None
            for term in terms:
                if term and term.lower() in lower:
                    hit = True; matched = term; break
            if not hit: continue
            # Avoid treating ordinary declarations as symptom sites unless a symptom literal/status/error construct is present.
            sites.append({"source": path, "line": i, "matched_text": line.strip(), "function": _enclosing_function(lines, i), "kind": "symptom_site", "matched_term": matched})
    # de-duplicate exact sites
    seen = set(); out = []
    for s in sites:
        k = (s["source"], s["line"])
        if k not in seen: seen.add(k); out.append(s)
    return out[:20]


def _guard_for_site(content: str, line_no: int) -> Optional[Dict[str, Any]]:
    lines = content.splitlines()
    for i in range(line_no - 2, max(-1, line_no - 12), -1):
        line = lines[i].strip()
        if re.match(r"^(if|elif|else\s+if)\b", line) or re.search(r"\bcatch\s*\(", line) or "?" in line:
            return {"source_line": i + 1, "matched_text": line, "kind": "guard_condition"}
    return None


def _assignment_for_identifier(lines: List[str], ident: str, before: int) -> Optional[Tuple[int, str]]:
    pats = [rf"\b(?:const|let|var)\s+{re.escape(ident)}\s*=", rf"\b{re.escape(ident)}\s*=", rf"\b{re.escape(ident)}\s*:=", rf"\b{re.escape(ident)}\s*="]
    for i in range(before - 1, max(-1, before - 30), -1):
        if any(re.search(p, lines[i]) for p in pats): return i + 1, lines[i].strip()
    return None


def _identifiers_from_guard(text: str) -> List[str]:
    reserved = {"if", "else", "elif", "catch", "true", "false", "null", "None", "return", "throw", "raise", "status", "HTTPException"}
    return [x for x in _unique(IDENT_RE.findall(text)) if x not in reserved and not x.isdigit()]


def _function_definition(content: str, name: str) -> Optional[Tuple[int, int]]:
    lines = content.splitlines()
    pat = re.compile(rf"\b(?:def|function)\s+{re.escape(name)}\s*\(")
    start = None
    for i, line in enumerate(lines):
        if pat.search(line): start = i; break
    if start is None: return None
    base_indent = len(lines[start]) - len(lines[start].lstrip())
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if lines[j].strip() and len(lines[j]) - len(lines[j].lstrip()) <= base_indent and re.match(r"\s*(?:def|class|function|export\s+(?:async\s+)?function)\b", lines[j]):
            end = j; break
    return start + 1, end


def _callee_names(text: str) -> List[str]:
    return [x for x in _unique(CALL_RE.findall(text)) if x.lower() not in {"if", "for", "while", "switch", "catch", "return", "raise", "print", "str", "int", "len"}]


def _properties(text: str) -> List[str]:
    vals = []
    for m in PROPERTY_RE.finditer(text): vals.extend(x for x in m.groups() if x)
    return _unique(vals)


def _evidence_kind(line: str, default: str = "assignment") -> str:
    low = line.lower()
    if re.search(r"\b(?:select|insert|update|delete|query|execute)\b", low): return "query"
    if re.search(r"\b(?:sign|encode|serialize|dump)\s*\(", low): return "sign_site"
    if re.search(r"\b(?:verify|decode|deserialize|load)\s*\(", low): return "verify_site"
    if CALL_RE.search(line): return "call"
    return default


def _function_definition_global(contents: Dict[str, str], name: str) -> Optional[Tuple[str, int, int]]:
    for path, content in contents.items():
        loc = _function_definition(content, name)
        if loc:
            return path, loc[0], loc[1]
    return None


def _producer_consumer_evidence(contents: Dict[str, str], trace_id: str, seed_lines: List[Tuple[str, int]], depth: int = 3) -> List[Dict[str, Any]]:
    """Generic light backward slice plus cross-boundary property pairing."""
    evidence = []
    queue = [(p, ln, 0) for p, ln in seed_lines]
    seen = set()
    while queue:
        path, line_no, d = queue.pop(0)
        key = (path, line_no)
        if key in seen or d > depth: continue
        seen.add(key)
        content = contents.get(path, "")
        if not content: continue
        lines = content.splitlines()
        line = _line_at(content, line_no)
        evidence.append({"source": path, "line": line_no, "matched_text": line, "kind": "symptom_site" if d == 0 else _evidence_kind(line), "flow_id": trace_id, "distance": d})
        guard = _guard_for_site(content, line_no) if d == 0 else None
        if guard:
            evidence.append({"source": path, "line": guard["source_line"], "matched_text": guard["matched_text"], "kind": "guard_condition", "flow_id": trace_id, "distance": d + 1})
            for ident in _identifiers_from_guard(guard["matched_text"]):
                ass = _assignment_for_identifier(lines, ident, guard["source_line"])
                if ass: queue.append((path, ass[0], d + 1))
        # For every identifier on a slice line, follow local assignment and called definitions.
        for ident in _identifiers_from_guard(line):
            ass = _assignment_for_identifier(lines, ident, line_no)
            if ass: queue.append((path, ass[0], d + 1))
        for callee in _callee_names(line):
            found = _function_definition_global(contents, callee)
            if found:
                callee_path, start, end = found
                queue.append((callee_path, start, d + 1))
                callee_lines = contents[callee_path].splitlines()
                body_lines = list(range(start + 1, min(end, start + 8) + 1))
                query_lines = [i for i in range(start + 1, end + 1)
                               if re.search(r"\b(?:SELECT|INSERT|UPDATE|DELETE|query|execute|fetch|return|raise|throw)\b", callee_lines[i - 1], re.I)]
                for body_line in _unique([str(x) for x in query_lines + body_lines]):
                    queue.append((callee_path, int(body_line), d + 1))
        # Pair object properties across files.  This is structural, not name-specific.
        props = _properties(line)
        if props:
            for other_path, other_content in contents.items():
                if other_path == path: continue
                for prop in props:
                    for idx, other_line in enumerate(other_content.splitlines(), 1):
                        if re.search(rf"(?:[\"']{re.escape(prop)}[\"']\s*:|\. {re.escape(prop)}\b|\.{re.escape(prop)}\b)", other_line):
                            producer_shape = bool(re.search(r"\b(?:sign|encode|serialize|pack|create)\w*\s*\(", other_line, re.I) and "{" in other_line)
                            boundary_words = any(k in other_line.lower() for k in ("sign", "encode", "serialize", "insert", "create", "payload", "token", "session"))
                            if producer_shape or boundary_words:
                                evidence.append({"source": other_path, "line": idx, "matched_text": other_line.strip(), "kind": "sign_site", "flow_id": trace_id, "distance": d + 1})
                                break
    return evidence


def trace_symptom_flow(problem_understanding: Dict[str, Any], file_contents: Dict[str, str]) -> Dict[str, Any]:
    sites = _symptom_site_candidates(file_contents, problem_understanding)
    evidence: List[Dict[str, Any]] = []
    flows: List[Dict[str, Any]] = []
    for idx, site in enumerate(sites):
        flow_id = f"flow-{idx + 1}"
        items = _producer_consumer_evidence(file_contents, flow_id, [(site["source"], site["line"])], MAX_TRACE_DEPTH)
        evidence.extend(items)
        # Add neutral transformations found on the traced source files.
        traced_files = {x["source"] for x in items}
        for path in traced_files:
            for ln, line in enumerate(file_contents[path].splitlines(), 1):
                if re.search(r"\.(?:toLowerCase|toUpperCase|strip|trim|parseInt|parseFloat|json|loads|dumps|decode|encode)\s*\(", line):
                    evidence.append({"source": path, "line": ln, "matched_text": line.strip(), "kind": "transformation", "flow_id": flow_id, "distance": 3})
        # Keep ordered unique steps for UI.
        steps = []
        seen = set()
        for e in sorted([x for x in evidence if x.get("flow_id") == flow_id], key=lambda x: (x.get("distance", 9), x.get("line", 0))):
            k = (e["source"], e["line"])
            if k not in seen:
                seen.add(k); steps.append({"file": e["source"], "line": e["line"], "kind": e["kind"]})
        if steps:
            flows.append({"flow_id": flow_id, "symptom_site": {"file": site["source"], "line": site["line"]}, "steps": steps})
            evidence.extend([])
    # A no-site result is deliberate; no synthetic flow is created.
    return {"symptom_sites": sites, "evidence": evidence, "flows": flows}


def _frameworks_from_manifests(file_contents: Dict[str, str]) -> List[str]:
    deps: Set[str] = set()
    for path, content in file_contents.items():
        if path.lower().endswith("package.json"):
            try:
                data = json.loads(content)
                for section in ("dependencies", "devDependencies", "peerDependencies"):
                    deps.update((data.get(section) or {}).keys())
            except Exception: pass
        if path.lower().endswith(("requirements.txt", "pyproject.toml")):
            for name in re.findall(r"(?im)^\s*([A-Za-z][A-Za-z0-9_.-]+)(?:\s*[<>=!~]|\s*$)", content): deps.add(name.lower())
    return _unique(sorted(deps))


def _relationships(contents: Dict[str, str]) -> List[Dict[str, Any]]:
    existing = set(contents)
    basename = {PurePosixPath(p).name: p for p in existing}
    out = []
    for path, content in contents.items():
        if not _is_source(path): continue
        for m in re.finditer(r"(?:import\s+.*?\s+from|require\s*\(|from)\s*[\"']([^\"']+)[\"']", content):
            target = m.group(1)
            if not target.startswith("."): continue
            base = _norm(str(PurePosixPath(path).parent / target))
            candidates = [base, base + ".js", base + ".ts", base + ".jsx", base + ".tsx", base + ".py", base + "/index.js", base + "/index.ts", base + "/__init__.py"]
            resolved = next((x for x in candidates if x in existing), None)
            if resolved: out.append({"from": path, "relationship": "imports", "to": resolved, "source_file": path})
    seen = set(); clean = []
    for x in out:
        k = (x["from"], x["to"])
        if k not in seen: seen.add(k); clean.append(x)
    return clean


def _evidence_rank(e: Dict[str, Any], symptom_files: Set[str]) -> Tuple[int, int, int, int]:
    flow = 100 if e.get("flow_id") else 0
    distance = max(0, 30 - int(e.get("distance", 9)))
    source = 10 if _is_source(e.get("source", "")) else 0
    nonmeta = 10 if not _is_manifest_or_doc(e.get("source", "")) else -30
    symptom = 20 if e.get("source") in symptom_files else 0
    return flow + symptom + distance + source + nonmeta, flow, distance, source


def _dedup_evidence(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set(); out = []
    for x in items:
        k = (x.get("source"), x.get("line"), x.get("kind"), x.get("matched_text"))
        if k not in seen: seen.add(k); out.append(x)
    return out


def _generic_evidence(search_results: List[Dict[str, Any]], contents: Dict[str, str], problem: Dict[str, Any]) -> List[Dict[str, Any]]:
    terms = _symptom_terms(problem)
    out = []
    for r in search_results:
        p = _path_from_result(r)
        if not p or _is_manifest_or_doc(p): continue
        ln = _line_from_result(r)
        if ln is None: continue
        out.append({"source": p, "line": ln, "matched_text": _match_from_result(r), "kind": "symptom_site" if any(t.lower() in _match_from_result(r).lower() for t in terms) else "call"})
    return out


def _build_relevant_files(paths: List[str], hits: List[Dict[str, Any]], contents: Dict[str, str], terms: List[str]) -> List[Dict[str, Any]]:
    hit_counts = defaultdict(int)
    for h in hits:
        p = _path_from_result(h)
        if p: hit_counts[p] += 1
    records = []
    for p in paths:
        # Manifests/docs can inform dependency metadata, but should not crowd
        # the source files shown to the investigator as relevant evidence.
        if _is_manifest_or_doc(p):
            continue
        if p not in contents and hit_counts[p] == 0: continue
        score = hit_counts[p] * 2 + _file_priority(p)
        if any(t.lower() in p.lower() for t in terms): score += 3
        records.append({"path": p, "relevance": "high" if score >= 8 else "medium" if score >= 3 else "low", "relevance_score": score, "matched_concepts": [t for t in terms if t.lower() in p.lower()], "search_hit_count": hit_counts[p]})
    records.sort(key=lambda x: x["relevance_score"], reverse=True)
    return records[:20]


def _implementation_context(contents: Dict[str, str], flows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for flow in flows:
        for step in flow["steps"]:
            p, ln = step["file"], step["line"]
            out.append({"file": p, "description": f"Line {ln}: {_line_at(contents.get(p, ''), ln)}", "flow_id": flow["flow_id"]})
    return out[:60]


def _summary(ctx: Dict[str, Any]) -> str:
    sites = ctx.get("symptom_sites", [])
    flows = ctx.get("trace_flows", [])
    files = []
    for f in flows:
        files.append(f["flow_id"] + ": " + " -> ".join(f"{s['file']}:{s['line']}" for s in f["steps"]))
    return f"Found {len(sites)} symptom site(s) and {len(flows)} traced flow(s)." + (" Flows: " + " | ".join(files) + "." if files else "")


def build_evidence_candidates(search_results: List[Dict[str, Any]], targeted_results: List[Dict[str, Any]], file_contents: Dict[str, str], problem_understanding: Dict[str, Any], trace_result: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    trace_result = trace_result or trace_symptom_flow(problem_understanding, file_contents)
    all_items = list(trace_result.get("evidence", [])) + _generic_evidence(search_results + targeted_results, file_contents, problem_understanding)
    all_items = _dedup_evidence(all_items)
    symptom_files = {x["source"] for x in trace_result.get("symptom_sites", [])}
    all_items.sort(key=lambda e: _evidence_rank(e, symptom_files), reverse=True)
    # Never drop flow steps for generic matches: reserve all flow evidence first, then fill remaining slots.
    flow_items = [x for x in all_items if x.get("flow_id")]
    generic = [x for x in all_items if not x.get("flow_id")]
    if len(flow_items) >= MAX_EVIDENCE: return flow_items
    return flow_items + generic[:MAX_EVIDENCE - len(flow_items)]


def gather_context(validated_problem_context: Dict[str, Any], repository_summary: Dict[str, Any], workspace: Any, *, list_files_tool: Any, search_code_tool: Any, read_file_tool: Any) -> Dict[str, Any]:
    if not validated_problem_context: raise ValueError("validated_problem_context is required.")
    if not repository_summary: raise ValueError("repository_summary is required.")
    if workspace is None: raise ValueError("workspace is required.")
    out = create_empty_gathered_context()
    problem = understand_problem(validated_problem_context)
    out["problem_understanding"] = problem
    concepts = identify_investigation_concepts(problem, repository_summary)
    out["investigation_concepts"] = concepts
    history = out["search_history"]
    try: paths = _list_files(workspace, list_files_tool)
    except Exception: paths = []
    symptom_terms = _symptom_terms(problem)
    broad = _broad_queries(problem, concepts)
    broad_hits = []
    for q in broad:
        broad_hits.extend(_search_with_fallback(workspace, q, search_code_tool, history, "broad"))
    ranked = _rank_files(paths, broad_hits, symptom_terms)
    to_read = ranked[:MAX_INITIAL_FILES_TO_READ]
    contents = {}
    for p in to_read:
        c = _read(workspace, p, read_file_tool)
        if c: contents[p] = c
    # Read additional files hit by search until the trace has enough material.
    for p in ranked[MAX_INITIAL_FILES_TO_READ:MAX_TOTAL_FILES_TO_READ]:
        if p not in contents:
            c = _read(workspace, p, read_file_tool)
            if c: contents[p] = c
    # Read a bounded number of dependency manifests only. Do not load every
    # README/lockfile in a large monorepo: they are not symptom evidence and
    # can otherwise bypass MAX_TOTAL_FILES_TO_READ.
    metadata_paths = [
        p for p in paths
        if PurePosixPath(_norm(p)).name.lower() in DEPENDENCY_MANIFEST_NAMES
    ]
    for p in metadata_paths[:MAX_METADATA_FILES_TO_READ]:
        if p not in contents:
            c = _read(workspace, p, read_file_tool)
            if c:
                contents[p] = c
    # Symptom terms get one targeted search pass even if the broad search missed them.
    targeted_hits = []
    for q in _unique(symptom_terms)[:MAX_TARGETED_SEARCHES]:
        targeted_hits.extend(_search_with_fallback(workspace, q, search_code_tool, history, "targeted"))
    for r in targeted_hits:
        p = _path_from_result(r)
        if p and p not in contents and not _is_manifest_or_doc(p) and len(contents) < MAX_TOTAL_FILES_TO_READ:
            c = _read(workspace, p, read_file_tool)
            if c: contents[p] = c
    # A trace is intentionally based only on actual file contents.
    trace = trace_symptom_flow(problem, contents)
    out["relevant_files"] = _build_relevant_files(paths, broad_hits + targeted_hits, contents, symptom_terms)
    # Concrete identifiers/terms, derived from actual code and problem tokens.
    terms = []
    for p, c in contents.items():
        for token in re.findall(r"\b(?:[A-Za-z_$][A-Za-z0-9_$]*|\d{3})\b", c):
            if len(token) >= 3 and (token in symptom_terms or any(t.lower() == token.lower() for t in symptom_terms)):
                terms.append(token)
    terms.extend(symptom_terms)
    terms.extend([s["kind"] for s in trace.get("evidence", []) if s.get("kind") in {"guard_condition", "assignment", "call", "sign_site", "verify_site", "transformation", "query"}])
    out["technical_terms"] = _unique(terms)[:30]
    # Components are only named when they occur in traced source or relevant source files.
    comps = []
    for e in trace.get("evidence", []):
        fn = _enclosing_function(contents.get(e["source"], "").splitlines(), e["line"])
        if fn: comps.append(fn)
    out["related_components"] = _unique(comps)[:20]
    out["relationships"] = _relationships(contents)
    out["implementation_context"] = _implementation_context(contents, trace.get("flows", []))
    out["evidence_candidates"] = build_evidence_candidates(broad_hits, targeted_hits, contents, problem, trace)
    out["trace_flows"] = trace.get("flows", [])
    out["symptom_sites"] = trace.get("symptom_sites", [])
    out["frameworks_detected"] = _frameworks_from_manifests(contents)
    out["context_summary"] = _summary(out)
    # Contract rule: a context is not sufficient/complete without a symptom site.
    out["context_status"] = "sufficient" if trace.get("symptom_sites") else "incomplete"
    return out


def context_gathering_node(state: Dict[str, Any], *, list_files_tool: Any, search_code_tool: Any, read_file_tool: Any) -> Dict[str, Any]:
    validated = state.get("validated_problem_context")
    repo = state.get("repository_summary")
    workspace = state.get("workspace")
    if not validated: raise ValueError("validated_problem_context is missing from state.")
    if not repo: raise ValueError("repository_summary is missing from state.")
    if not workspace: raise ValueError("workspace is missing from state.")
    gathered = gather_context(validated, repo, workspace, list_files_tool=list_files_tool, search_code_tool=search_code_tool, read_file_tool=read_file_tool)
    return {"gathered_context": gathered, "current_step": "context_gathered"}
