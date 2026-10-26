"""Generic context gathering for the software-issue investigator.

This module gathers repository evidence only.  It does not diagnose a root cause.
"""
from __future__ import annotations

import ast
import json
import logging
import re
import time
from collections import defaultdict
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from app.logging_config import sanitize_log_text

logger = logging.getLogger("app.graph.context_gathering")


MAX_BROAD_SEARCHES = 8
MAX_TARGETED_SEARCHES = 12
MAX_INITIAL_FILES_TO_READ = 10
MAX_TOTAL_FILES_TO_READ = 20
MAX_METADATA_FILES_TO_READ = 8
MAX_TRACE_DEPTH = 3
MAX_EVIDENCE = 30

IGNORED_DIRECTORIES = {".git", ".venv", "venv", "env", "node_modules", "__pycache__", ".next", "dist", "build", "coverage", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox"}
CODE_EXTENSIONS = {".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".go", ".rs", ".php", ".rb", ".cs", ".swift", ".kt", ".kts", ".vue", ".svelte", ".c", ".cpp", ".h", ".hpp"}
MANIFEST_DOC_NAMES = {"package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "requirements.txt", "pyproject.toml", "poetry.lock", "pom.xml", "build.gradle", "cargo.toml", "cargo.lock", "composer.lock", "gemfile.lock", "go.mod", "go.sum", "readme.md", "readme", "dockerfile", "docker-compose.yml", "docker-compose.yaml"}
LOCKFILE_NAMES = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "cargo.lock", "composer.lock", "gemfile.lock", "go.sum"}
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


from app.agents.diagnosis_guard import is_trivial_line

GENERIC_QUERY_WORDS = {
    "mismatch", "match", "matches", "stored", "system", "associated",
    "issue", "problem", "error", "defect", "bug", "feature", "code",
    "file", "files", "function", "service", "model", "controller",
    "handler", "request", "response", "status", "data", "value",
    "user", "users", "app", "application", "project", "repo",
    "profile", "login", "opening", "returns",
    "symptom_site", "sign_site", "consumer_site", "query_site",
    "guard_condition", "transformation", "verification_probe",
    "producer_site", "symptom_emit",
    # Prompt instruction and boilerplate tokens to avoid noisy searches
    "investigate", "investigation", "investigator", "investigators",
    "identify", "identifying", "identified",
    "additional", "developer", "context",
    "trace", "tracing", "traced",
    "verify", "verifying", "verification",
    "report", "reported", "reporting",
    "findings", "finding",
    "taskflow", "relevant", "source", "please",
    "actual", "between", "differ", "differs", "difference",
    "check", "inspect", "examine",
    "note", "notes", "step", "steps", "task", "tasks", "summary",
    "probe", "hypothesis", "hypotheses", "unverified",
}


SYNTAX_KEYWORDS = {
    "import", "export", "default", "from", "require", "package", "module",
    "const", "let", "var", "def", "async", "await", "function", "class",
    "return", "throw", "raise", "catch", "try", "finally", "except",
    "if", "else", "elif", "for", "while", "switch", "case", "break",
    "true", "false", "null", "none", "undefined", "self", "this",
    "typeof", "instanceof", "void", "new", "delete", "in", "of",
    "http", "https", "localhost", "header", "headers", "status",
    "json", "object", "array", "string", "number", "boolean",
    "print", "console", "log", "logger", "error", "warn", "info",
    "args", "kwargs", "params", "body", "query", "payload",
    "content", "length", "type", "name", "value", "values", "data",
    "item", "items", "list", "dict", "str", "int", "float", "bool",
}


def _clean_token(t: str) -> str:
    cleaned = re.sub(r"^[^\w$]+|[^\w$]+$", "", str(t).strip())
    cleaned = re.sub(r"\.+$", "", cleaned)
    return cleaned


def _is_clean_query_token(t: str) -> bool:
    t_clean = _clean_token(t)
    if not t_clean or len(t_clean) < 3:
        return False
    if " " in t_clean or "\t" in t_clean or "\n" in t_clean:
        return False
    low = t_clean.lower()
    if low in STOP_WORDS or low in GENERIC_QUERY_WORDS or low in SYNTAX_KEYWORDS:
        return False
    return True


def create_empty_gathered_context() -> Dict[str, Any]:
    return {
        "problem_understanding": {
            "problem_summary": "",
            "observed_behavior": "",
            "expected_behavior": "",
            "target_areas": [],
        },
        "investigation_concepts": [],
        "relevant_files": [],
        "file_contents": {},
        "technical_terms": [],
        "related_components": [],
        "frameworks_detected": [],
        "context_summary": "",
        "search_history": [],
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
        for key in ("results", "files", "items", "matches", "entries"):
            if isinstance(value.get(key), list): return [x if isinstance(x, dict) else {"match": _text(x)} for x in value[key]]
        return [value]
    if isinstance(value, list): return [x if isinstance(x, dict) else {"match": _text(x)} for x in value]
    return []


def _path_from_result(r: Dict[str, Any]) -> str:
    return _norm(r.get("path") or r.get("file") or r.get("source") or r.get("filename") or r.get("name") or r.get("match") or "")


def _match_from_result(r: Dict[str, Any]) -> str:
    # search_code tool returns {"path": ..., "line": int, "content": str}
    for k in ("content", "matched_text", "match", "snippet", "line_content", "text"):
        val = r.get(k)
        if val is not None and isinstance(val, str) and val.strip():
            return val.strip()
    return ""


def _line_from_result(r: Dict[str, Any]) -> Optional[int]:
    for k in ("line", "line_number", "lineno"):
        val = r.get(k)
        if val is not None:
            try:
                return int(val)
            except (TypeError, ValueError):
                pass
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
    words = re.findall(r"[A-Za-z][A-Za-z0-9_$]{2,}|\b\d{3}\b", text)
    cleaned = [_clean_token(w) for w in words]
    return _unique([w for w in cleaned if _is_clean_query_token(w)])


def _symptom_terms(problem: Dict[str, Any]) -> List[str]:
    text = " ".join(_text(problem.get(k)) for k in ("problem_summary", "observed_behavior", "expected_behavior"))
    terms: List[str] = []
    terms.extend(STATUS_RE.findall(text))
    terms.extend([_clean_token(m.group(2)) for m in QUOTED_RE.finditer(text)])
    terms.extend([_clean_token(m.group(1)) for m in ROUTE_RE.finditer(text)])
    terms.extend([_clean_token(x) for x in re.findall(r"\b[A-Z][A-Za-z0-9_-]{2,}\b", text) if x.lower() not in STOP_WORDS])
    terms.extend(_problem_tokens(text))
    symptom_blocked = {"request", "response", "status", "authentication", "authorization", "login", "opening", "returns", "profile"}
    clean_terms = []
    for x in terms:
        cleaned = _clean_token(x)
        if _is_clean_query_token(cleaned) and cleaned.lower() not in symptom_blocked:
            clean_terms.append(cleaned)
    return _unique(clean_terms)


def identify_investigation_concepts(problem_understanding: Dict[str, Any], repository_summary: Dict[str, Any]) -> List[str]:
    text = " ".join(_text(problem_understanding.get(k)) for k in ("problem_summary", "observed_behavior", "expected_behavior")) + " " + " ".join(_text_list(problem_understanding.get("target_areas")))
    blocked = {"api", "http", "request", "response", "error", "status", "user", "page", "application", "project", "problem", "issue", "service", "model", "repository", "component"}
    concepts = []
    for phrase in ("authentication", "authorization", "login", "logout", "token", "session", "permission", "database", "query", "validation", "status code", "exception", "route", "profile"):
        if re.search(rf"\b{re.escape(phrase)}\b", text, re.I): concepts.append(phrase)
    for st in _symptom_terms(problem_understanding):
        cleaned_st = _clean_token(st)
        if _is_clean_query_token(cleaned_st):
            concepts.append(cleaned_st)
    concepts = [x for x in _unique(concepts) if x.lower() not in blocked and _is_clean_query_token(x)]
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


def _is_lockfile(p: str) -> bool:
    return _norm(p).rsplit("/", 1)[-1].lower() in LOCKFILE_NAMES


def _is_seed_or_script(p: str) -> bool:
    norm = _norm(p).lower()
    return any(part in norm for part in ("seed", "seeds", "scripts", "script", "fixtures", "fixture", "mock", "mocks"))


def _file_priority(path: str, problem_text: str = "") -> int:
    norm = _norm(path).lower()
    prob = str(problem_text or "").lower()
    parts = [p.lower() for p in PurePosixPath(norm).parts]
    filename = parts[-1] if parts else ""
    stem = PurePosixPath(filename).stem.lower()

    if _is_lockfile(path):
        return -150
    if _is_manifest_or_doc(path):
        return -80
    if _is_seed_or_script(path):
        return -60

    score = 0
    if _is_source(path):
        score += 20

    # Extract meaningful tokens from problem text (>= 3 chars, non-stopword)
    prob_tokens = [w for w in re.findall(r"[a-z0-9_$-]+", prob) if len(w) >= 3 and w not in STOP_WORDS and w not in GENERIC_QUERY_WORDS]

    # Exact filename or stem match in problem text
    if filename in prob and len(filename) >= 4:
        score += 30
    elif stem in prob and len(stem) >= 3:
        score += 25

    # Check path and filename token matches against problem
    for tok in set(prob_tokens):
        if tok in stem:
            score += 15
        elif any(tok in part for part in parts[:-1]):
            score += 6

    return score


def _rank_files(paths: Iterable[str], hits: List[Dict[str, Any]], symptom_terms: List[str], problem_text: str = "") -> List[str]:
    scores = defaultdict(int)
    hit_count = defaultdict(int)
    for r in hits:
        p = _path_from_result(r)
        if not p or _ignored(p): continue
        scores[p] += 5
        hit_count[p] += 1
    for p in paths:
        scores[p] += _file_priority(p, problem_text)
        low = p.lower()
        for t in symptom_terms:
            t_low = t.lower()
            if len(t_low) >= 3 and t_low in low:
                scores[p] += 4
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
    # Extract concrete observed error / status code from problem
    obs_text = " ".join([
        _text(problem.get("observed_behavior")),
        _text(problem.get("error")),
        _text(problem.get("actual_behavior")),
        _text(problem.get("problem_summary")),
    ]).lower()

    # 1. Observed HTTP status codes (e.g. 401, 403, 404, 500)
    status_codes = [int(c) for c in STATUS_RE.findall(obs_text)]
    if not status_codes and "unauthorized" in obs_text:
        status_codes.append(401)
    if not status_codes and "forbidden" in obs_text:
        status_codes.append(403)
    if not status_codes and "not found" in obs_text:
        status_codes.append(404)

    # 2. Specific thrown error patterns if stated
    error_patterns = []
    if "unauthorized" in obs_text:
        error_patterns.append(re.compile(r"\b(?:401|unauthorized)\b", re.I))
    for quoted in QUOTED_RE.finditer(obs_text):
        q_val = quoted.group(2).strip()
        if len(q_val) >= 4 and q_val.lower() not in STOP_WORDS:
            error_patterns.append(re.compile(rf"\b{re.escape(q_val)}\b", re.I))

    # If no concrete symptom behavior was reported in problem, report none
    if not status_codes and not error_patterns:
        return []

    sites = []
    for path, content in contents.items():
        if _is_manifest_or_doc(path) or _is_seed_or_script(path):
            continue
        lines = content.splitlines()
        for i, line in enumerate(lines, 1):
            s = line.strip()
            if is_trivial_line(s):
                continue

            emitted = False
            matched_term = None

            # A. Status code emission:
            for sc in status_codes:
                if re.search(rf"\b(?:status|sendStatus|statusCode|status_code|HttpError|HTTPException|abort|code)\s*(?:\.json\s*\(|===|==|=|\(|\:|,)\s*{sc}\b", s):
                    emitted = True
                    matched_term = str(sc)
                    break
                if re.search(rf"\b(?:res\.status\({sc}\)|HttpError\({sc}\)|HTTPException\([^)]*{sc}\))", s):
                    emitted = True
                    matched_term = str(sc)
                    break

            # B. Thrown error matching observed error:
            if not emitted and any(k in s for k in ("throw ", "raise ", "reject(")):
                for ep in error_patterns:
                    if ep.search(s):
                        emitted = True
                        matched_term = ep.pattern
                        break

            # C. UI error string emission:
            if not emitted and any(k in s for k in ("setError", "errorMessage", "<Alert", "<Toast", "alert(")):
                for ep in error_patterns:
                    if ep.search(s):
                        emitted = True
                        matched_term = ep.pattern
                        break

            if emitted:
                sites.append({
                    "source": path,
                    "line": i,
                    "matched_text": s,
                    "function": _enclosing_function(lines, i),
                    "kind": "symptom_site",
                    "matched_term": matched_term,
                    "role": "symptom_emit",
                })

    seen = set()
    out = []
    for s in sites:
        k = (s["source"], s["line"])
        if k not in seen:
            seen.add(k)
            out.append(s)
    return out[:10]


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


def _parse_file_symbols(content: str, path: str) -> Dict[str, Any]:
    """Parse functions, imports, and calls using AST for Python and language-aware regex for JS/TS."""
    functions: Dict[str, Tuple[int, int]] = {}
    imports: Dict[str, Tuple[str, str, int]] = {}  # local_symbol -> (module_ref, imported_name, line_no)
    calls: List[Tuple[int, str, Optional[str]]] = []  # (line_no, func_name, obj_name)

    ext = PurePosixPath(path).suffix.lower()

    if ext == ".py":
        try:
            tree = ast.parse(content)
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    functions[node.name] = (node.lineno, getattr(node, "end_lineno", node.lineno))
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        local_name = alias.asname or alias.name
                        imports[local_name] = (alias.name, alias.name, node.lineno)
                elif isinstance(node, ast.ImportFrom):
                    mod = node.module or ""
                    prefix = "." * node.level if node.level else ""
                    module_ref = f"{prefix}{mod}"
                    for alias in node.names:
                        local_name = alias.asname or alias.name
                        imports[local_name] = (module_ref, alias.name, node.lineno)
                elif isinstance(node, ast.Call):
                    lineno = getattr(node, "lineno", 1)
                    if isinstance(node.func, ast.Name):
                        calls.append((lineno, node.func.id, None))
                    elif isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
                        calls.append((lineno, node.func.attr, node.func.value.id))
            return {"functions": functions, "imports": imports, "calls": calls}
        except Exception:
            pass  # Fallback to regex below

    # Regex parser for JS/TS/other or failed Python AST
    lines = content.splitlines()
    fn_decl_re = re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(")
    arrow_fn_re = re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?\([^)]*\)\s*=>")
    py_def_re = re.compile(r"^\s*(?:async\s+def|def)\s+([A-Za-z_$][\w$]*)\s*\(")

    for i, line in enumerate(lines, 1):
        m = fn_decl_re.search(line) or arrow_fn_re.search(line) or py_def_re.search(line)
        if m:
            name = m.group(1)
            if name not in {"if", "for", "while", "switch", "catch"}:
                functions[name] = (i, min(len(lines), i + 40))

    import_named_re = re.compile(r"import\s+\{([^}]+)\}\s+from\s*['\"]([^'\"]+)['\"]")
    import_default_re = re.compile(r"import\s+(?:\*\s+as\s+)?([A-Za-z_$][\w$]*)\s+from\s*['\"]([^'\"]+)['\"]")
    require_named_re = re.compile(r"(?:const|let|var)\s+\{([^}]+)\}\s*=\s*require\s*\(\s*['\"]([^'\"]+)['\"]\s*\)")
    require_default_re = re.compile(r"(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*require\s*\(\s*['\"]([^'\"]+)['\"]\s*\)")

    for i, line in enumerate(lines, 1):
        for m in import_named_re.finditer(line):
            symbols = [s.strip().split(" as ")[-1].strip() for s in m.group(1).split(",") if s.strip()]
            mod = m.group(2)
            for s in symbols:
                imports[s] = (mod, s, i)
        for m in import_default_re.finditer(line):
            imports[m.group(1)] = (m.group(2), m.group(1), i)
        for m in require_named_re.finditer(line):
            symbols = [s.strip().split(" as ")[-1].strip() for s in m.group(1).split(",") if s.strip()]
            mod = m.group(2)
            for s in symbols:
                imports[s] = (mod, s, i)
        for m in require_default_re.finditer(line):
            imports[m.group(1)] = (m.group(2), m.group(1), i)

        # Calls
        for call_m in re.finditer(r"\b(?:([A-Za-z_$][\w$]*)\.)?([A-Za-z_$][\w$]*)\s*\(", line):
            obj = call_m.group(1)
            fn = call_m.group(2)
            if fn.lower() not in {"if", "for", "while", "switch", "catch", "return", "throw", "raise", "print", "str", "int", "len", "console", "require", "import"}:
                calls.append((i, fn, obj))

    return {"functions": functions, "imports": imports, "calls": calls}


def _resolve_import_to_path(source_path: str, module_ref: str, available_paths: Set[str]) -> Optional[str]:
    """Resolve an import string to an actual workspace path without basename guessing."""
    if module_ref.startswith("."):
        parent = PurePosixPath(source_path).parent
        clean_ref = module_ref
        dots = 0
        while clean_ref.startswith("."):
            dots += 1
            clean_ref = clean_ref[1:]

        target_dir = parent
        if dots > 1:
            for _ in range(dots - 1):
                target_dir = target_dir.parent

        rel_target = target_dir / clean_ref if clean_ref else target_dir
        base = _norm(str(rel_target))
        candidates = [
            base,
            base + ".js", base + ".ts", base + ".jsx", base + ".tsx", base + ".py",
            base + "/index.js", base + "/index.ts", base + "/__init__.py"
        ]
        for c in candidates:
            if c in available_paths:
                return c
        return None

    mod_as_path = module_ref.replace(".", "/")
    for p in available_paths:
        norm_p = _norm(p)
        if norm_p.endswith(mod_as_path) or any(norm_p.endswith(f"{mod_as_path}{ext}") for ext in (".py", ".js", ".ts", ".jsx", ".tsx", "/__init__.py", "/index.js", "/index.ts")):
            return norm_p

    return None


def _resolve_callee(
    contents: Dict[str, str],
    parsed_cache: Dict[str, Dict[str, Any]],
    caller_path: str,
    callee_name: str,
    obj_name: Optional[str] = None,
) -> Optional[Tuple[str, int, int, str]]:
    """Resolve a call target to (target_file, start_line, end_line, status).
    status is 'confirmed' (supported by local definition or import) or 'candidate' (uncertain cross-file match).
    """
    caller_data = parsed_cache.get(caller_path)
    if not caller_data:
        caller_data = _parse_file_symbols(contents.get(caller_path, ""), caller_path)
        parsed_cache[caller_path] = caller_data

    # 1. Local definition in caller file
    if callee_name in caller_data["functions"]:
        start, end = caller_data["functions"][callee_name]
        return caller_path, start, end, "confirmed"

    # 2. Check imports in caller file
    available_files = set(contents.keys())
    imported_target: Optional[str] = None

    imported_symbol: Optional[str] = None
    if obj_name and obj_name in caller_data["imports"]:
        mod_ref, imported_symbol, _ = caller_data["imports"][obj_name]
        imported_target = _resolve_import_to_path(caller_path, mod_ref, available_files)
    elif callee_name in caller_data["imports"]:
        mod_ref, imported_symbol, _ = caller_data["imports"][callee_name]
        imported_target = _resolve_import_to_path(caller_path, mod_ref, available_files)

    if imported_target and imported_target in contents:
        target_data = parsed_cache.get(imported_target)
        if not target_data:
            target_data = _parse_file_symbols(contents[imported_target], imported_target)
            parsed_cache[imported_target] = target_data

        # Respect imported aliases (e.g. `from .service import lookup as find_user`).
        # An import proves a module relationship, but it does not prove an arbitrary
        # function body or line range. Do not label the whole file as a confirmed call.
        target_symbol = imported_symbol or callee_name
        if target_symbol in target_data["functions"]:
            start, end = target_data["functions"][target_symbol]
            return imported_target, start, end, "confirmed"
        return None

    # 3. Candidate resolution across other files (NOT confirmed)
    candidates = []
    caller_parts = PurePosixPath(caller_path).parts
    for path, content in contents.items():
        if path == caller_path or not _is_source(path):
            continue
        p_data = parsed_cache.get(path)
        if not p_data:
            p_data = _parse_file_symbols(content, path)
            parsed_cache[path] = p_data
        if callee_name in p_data["functions"]:
            start, end = p_data["functions"][callee_name]
            target_parts = PurePosixPath(path).parts
            common_prefix_len = 0
            for a, b in zip(caller_parts, target_parts):
                if a == b:
                    common_prefix_len += 1
                else:
                    break
            candidates.append((common_prefix_len, path, start, end))

    if candidates:
        candidates.sort(key=lambda c: c[0], reverse=True)
        _, best_path, start, end = candidates[0]
        return best_path, start, end, "candidate"

    return None


def _function_definition(content: str, name: str) -> Optional[Tuple[int, int]]:
    parsed = _parse_file_symbols(content, "temp.py" if "def " in content else "temp.js")
    return parsed["functions"].get(name)


def _callee_names(text: str) -> List[str]:
    return [x for x in _unique(CALL_RE.findall(text)) if x.lower() not in {"if", "for", "while", "switch", "catch", "return", "raise", "print", "str", "int", "len", "console", "require", "import"}]


def _properties(text: str) -> List[str]:
    vals = []
    for m in PROPERTY_RE.finditer(text): vals.extend(x for x in m.groups() if x)
    return _unique(vals)


def _evidence_kind(line: str, default: str = "assignment") -> str:
    low = line.lower()
    if re.search(r"\b(?:select\s+.*?\s+from|insert\s+into|update\s+\w+\s+set|delete\s+from)\b|db\.prepare|db\.execute", low): return "query"
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
    """Generic light backward slice plus cross-boundary property pairing with candidate tracking."""
    evidence = []
    queue = [(p, ln, 0) for p, ln in seed_lines]
    seen = set()
    parsed_cache: Dict[str, Dict[str, Any]] = {}

    while queue:
        path, line_no, d = queue.pop(0)
        key = (path, line_no)
        if key in seen or d > depth: continue
        seen.add(key)
        content = contents.get(path, "")
        if not content:
            continue
        lines = content.splitlines()
        if not isinstance(line_no, int) or line_no < 1 or line_no > len(lines):
            continue
        line = _line_at(content, line_no)
        evidence.append({"source": path, "line": line_no, "matched_text": line, "kind": "symptom_site" if d == 0 else _evidence_kind(line), "flow_id": trace_id, "distance": d, "status": "confirmed"})
        guard = _guard_for_site(content, line_no) if d == 0 else None
        if guard:
            evidence.append({"source": path, "line": guard["source_line"], "matched_text": guard["matched_text"], "kind": "guard_condition", "flow_id": trace_id, "distance": d + 1, "status": "confirmed"})
            for ident in _identifiers_from_guard(guard["matched_text"]):
                ass = _assignment_for_identifier(lines, ident, guard["source_line"])
                if ass: queue.append((path, ass[0], d + 1))
        # For every identifier on a slice line, follow local assignment and called definitions.
        for ident in _identifiers_from_guard(line):
            ass = _assignment_for_identifier(lines, ident, line_no)
            if ass: queue.append((path, ass[0], d + 1))
        for callee in _callee_names(line):
            resolved = _resolve_callee(contents, parsed_cache, path, callee)
            if resolved:
                callee_path, start, end, status = resolved
                queue.append((callee_path, start, d + 1))
                callee_lines = contents[callee_path].splitlines()
                callee_text = callee_lines[start - 1].strip() if 1 <= start <= len(callee_lines) else ""
                evidence.append({
                    "source": callee_path,
                    "line": start,
                    "matched_text": callee_text,
                    "kind": "call" if status == "confirmed" else "candidate_call",
                    "status": status,
                    "uncertain": status == "candidate",
                    "flow_id": trace_id,
                    "distance": d + 1,
                })
                body_lines = list(range(start + 1, min(end, start + 8) + 1))
                query_lines = [i for i in range(start + 1, end + 1)
                               if re.search(r"\b(?:SELECT|INSERT|UPDATE|DELETE|query|execute|fetch|return|raise|throw)\b", callee_lines[i - 1], re.I)]
                for body_line in _unique([str(x) for x in query_lines + body_lines]):
                    queue.append((callee_path, int(body_line), d + 1))
        # Pair object properties across files.
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
                                evidence.append({"source": other_path, "line": idx, "matched_text": other_line.strip(), "kind": "sign_site", "flow_id": trace_id, "distance": d + 1, "status": "candidate", "uncertain": True})
                                break
    return evidence


def trace_symptom_flow(problem_understanding: Dict[str, Any], file_contents: Dict[str, str]) -> Dict[str, Any]:
    sites = _symptom_site_candidates(file_contents, problem_understanding)[:5]
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
                if is_trivial_line(line):
                    continue
                if re.search(r"\.(?:toLowerCase|toUpperCase|strip|trim|parseInt|parseFloat|json|loads|dumps|decode|encode)\s*\(", line):
                    evidence.append({"source": path, "line": ln, "matched_text": line.strip(), "kind": "transformation", "flow_id": flow_id, "distance": 3, "status": "confirmed"})
        # Keep ordered unique steps for UI (at most 12 steps, ranked by distance to symptom, no trivial lines)
        steps = []
        seen = set()
        flow_items = [x for x in evidence if x.get("flow_id") == flow_id]
        for e in sorted(flow_items, key=lambda x: (x.get("distance", 9), x.get("line", 0))):
            line_str = _line_at(file_contents.get(e["source"], ""), e["line"])
            if is_trivial_line(line_str):
                continue
            k = (e["source"], e["line"])
            if k not in seen:
                seen.add(k)
                steps.append({
                    "file": e["source"],
                    "line": e["line"],
                    "kind": e["kind"],
                    "status": e.get("status", "confirmed"),
                    "uncertain": e.get("uncertain", False),
                    "distance": e.get("distance", 9),
                })
            if len(steps) >= 12:
                break
        if steps:
            has_uncertain = any(s.get("uncertain") for s in steps)
            flows.append({
                "flow_id": flow_id,
                "symptom_site": {"file": site["source"], "line": site["line"]},
                "steps": steps,
                "status": "candidate" if has_uncertain else "confirmed",
            })
        if len(flows) >= 5:
            break
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
    available_files = set(contents.keys())
    parsed_cache = {p: _parse_file_symbols(c, p) for p, c in contents.items() if _is_source(p)}
    out = []

    for path, data in parsed_cache.items():
        # 1. Imports
        for local_sym, (mod_ref, orig_name, ln) in data["imports"].items():
            target_path = _resolve_import_to_path(path, mod_ref, available_files)
            if target_path and target_path != path:
                out.append({
                    "from": path,
                    "to": target_path,
                    "relationship": "imports",
                    "status": "confirmed",
                    "source_file": path,
                    "source_line": ln,
                    "symbol": orig_name,
                })

        # 2. Calls
        for ln, fn_name, obj_name in data["calls"]:
            resolved = _resolve_callee(contents, parsed_cache, path, fn_name, obj_name)
            if resolved:
                target_path, start, _, status = resolved
                if target_path != path:
                    out.append({
                        "from": path,
                        "to": target_path,
                        "relationship": "calls" if status == "confirmed" else "candidate_call",
                        "status": status,
                        "uncertain": status == "candidate",
                        "source_file": path,
                        "source_line": ln,
                        "symbol": fn_name,
                    })

    seen = set()
    clean = []
    for r in out:
        k = (r["from"], r["to"], r["relationship"], r.get("symbol"))
        if k not in seen:
            seen.add(k)
            clean.append(r)
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
        if not p or _is_manifest_or_doc(p) or _is_seed_or_script(p): continue
        ln = _line_from_result(r)
        if ln is None: continue
        line_text = _match_from_result(r)
        if is_trivial_line(line_text): continue
        out.append({"source": p, "line": ln, "matched_text": line_text, "kind": "symptom_site" if any(t.lower() in line_text.lower() for t in terms) else "call"})
    return out


def _build_relevant_files(paths: List[str], hits: List[Dict[str, Any]], contents: Dict[str, str], terms: List[str], problem_text: str = "") -> List[Dict[str, Any]]:
    hit_counts = defaultdict(int)
    for h in hits:
        p = _path_from_result(h)
        if p: hit_counts[p] += 1
    records = []
    prob_lower = str(problem_text or "").lower()
    for p in paths:
        is_seed = _is_seed_or_script(p)
        is_meta = _is_manifest_or_doc(p) or _is_lockfile(p)
        filename = _norm(p).rsplit("/", 1)[-1].lower()
        # Drop manifests, lockfiles, README, seed/scripts unless the problem concerns them
        if (is_meta or is_seed) and filename not in prob_lower:
            continue
        if p not in contents and hit_counts[p] == 0: continue
        score = hit_counts[p] * 2 + _file_priority(p, problem_text)
        if any(t.lower() in p.lower() for t in terms): score += 3
        records.append({
            "path": p,
            "relevance": "high" if score >= 8 else "medium" if score >= 3 else "low",
            "relevance_score": score,
            "matched_concepts": [t for t in terms if t.lower() in p.lower()],
            "search_hit_count": hit_counts[p],
        })
    records.sort(key=lambda x: x["relevance_score"], reverse=True)
    return records[:20]


def _implementation_context(contents: Dict[str, str], flows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    # One entry per (file, line) with a list of flow ids; no blank or brace-only lines
    entry_map: Dict[Tuple[str, int], Dict[str, Any]] = {}
    for flow in flows:
        fid = flow["flow_id"]
        for step in flow.get("steps", []):
            p, ln = step["file"], step["line"]
            line_text = _line_at(contents.get(p, ""), ln)
            if is_trivial_line(line_text):
                continue
            loc = (p, ln)
            if loc not in entry_map:
                entry_map[loc] = {
                    "file": p,
                    "line": ln,
                    "description": f"Line {ln}: {line_text}",
                    "flow_ids": [fid],
                }
            else:
                if fid not in entry_map[loc]["flow_ids"]:
                    entry_map[loc]["flow_ids"].append(fid)
    return list(entry_map.values())[:60]


def _summary(ctx: Dict[str, Any]) -> str:
    rel_count = len(ctx.get("relevant_files", []))
    concept_count = len(ctx.get("investigation_concepts", []))
    comp_count = len(ctx.get("related_components", []))
    frameworks = ctx.get("frameworks_detected", [])
    fw_str = f", frameworks: {', '.join(frameworks)}" if frameworks else ""
    return f"Context gathered: {rel_count} relevant file(s), {concept_count} concept(s), {comp_count} component(s){fw_str}."


def build_evidence_candidates(search_results: List[Dict[str, Any]], targeted_results: List[Dict[str, Any]], file_contents: Dict[str, str], problem_understanding: Dict[str, Any], trace_result: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    trace_result = trace_result or trace_symptom_flow(problem_understanding, file_contents)
    all_items = list(trace_result.get("evidence", [])) + _generic_evidence(search_results + targeted_results, file_contents, problem_understanding)

    # Detect client request interceptors, authorization header attachments, and client configuration
    for path, content in file_contents.items():
        if _is_manifest_or_doc(path) or _is_seed_or_script(path):
            continue
        lines = content.splitlines()
        for idx, line in enumerate(lines, 1):
            s = line.strip()
            if is_trivial_line(s):
                continue
            if re.search(
                r"\binterceptors(?:\.request|\.response)?\.use\b"
                r"|\b(?:config|request|req|client)\.headers\b"
                r"|\.headers(?:\.Authorization|\[['\"]Authorization['\"]\]|\.set|\.append)\b"
                r"|\bAuthorization\s*:\s*[`'\"]Bearer\b",
                s,
                re.IGNORECASE,
            ):
                all_items.append({
                    "source": path,
                    "line": idx,
                    "matched_text": s,
                    "kind": "client_interceptor",
                    "flow_id": "client-interceptor",
                    "distance": 1,
                })

    all_items = _dedup_evidence(all_items)
    symptom_files = {x["source"] for x in trace_result.get("symptom_sites", [])}
    all_items.sort(key=lambda e: _evidence_rank(e, symptom_files), reverse=True)
    # Filter trivial lines at candidate construction
    clean_items = [x for x in all_items if not is_trivial_line(x.get("matched_text"))]
    flow_items = [x for x in clean_items if x.get("flow_id")]
    generic = [x for x in clean_items if not x.get("flow_id")]
    # Enforce the evidence cap even when traced flows alone exceed the limit.
    if len(flow_items) >= MAX_EVIDENCE:
        return flow_items[:MAX_EVIDENCE]
    return flow_items + generic[:MAX_EVIDENCE - len(flow_items)]


def _extract_producer_consumer_pairs(contents: Dict[str, str], problem: Dict[str, Any], trace: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Emit producer/consumer pairs with neutral operations and data flow selection.

    producer = code that issues the credential used on the failing request
    consumer = first line that reads the property from decoded carrier and passes to lookup (not verify wrapper)
    query = query reached by following consumer's callee into definition
    rejected_candidates = other producers (e.g. register path) with reason
    """
    prob_text = " ".join([
        _text(problem.get("problem_summary")),
        _text(problem.get("observed_behavior")),
        _text(problem.get("expected_behavior")),
    ]).lower()

    # Only extract producer-consumer value transformation pairs when the problem context relates to
    # credential transformation, token claims, casing, or collation divergence
    is_casing_divergence_context = any(
        k in prob_text for k in (
            "casing", "capital", "normalization", "divergence", "case-sensitive", "nocase",
            "letter", "lowercase", "uppercase", "transform", "claim", "credential",
            "mismatch", "collation", "hash", "encoding", "token", "auth", "login",
            "unauthorized", "401", "403", "session", "jwt", "bearer", "ticket"
        )
    )

    if not is_casing_divergence_context:
        return [], []

    # Find candidate producers across read files
    producers = []
    for path, content in contents.items():
        if _is_manifest_or_doc(path) or _is_seed_or_script(path):
            continue
        lines = content.splitlines()
        for i, line in enumerate(lines, 1):
            s = line.strip()
            if is_trivial_line(s):
                continue
            # Look for signing / carrier issuance
            if re.search(r"\b(?:signToken|sign|mintTicket|issue|createToken|jwt\.sign|session\[)\w*\s*\(", s, re.I):
                if re.match(r"^\s*(?:export\s+)?(?:async\s+)?(?:function|def)\s+\w+", s):
                    continue
                fn_name = _enclosing_function(lines, i)
                props = re.findall(r"\b([A-Za-z_$][\w$]*)\s*:", s) or re.findall(r"(?:user|payload|record|data|entity)\.([A-Za-z_$][\w$]*)", s)
                fn_body = "\n".join(lines[max(0, i - 25):min(len(lines), i + 5)])
                ops = []
                if ".trim()" in fn_body or "strip()" in fn_body:
                    ops.append("trim")
                if ".toLowerCase()" in fn_body or ".lower()" in fn_body:
                    ops.append("toLowerCase")
                if not ops:
                    ops.append("raw")
                producers.append({
                    "source": path,
                    "line": i,
                    "matched_text": s,
                    "function": fn_name,
                    "property": props[0] if props else "",
                    "operations": ops,
                })

    # Find candidate consumers across read files
    consumers = []
    for path, content in contents.items():
        if _is_manifest_or_doc(path) or _is_seed_or_script(path):
            continue
        lines = content.splitlines()
        for i, line in enumerate(lines, 1):
            s = line.strip()
            if is_trivial_line(s):
                continue
            has_carrier_read = bool(re.search(r"\b(?:payload|decoded|claims|session)[\.\[]\s*['\"]?([A-Za-z_$][\w$]*)['\"]?", s))
            has_lookup_call = bool(re.search(r"\b(?:findBy|find|get|fetch|query|lookup)\w*\s*\(", s, re.I))
            if has_carrier_read and has_lookup_call and "verifyToken" not in s and "verify(" not in s:
                fn_name = _enclosing_function(lines, i)
                prop_m = re.search(r"\b(?:payload|decoded|claims|session)[\.\[]\s*['\"]?([A-Za-z_$][\w$]*)['\"]?", s)
                prop = prop_m.group(1) if prop_m else ""
                consumers.append({
                    "source": path,
                    "line": i,
                    "matched_text": s,
                    "function": fn_name,
                    "property": prop,
                    "operations": ["exact equality"],
                })

    # Find candidate queries across read files
    queries = []
    for path, content in contents.items():
        if _is_manifest_or_doc(path) or _is_seed_or_script(path):
            continue
        lines = content.splitlines()
        for i, line in enumerate(lines, 1):
            s = line.strip()
            if is_trivial_line(s):
                continue
            if re.search(r"\b(?:SELECT\s+.*?\s+FROM|INSERT\s+INTO|UPDATE\s+\w+\s+SET|DELETE\s+FROM)\b|db\.prepare|db\.execute", s, re.I):
                fn_name = _enclosing_function(lines, i)
                is_lower = "lower(" in s.lower()
                queries.append({
                    "source": path,
                    "line": i,
                    "matched_text": s,
                    "function": fn_name,
                    "operations": ["LOWER()"] if is_lower else ["exact equality"],
                })

    chosen_consumer = consumers[0] if consumers else None

    # Match query dynamically to consumer entity context without hardcoded assumptions
    chosen_query = None
    if chosen_consumer and queries:
        consumer_str = (chosen_consumer.get("matched_text", "") + " " + chosen_consumer.get("source", "")).lower()
        consumer_tokens = {w for w in re.findall(r"[A-Za-z0-9_]{3,}", consumer_str) if w not in STOP_WORDS and w not in GENERIC_QUERY_WORDS and w not in SYNTAX_KEYWORDS}
        scored_queries = []
        for q in queries:
            q_str = (q.get("matched_text", "") + " " + q.get("source", "")).lower()
            q_tokens = {w for w in re.findall(r"[A-Za-z0-9_]{3,}", q_str) if w not in STOP_WORDS and w not in GENERIC_QUERY_WORDS and w not in SYNTAX_KEYWORDS}
            overlap = len(consumer_tokens & q_tokens)
            scored_queries.append((overlap, q))
        scored_queries.sort(key=lambda x: x[0], reverse=True)
        chosen_query = scored_queries[0][1] if scored_queries else None

    chosen_producer = None
    rejected_producers = []

    if len(producers) > 1:
        diverging = []
        matching = []
        q_ops = chosen_query.get("operations", ["exact equality"]) if chosen_query else ["exact equality"]
        for prod in producers:
            p_ops = prod.get("operations", ["toLowerCase"])
            diverges = (
                ("toLowerCase" in p_ops and "LOWER()" not in q_ops and "toLowerCase" not in q_ops)
                or ("raw" in p_ops and ("LOWER()" in q_ops or "toLowerCase" in q_ops))
                or ("trim" in p_ops and "toLowerCase" in q_ops)
            )
            fn_low = prod.get("function", "").lower()
            in_problem = any(word in prob_text for word in re.findall(r"\w+", fn_low) if len(word) > 3)
            if diverges:
                diverging.append((prod, in_problem))
            else:
                matching.append((prod, in_problem))

        if len(diverging) == 1:
            chosen_producer = diverging[0][0]
            rejected_producers = [
                {**p[0], "reason": f"Producer on {p[0].get('function') or 'alternative'} path normalizes consistently with query; does not diverge."}
                for p in matching
            ]
        elif len(diverging) > 1:
            chosen_producer = next((p[0] for p in diverging if p[1]), diverging[0][0])
            rejected_producers = [
                {**p, "reason": f"Credential issued on {p.get('function') or 'alternative'} path; failing request uses credential issued during {chosen_producer.get('function')}."}
                for p in producers if p != chosen_producer
            ]
        else:
            for prod in producers:
                fn_low = prod.get("function", "").lower()
                if any(word in prob_text for word in re.findall(r"\w+", fn_low) if len(word) > 3) or any(k in fn_low for k in ("login", "signin", "auth")):
                    chosen_producer = prod
                    break
            if not chosen_producer:
                chosen_producer = producers[0]
            rejected_producers = [
                {**p, "reason": f"Credential issued on {p.get('function') or 'alternative'} path; failing request uses credential issued during {chosen_producer.get('function')}."}
                for p in producers if p != chosen_producer
            ]
    elif producers:
        chosen_producer = producers[0]

    chosen_pairs = []
    if chosen_producer and chosen_consumer:
        prop_resolved = chosen_producer.get("property") or chosen_consumer.get("property") or ""
        chosen_pairs.append({
            "producer": chosen_producer.get("matched_text", ""),
            "writer": chosen_producer.get("matched_text", ""),
            "producer_file": chosen_producer.get("source", ""),
            "producer_line": chosen_producer.get("line"),
            "producer_operations": chosen_producer.get("operations", ["toLowerCase"]),
            "consumer": chosen_consumer.get("matched_text", ""),
            "reader": chosen_consumer.get("matched_text", ""),
            "consumer_file": chosen_consumer.get("source", ""),
            "consumer_line": chosen_consumer.get("line"),
            "consumer_operations": chosen_consumer.get("operations", ["exact equality"]),
            "query": chosen_query.get("matched_text", "") if chosen_query else "",
            "query_file": chosen_query.get("source", "") if chosen_query else "",
            "query_line": chosen_query.get("line") if chosen_query else None,
            "query_operations": chosen_query.get("operations", ["exact equality"]) if chosen_query else ["exact equality"],
            "property": prop_resolved,
        })

    return chosen_pairs, rejected_producers


def _derive_focused_targeted_queries(
    problem: Dict[str, Any],
    contents: Dict[str, str],
    symptom_sites: List[Dict[str, Any]],
    searched_queries: Set[str],
) -> List[str]:
    """Derive focused search queries from problem context and candidate symptom code without broad token dumps."""
    candidate_queries: List[str] = []

    for ta in problem.get("target_areas", []):
        for part in re.findall(r"[A-Za-z0-9_$-]{3,}", str(ta)):
            if _is_clean_query_token(part) and part.lower() not in searched_queries:
                candidate_queries.append(part)

    obs = " ".join([_text(problem.get("problem_summary")), _text(problem.get("observed_behavior")), _text(problem.get("expected_behavior"))])
    for quoted in QUOTED_RE.finditer(obs):
        q_val = quoted.group(2).strip()
        if _is_clean_query_token(q_val) and q_val.lower() not in searched_queries:
            candidate_queries.append(q_val)

    for route in ROUTE_RE.finditer(obs):
        for seg in route.group(1).split("/"):
            if _is_clean_query_token(seg) and seg.lower() not in searched_queries:
                candidate_queries.append(seg)

    for site in symptom_sites:
        fn = site.get("function")
        if fn and _is_clean_query_token(fn) and fn.lower() not in searched_queries:
            candidate_queries.append(fn)
        matched_text = site.get("matched_text", "")
        for ident in _identifiers_from_guard(matched_text):
            if _is_clean_query_token(ident) and ident.lower() not in searched_queries:
                candidate_queries.append(ident)
        for callee in _callee_names(matched_text):
            if _is_clean_query_token(callee) and callee.lower() not in searched_queries:
                candidate_queries.append(callee)

    out = []
    seen = set()
    for q in candidate_queries:
        low = q.lower()
        if low not in seen and low not in searched_queries:
            seen.add(low)
            out.append(q)
    return out[:MAX_TARGETED_SEARCHES]


def gather_context(validated_problem_context: Dict[str, Any], repository_summary: Dict[str, Any], workspace: Any, *, list_files_tool: Any, search_code_tool: Any, read_file_tool: Any) -> Dict[str, Any]:
    start_time = time.time()
    if not validated_problem_context: raise ValueError("validated_problem_context is required.")
    if not repository_summary: raise ValueError("repository_summary is required.")
    if workspace is None: raise ValueError("workspace is required.")
    out = create_empty_gathered_context()
    problem = understand_problem(validated_problem_context)
    out["problem_understanding"] = problem
    prob_text = " ".join([problem.get("problem_summary", ""), problem.get("observed_behavior", ""), problem.get("expected_behavior", "")])

    session_id = validated_problem_context.get("session_id", "session")
    logger.info(
        "[ContextGathering] context_gathering_start session_id=%s workspace=%s problem='%s'",
        session_id,
        sanitize_log_text(str(workspace), max_len=100),
        sanitize_log_text(problem.get("problem_summary") or prob_text, max_len=120),
    )

    concepts = identify_investigation_concepts(problem, repository_summary)
    out["investigation_concepts"] = concepts
    logger.debug("[ContextGathering] Extracted %d concepts: %s", len(concepts), concepts[:5])
    history = out["search_history"]
    try: paths = _list_files(workspace, list_files_tool)
    except Exception as exc:
        logger.warning("[ContextGathering] Failed to list files from workspace: %s", exc)
        paths = []
    symptom_terms = _symptom_terms(problem)

    # Broad queries: single tokens, strip punctuation, no generic words
    broad = [_clean_token(q) for q in _broad_queries(problem, concepts) if _is_clean_query_token(q)]
    searched_queries: set[str] = set()
    broad_hits = []
    for q in broad:
        searched_queries.add(q.lower())
        broad_hits.extend(_search_with_fallback(workspace, q, search_code_tool, history, "broad"))
    logger.debug("[ContextGathering] Broad searches: %d queries executed, %d hits found", len(broad), len(broad_hits))

    ranked = _rank_files(paths, broad_hits, symptom_terms, prob_text)
    to_read = ranked[:MAX_INITIAL_FILES_TO_READ]
    contents = {}
    for p in to_read:
        c = _read(workspace, p, read_file_tool)
        if c: contents[p] = c
    # Read additional files hit by search until enough material.
    for p in ranked[MAX_INITIAL_FILES_TO_READ:MAX_TOTAL_FILES_TO_READ]:
        if p not in contents:
            c = _read(workspace, p, read_file_tool)
            if c: contents[p] = c

    # Bounded dependency manifests
    metadata_paths = [
        p for p in paths
        if PurePosixPath(_norm(p)).name.lower() in DEPENDENCY_MANIFEST_NAMES
    ]
    for p in metadata_paths[:MAX_METADATA_FILES_TO_READ]:
        if p not in contents:
            c = _read(workspace, p, read_file_tool)
            if c:
                contents[p] = c

    # Focused targeted searches derived from problem context and preliminary symptom candidates
    candidate_sites = _symptom_site_candidates(contents, problem)
    focused_queries = _derive_focused_targeted_queries(problem, contents, candidate_sites, searched_queries)
    targeted_hits = []
    for q in focused_queries:
        searched_queries.add(q.lower())
        targeted_hits.extend(_search_with_fallback(workspace, q, search_code_tool, history, "targeted"))
    for r in targeted_hits:
        p = _path_from_result(r)
        if p and p not in contents and not _is_manifest_or_doc(p) and not _is_seed_or_script(p) and len(contents) < MAX_TOTAL_FILES_TO_READ:
            c = _read(workspace, p, read_file_tool)
            if c: contents[p] = c

    out["file_contents"] = contents
    out["relevant_files"] = _build_relevant_files(paths, broad_hits + targeted_hits, contents, symptom_terms, prob_text)

    # Technical terms: single tokens, no internal labels, no generic words
    terms = []
    for p, c in contents.items():
        if _is_manifest_or_doc(p) or _is_seed_or_script(p):
            continue
        for token in re.findall(r"\b(?:[A-Za-z_$][A-Za-z0-9_$]*|\d{3})\b", c):
            tok_clean = _clean_token(token)
            if _is_clean_query_token(tok_clean):
                if tok_clean in symptom_terms or any(t.lower() == tok_clean.lower() for t in symptom_terms):
                    terms.append(tok_clean)
    for st in symptom_terms:
        st_clean = _clean_token(st)
        if _is_clean_query_token(st_clean):
            terms.append(st_clean)
    out["technical_terms"] = [t for t in _unique(terms) if _is_clean_query_token(t)][:30]

    # Components extracted purely from source file definitions (functions / classes)
    comps = []
    for rf in out["relevant_files"][:8]:
        p = rf.get("path") if isinstance(rf, dict) else str(rf)
        c = contents.get(p, "")
        if c and _is_source(p):
            parsed = _parse_file_symbols(c, p)
            comps.extend(parsed.get("functions", {}).keys())
    out["related_components"] = _unique(comps)[:20]

    out["frameworks_detected"] = _frameworks_from_manifests(contents)
    out["context_summary"] = _summary(out)

    # Context Quality / Completeness Gate evaluation (pure evaluation, no candidate evidence or flow construction)
    quality_gate = evaluate_gathered_context_quality(out, workspace=workspace)
    out["context_status"] = quality_gate["context_status"]
    out["context_completeness"] = quality_gate

    total_hits = len(broad_hits) + len(targeted_hits)
    logger.info(
        "[ContextGathering] scan_statistics scanned_files=%d relevant_files=%d queries_executed=%d total_hits=%d",
        len(paths),
        len(out["relevant_files"]),
        len(searched_queries),
        total_hits,
    )
    if quality_gate["is_sufficient"]:
        if len(out["relevant_files"]) == 0:
            logger.warning(
                "[ContextGathering] completeness_gate_result status=sufficient note='zero initial hits; broader exploration permitted' reason='%s'",
                quality_gate["reason"],
            )
        else:
            logger.info(
                "[ContextGathering] completeness_gate_result status=sufficient reason='%s'",
                quality_gate["reason"],
            )
    else:
        logger.error(
            "[ContextGathering] completeness_gate_result status=incomplete reason='%s'",
            quality_gate["reason"],
        )

    elapsed = time.time() - start_time
    logger.info(
        "[ContextGathering] context_gathering_complete session_id=%s duration=%.2fs status=%s relevant_files=%d",
        session_id,
        elapsed,
        out["context_status"],
        len(out["relevant_files"]),
    )
    return out


def evaluate_gathered_context_quality(
    gathered_context: Dict[str, Any],
    workspace: Optional[Any] = None,
    *args: Any,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Completeness gate validating whether problem input and workspace are usable for planning.

    Distinguishes unusable input from incomplete initial search results.
    Does not produce evidence candidates, trace flows, hypotheses, unresolved questions, or evidence gaps.
    """
    prob = gathered_context.get("problem_understanding") or {}
    prob_text = " ".join(filter(None, [
        _text(prob.get("problem_summary")),
        _text(prob.get("observed_behavior")),
        _text(prob.get("expected_behavior")),
        _text(prob.get("problem_description")),
        _text(gathered_context.get("problem")),
    ])).strip()

    # Check 1: Input Usability - problem text must be non-empty
    if not prob_text:
        return {
            "is_sufficient": False,
            "context_status": "incomplete",
            "reason": "Unusable input: problem description or workspace path missing.",
        }

    # Check 1b: Input Usability - workspace directory check
    ws = workspace or gathered_context.get("workspace") or gathered_context.get("workspace_path")
    if ws is not None:
        try:
            ws_path = Path(str(ws))
            if not ws_path.exists():
                return {
                    "is_sufficient": False,
                    "context_status": "incomplete",
                    "reason": "Unusable input: problem description or workspace path missing.",
                }
        except Exception:
            return {
                "is_sufficient": False,
                "context_status": "incomplete",
                "reason": "Unusable input: problem description or workspace path missing.",
            }
    elif not gathered_context.get("relevant_files") and not gathered_context.get("search_history"):
        return {
            "is_sufficient": False,
            "context_status": "incomplete",
            "reason": "Unusable input: problem description or workspace path missing.",
        }

    # Check 2: Initial Search Completeness
    relevant_files = gathered_context.get("relevant_files") or []
    if len(relevant_files) == 0:
        return {
            "is_sufficient": True,
            "context_status": "sufficient",
            "reason": "Valid problem input and repository structure found; initial keyword hits incomplete, enabling broader exploration in planning.",
        }

    return {
        "is_sufficient": True,
        "context_status": "sufficient",
        "reason": f"Gathered context sufficient with {len(relevant_files)} relevant file(s) identified.",
    }


def context_gathering_node(state: Dict[str, Any], *, list_files_tool: Any, search_code_tool: Any, read_file_tool: Any) -> Dict[str, Any]:
    logger.info("[ContextGatheringNode] Executing context gathering node")
    validated = state.get("validated_problem_context")
    repo = state.get("repository_summary")
    workspace = state.get("workspace") or state.get("workspace_path")
    if not validated:
        logger.error("[ContextGatheringNode] validated_problem_context is missing from state.")
        raise ValueError("validated_problem_context is missing from state.")
    if not repo:
        logger.error("[ContextGatheringNode] repository_summary is missing from state.")
        raise ValueError("repository_summary is missing from state.")
    if not workspace:
        logger.error("[ContextGatheringNode] workspace is missing from state.")
        raise ValueError("workspace is missing from state.")
    gathered = gather_context(validated, repo, workspace, list_files_tool=list_files_tool, search_code_tool=search_code_tool, read_file_tool=read_file_tool)
    logger.info(
        "[ContextGatheringNode] Completed. Status: %s, concepts: %d, files: %d",
        gathered.get("context_status"),
        len(gathered.get("investigation_concepts", [])),
        len(gathered.get("relevant_files", [])),
    )
    return {
        "gathered_context": gathered,
        "context_status": gathered.get("context_status"),
        "context_completeness": gathered.get("context_completeness"),
        "current_step": "context_gathered",
    }

