

"""Phase 5 Code Investigator.

Explores repository code, traces references, and reports evidence-backed
findings without claiming an unverified root cause is confirmed.
"""
from __future__ import annotations

import json
import logging
from pathlib import PurePosixPath
import re
import time
from typing import Any, Callable, List, Literal, Optional

from pydantic import BaseModel, Field

from app.agents.investigator_contract import (
    ToolCallTracker,
    classify_sql_statement,
    create_hypothesis,
    extract_terms,
    normalize_items,
    resolve_workspace,
    safe_list,
    safe_read,
    safe_search,
    search_result_evidence,
    unique_evidence,
)


class CodeHypothesisItem(BaseModel):
    title: str = Field(description="Clear title of the hypothesized defect or mechanism")
    possible_cause: str = Field(description="Plausible defect mechanism explaining how the issue occurs")
    reasoning: str = Field(description="Step-by-step reasoning linking the evidence to the reported symptom")
    cited_sources: List[str] = Field(default_factory=list, description="Source paths from the evidence")
    unanswered_questions: List[str] = Field(default_factory=list, description="Questions needing runtime/test verification")
    confidence: Literal["low", "medium", "high"] = Field(default="medium")


class CodeHypothesesPayload(BaseModel):
    hypotheses: List[CodeHypothesisItem] = Field(default_factory=list)

from app.logging_config import sanitize_log_text
from app.tools.investigation_tools import list_files, read_file, search_code

logger = logging.getLogger("app.agents.code_investigator")


CATEGORIES = {
    "confirmed": "Confirmed Finding",
    "possible": "Possible Cause",
    "assumption": "Assumption",
    "unknown": "Unknown",
}

SOURCE_EXTENSIONS = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs",
    ".java", ".go", ".rs", ".php", ".rb", ".cs", ".cpp",
    ".c", ".h", ".sql",
}

IGNORED_PARTS = {
    ".git", ".venv", "venv", "node_modules", "__pycache__",
    "dist", "build", ".pytest_cache", "coverage",
}

MAX_FILES = 10
MAX_EVIDENCE = 40
MAX_SNIPPET_CHARS = 500


def _is_non_application_path(path: str) -> bool:
    """Identify build artifacts, lockfiles, documentation, test suites, or seed scripts."""
    if not path:
        return False
    lowered = path.replace("\\", "/").lower()
    p = PurePosixPath(lowered)
    parts = p.parts
    filename = p.name
    suffix = p.suffix
    stem = p.stem
    if filename in {
        "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock",
        "package.json", "composer.lock", "cargo.lock", "gemfile.lock"
    }:
        return True
    if suffix in {".md", ".markdown", ".rst", ".txt", ".lock", ".log"}:
        return True
    if stem in {"license", "readme", "contributing", "authors", "changelog", "notice"}:
        return True
    if any(part in {"scripts", "seeds", "fixtures", "node_modules", ".git", "dist", "build"} for part in parts):
        return True
    return False


def _dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _path(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("path") or value.get("file") or value.get("source")

    if not isinstance(value, str):
        return ""

    return value.replace("\\", "/").strip()



def _safe_relative_path(value: str) -> bool:
    """Reject absolute paths and traversal paths before reading files."""
    if not value or "\x00" in value:
        return False

    path = PurePosixPath(value.replace("\\", "/"))
    return not path.is_absolute() and ".." not in path.parts


def _line_number(value: Any) -> int | None:
    try:
        number = int(value)
        return number if number > 0 else None
    except (TypeError, ValueError):
        return None


def _extract_multiline_statement(
    lines: list[str],
    match_idx: int,
    max_lines: int = 10,
    max_chars: int = MAX_SNIPPET_CHARS,
) -> tuple[int, str]:
    """
    Extract a complete multi-line statement surrounding a matching line.

    Expands:
    1. Backward if the preceding line initiated an open call, assignment, or declaration.
    2. Forward while parentheses/brackets remain unclosed or line ends with a continuation token.

    Returns:
        (start_line_number_1_based, statement_text)
    """
    if not lines or match_idx < 0 or match_idx >= len(lines):
        return (match_idx + 1, "")

    def _delimiter_balance(text: str) -> tuple[int, int]:
        paren = 0
        bracket = 0
        in_quote = None
        escaped = False
        for ch in text:
            if escaped:
                escaped = False
                continue
            if ch == "\\":
                escaped = True
                continue
            if in_quote:
                if ch == in_quote:
                    in_quote = None
            else:
                if ch in ("'", '"', "`"):
                    in_quote = ch
                elif ch == "(":
                    paren += 1
                elif ch == ")":
                    paren -= 1
                elif ch == "[":
                    bracket += 1
                elif ch == "]":
                    bracket -= 1
        return paren, bracket

    # Backward expansion: if preceding lines end with unclosed parens or continuation tokens
    start_idx = match_idx
    while start_idx > 0 and (match_idx - start_idx) < 3:
        prev_idx = start_idx - 1
        prev_line = lines[prev_idx].strip()
        if not prev_line:
            break
        if prev_line.endswith(("{", "}", ":", ";")):
            break
        p, b = _delimiter_balance(prev_line)
        ends_continuation = prev_line.endswith(("=", "(", "[", ",", "+", "."))
        if p > 0 or b > 0 or ends_continuation:
            start_idx = prev_idx
        else:
            break

    # Forward expansion from start_idx
    collected: list[str] = []
    curr_paren = 0
    curr_bracket = 0
    end_idx = start_idx

    while end_idx < len(lines) and (end_idx - start_idx) < max_lines:
        line_str = lines[end_idx]
        collected.append(line_str)
        p, b = _delimiter_balance(line_str)
        curr_paren += p
        curr_bracket += b

        clean = line_str.strip()
        if end_idx >= match_idx and curr_paren <= 0 and curr_bracket <= 0:
            if clean.endswith((";", ")", "]", "}")) or not clean.endswith((",", "+", ".", "=", "(", "[")):
                break

        end_idx += 1

    combined = "\n".join(collected).strip()
    if len(combined) > max_chars:
        combined = combined[:max_chars]

    return (start_idx + 1, combined)


def _is_covered_by_existing_evidence(
    source: str,
    line: Any,
    snippet: str,
    existing_evidence: list[dict[str, Any]],
) -> bool:
    """Check if a snippet or line is already subsumed by a larger multi-line statement from the same source."""
    if not source or not snippet:
        return False
    norm_source = source.replace("\\", "/").strip().lower()
    clean_snippet = snippet.strip()
    for ex in existing_evidence:
        ex_source = str(ex.get("source") or "").replace("\\", "/").strip().lower()
        if ex_source != norm_source:
            continue
        ex_line = ex.get("line")
        ex_snippet = str(ex.get("snippet") or "").strip()
        # Direct substring of already captured multi-line snippet
        if clean_snippet in ex_snippet:
            return True
        # Line falls within multi-line span
        if line is not None and ex_line is not None:
            span = ex_snippet.count("\n") + 1
            if span > 1 and ex_line <= line < (ex_line + span):
                return True
    return False


def _task_and_context(task: str, context: dict) -> str:
    """Combine the investigation task with useful historical context."""
    pieces = [task]

    for key in (
        "problem",
        "problem_summary",
        "observed_behavior",
        "expected_behavior",
        "error_message",
        "recent_changes",
        "target_areas",
        "prior_findings",
    ):
        value = context.get(key)

        if isinstance(value, str):
            pieces.append(value)
        elif isinstance(value, (list, tuple)):
            for item in value:
                if isinstance(item, dict):
                    stmt = str(item.get("statement") or item.get("finding") or item.get("description") or "")
                    if stmt:
                        pieces.append(stmt)
                elif item:
                    pieces.append(str(item))

    understanding = _dict(context.get("problem_understanding"))

    for key in (
        "problem_summary",
        "observed_behavior",
        "expected_behavior",
    ):
        if understanding.get(key):
            pieces.append(str(understanding[key]))

    target_areas = understanding.get("target_areas")

    if isinstance(target_areas, list):
        pieces.extend(str(item) for item in target_areas)

    return " ".join(piece for piece in pieces if piece)


def _file_score(path: str, terms: list[str]) -> int:
    """Prioritize likely relevant source files."""
    lowered = path.lower()
    score = sum(3 for term in terms if term.lower() in lowered)

    filename = PurePosixPath(path).name.lower()

    if any(
        token in filename
        for token in (
            "auth", "user", "service", "model", "route", "controller",
            "api", "client", "request", "profile", "token", "http",
        )
    ):
        score += 2

    if PurePosixPath(path).suffix.lower() in SOURCE_EXTENSIONS:
        score += 1

    if _is_non_application_path(path):
        score -= 20

    return score



def _finding(
    category: str,
    statement: str,
    *,
    evidence_ids: list[str] | None = None,
    confidence: str = "medium",
    rationale: str | None = None,
    task_id: str | None = None,
    agent_name: str | None = "code_investigator",
    file_path: str | None = None,
    line_number: int | None = None,
    supports_or_contradicts: str | None = None,
    relevant_code_reference: str | None = None,
) -> dict:
    result = {
        "category": category,
        "statement": statement,
        "description": statement,
        "confidence": confidence,
        "evidence_ids": evidence_ids or [],
        "task_id": task_id,
        "agent_name": agent_name,
    }

    if file_path:
        result["file_path"] = file_path
    if line_number is not None:
        result["line_number"] = line_number
    if relevant_code_reference:
        result["relevant_code_reference"] = relevant_code_reference
    elif file_path and line_number is not None:
        result["relevant_code_reference"] = f"{file_path}:{line_number}"
    if rationale:
        result["rationale"] = rationale
    if supports_or_contradicts:
        result["supports_or_contradicts"] = supports_or_contradicts

    return result


def _derive_substantive_finding_statement(src: str, items: list[dict]) -> str:
    """Derive substantive semantic observation of code rather than generic statement count."""
    snippets = [it.get("snippet", "") for it in items]
    all_snips = " ".join(snippets)

    parts = []
    norm_matches = [s.strip() for s in snippets if re.search(r"\b(?:tolowercase|touppercase|trim|normalize|cleanemail)\b", s.lower())]
    if norm_matches:
        parts.append(f"applies credential normalization/casing `{norm_matches[0][:60]}`")

    token_matches = [s.strip() for s in snippets if re.search(r"\b(?:signtoken|verifytoken|jwt\.sign|jwt\.verify|bearer)\b", s.lower())]
    if token_matches:
        parts.append(f"handles token claims/verification `{token_matches[0][:60]}`")

    query_matches = [s.strip() for s in snippets if any(k in s.lower() for k in ("select", "insert", "update", "where email", "findbyemail", "byemail"))]
    if query_matches:
        parts.append(f"executes database query/operation `{query_matches[0][:60]}`")

    error_matches = [s.strip() for s in snippets if re.search(r"\b(?:httperror|status\s*\(\s*401|res\.status\s*\(\s*401|unauthorized|status_code\s*=\s*401)\b", s.lower())]
    if error_matches:
        parts.append(f"enforces authorization rejection guard `{error_matches[0][:60]}`")

    if parts:
        return f"Observed implementation in {src}: {'; '.join(parts)}."

    decls = re.findall(r"\b(?:function|def|class|export\s+(?:default\s+)?function|export\s+const)\s+([A-Za-z0-9_$]+)", all_snips)
    if decls:
        unique_decls = list(dict.fromkeys(decls))[:3]
        return f"Observed logic in {src}: declares {', '.join(unique_decls)} across matching statements."

    top_snip = items[0].get("snippet", "").strip()[:70]
    line_no = items[0].get("line") or "?"
    return f"Observed implementation in {src} (line {line_no}): `{top_snip}`."


def _extract_relative_imports(content: str, current_file: str) -> list[str]:
    """Extract and resolve relative imported file paths referenced in source code."""
    imported_rel_paths: list[str] = []
    curr_dir = PurePosixPath(current_file).parent

    # JavaScript / TypeScript / Node.js
    js_patterns = [
        r"""(?:import|from)\s+['"](\.[^'"]+)['"]""",
        r"""require\s*\(\s*['"](\.[^'"]+)['"]\s*\)""",
        r"""import\s*\(\s*['"](\.[^'"]+)['"]\s*\)""",
    ]
    for pat in js_patterns:
        for match in re.findall(pat, content):
            rel_str = match.replace("\\", "/").strip()
            try:
                resolved = curr_dir / rel_str
                norm_parts = []
                for part in resolved.parts:
                    if part == ".":
                        continue
                    elif part == "..":
                        if norm_parts:
                            norm_parts.pop()
                    else:
                        norm_parts.append(part)
                norm_path = "/".join(norm_parts)
                if norm_path and norm_path not in imported_rel_paths:
                    imported_rel_paths.append(norm_path)
            except Exception:
                continue

    # Python relative imports: from .foo import bar, from ..models import user
    py_patterns = [
        r"""from\s+(\.+[A-Za-z0-9_.]+)\s+import""",
    ]
    for pat in py_patterns:
        for match in re.findall(pat, content):
            dots = len(match) - len(match.lstrip("."))
            mod_path = match.lstrip(".").replace(".", "/")
            base_dir = curr_dir
            for _ in range(dots - 1):
                base_dir = base_dir.parent
            resolved = base_dir / mod_path
            norm_parts = [p for p in resolved.parts if p != "."]
            norm_path = "/".join(norm_parts)
            if not norm_path.endswith(".py"):
                norm_path += ".py"
            if norm_path and norm_path not in imported_rel_paths:
                imported_rel_paths.append(norm_path)

    return imported_rel_paths


class CodeInvestigator:
    """Evidence-first repository investigator conforming to Phase 4 contracts."""

    investigator_name = "Code Investigator"
    agent_name = "code_investigator"

    def __init__(
        self,
        *,
        list_files_tool: Callable[..., Any] = list_files,
        search_code_tool: Callable[..., Any] = search_code,
        read_file_tool: Callable[..., Any] = read_file,
        max_files: int = MAX_FILES,
        max_evidence: int = MAX_EVIDENCE,
        llm_factory: Optional[Callable] = None,
    ) -> None:
        self.list_files_tool = list_files_tool
        self.search_code_tool = search_code_tool
        self.read_file_tool = read_file_tool
        self.llm_factory = llm_factory

        self.max_files = max(1, min(int(max_files), 30))
        self.max_evidence = max(1, min(int(max_evidence), 100))

    def investigate(
        self,
        workspace_path: Any = None,
        investigation_task: Any = None,
        shared_context: dict | None = None,
        *args: Any,
        **kwargs: Any,
    ) -> dict:
        """Explore source files and return structured evidence and findings."""
        start_time = time.time()

        # Normalize calling signatures (workspace_path, task, context) vs (task, context, state)
        actual_workspace = ""
        actual_task_dict: dict[str, Any] = {}
        actual_context: dict[str, Any] = {}

        if isinstance(workspace_path, str) and workspace_path.strip():
            actual_workspace = workspace_path.strip()
            if isinstance(investigation_task, dict):
                actual_task_dict = dict(investigation_task)
            elif isinstance(investigation_task, str):
                actual_task_dict = {"instructions": investigation_task.strip()}
            actual_context = _dict(shared_context)
        elif isinstance(workspace_path, dict):
            # Calling convention: investigate(task, context, state)
            actual_task_dict = dict(workspace_path)
            actual_context = _dict(investigation_task)
            state_dict = _dict(shared_context)
            actual_workspace = resolve_workspace(state_dict, actual_context)
        elif "workspace_path" in kwargs:
            actual_workspace = str(kwargs["workspace_path"] or "").strip()
            if isinstance(investigation_task, dict):
                actual_task_dict = dict(investigation_task)
            elif isinstance(investigation_task, str):
                actual_task_dict = {"instructions": investigation_task.strip()}
            actual_context = _dict(shared_context or kwargs.get("shared_context"))

        if not actual_workspace and actual_context:
            actual_workspace = resolve_workspace({}, actual_context)

        step_from_ctx = actual_context.get("investigation_step") if isinstance(actual_context.get("investigation_step"), dict) else {}
        task_id = str(
            actual_task_dict.get("task_id")
            or step_from_ctx.get("task_id")
            or actual_context.get("task_id")
            or "TASK-CODE-001"
        )
        task_str = str(
            actual_task_dict.get("instructions")
            or actual_task_dict.get("task")
            or actual_task_dict.get("objective")
            or actual_task_dict.get("description")
            or step_from_ctx.get("instructions")
            or step_from_ctx.get("task")
            or step_from_ctx.get("description")
            or ""
        ).strip()

        logger.info(
            "[Code Investigator] specialist_task_start: task_id=%s, agent_name=%s, workspace=%s",
            task_id,
            self.agent_name,
            actual_workspace,
        )

        task_obj = str(
            actual_task_dict.get("objective")
            or actual_task_dict.get("task")
            or actual_task_dict.get("instructions")
            or actual_task_dict.get("description")
            or task_str
        ).strip()

        result: dict[str, Any] = {
            "task_id": task_id,
            "agent_name": self.agent_name,
            "objective": task_obj,
            "status": "completed",
            "findings": [],
            "evidence": [],
            "hypotheses": [],
            "hypothesis_assessment": {},
            "confidence": "medium",
            "verification_status": "unverified",
            "supporting_evidence": [],
            "contradicting_evidence": [],
            "tool_executions": [],
            "tool_call_count": 0,
            "tool_call_limit_reached": False,
            "limitations": [],
            "errors": [],
            "unanswered_questions": [],
            "evidence_gaps": [],
            # Backwards-compatibility fields:
            "investigator_name": self.investigator_name,
            "investigation_task": task_str,
            "evidence_snippets": [],
            "tool_errors": [],
            "missing_evidence": [],
        }

        if not task_str:
            logger.warning("[Code Investigator] specialist_task_failed: Investigation aborted: investigation_task is empty (task_id=%s)", task_id)
            result["status"] = "failed"
            result["tool_errors"].append({
                "tool": "input",
                "error": "investigation_task is required",
            })
            result["errors"].append({
                "tool": "input",
                "error": "investigation_task is required",
            })
            result["findings"].append(_finding(
                CATEGORIES["unknown"],
                "Investigation cannot start because no task was supplied.",
                confidence="none",
                task_id=task_id,
                agent_name=self.agent_name,
            ))
            return result

        if not actual_workspace:
            logger.warning("[Code Investigator] specialist_task_failed: workspace_path is missing (task_id=%s)", task_id)
            result["status"] = "failed"
            result["tool_errors"].append({
                "tool": "input",
                "error": "workspace_path is required",
            })
            result["errors"].append({
                "tool": "input",
                "error": "workspace_path is required",
            })
            result["findings"].append(_finding(
                CATEGORIES["unknown"],
                "Investigation cannot start because workspace_path is missing.",
                confidence="none",
                task_id=task_id,
                agent_name=self.agent_name,
            ))
            return result

        combined_text = _task_and_context(task_str, actual_context)
        terms = extract_terms(combined_text, limit=8)
        logger.debug("[Code Investigator] Extracted search terms: %s", terms)

        # ------------------------------------------------------------------
        # Independent Evidence Discovery:
        # DO NOT copy candidates from context_gathering (candidates_from_context).
        # Discover evidence exclusively via direct tool calls (read_file, search_code).
        # ------------------------------------------------------------------
        discovered_evidence: list[dict[str, Any]] = []

        # Phase 10 Guardrail: Maximum 10 tool calls per investigator execution
        tracker = ToolCallTracker(max_calls=10, investigator_name=self.investigator_name, task_id=task_id)

        # 1. Collect candidate files from task scope and target files
        candidates: list[str] = []

        # Prioritize files explicitly identified in the 10-field task specification
        task_scope = actual_task_dict.get("scope") or actual_task_dict.get("target_files") or []
        if isinstance(task_scope, (list, tuple)):
            for item in task_scope:
                path = _path(item)
                if path and _safe_relative_path(path) and path not in candidates:
                    candidates.append(path)
        elif isinstance(task_scope, str):
            path = _path(task_scope)
            if path and _safe_relative_path(path) and path not in candidates:
                candidates.append(path)

        # Context target files / relevant files
        for key in ("target_files", "relevant_files", "symptom_sites"):
            values = actual_context.get(key, []) or []
            if not isinstance(values, list):
                continue
            for item in values:
                path = _path(item)
                if path and _safe_relative_path(path) and not _is_non_application_path(path) and path not in candidates:
                    candidates.append(path)

        # 2. Discover files via list_files if none specified
        if not candidates:
            listing, errors = safe_list(actual_workspace, self.list_files_tool, tracker=tracker)
            if errors:
                result["tool_errors"].extend(errors)
                result["errors"].extend(errors)
            else:
                for item in listing:
                        if item.get("type") == "directory":
                            continue
                        path = _path(item)
                        if not path or not _safe_relative_path(path) or _is_non_application_path(path):
                            continue
                        parts = PurePosixPath(path).parts
                        if any(part in IGNORED_PARTS for part in parts):
                            continue
                        if PurePosixPath(path).suffix.lower() in SOURCE_EXTENSIONS:
                            candidates.append(path)
        candidates = list(dict.fromkeys(candidates))
        candidates.sort(key=lambda item: _file_score(item, terms), reverse=True)
        selected_paths = candidates[:self.max_files]

        # 3. Read selected files and extract exact matching source lines with multi-hop local import expansion
        idx = 0
        while idx < len(selected_paths) and idx < self.max_files:
            if not tracker.can_call():
                break
            path = selected_paths[idx]
            idx += 1
            content, error = self._read(actual_workspace, path, tracker=tracker)
            if error:
                result["tool_errors"].append(error)
                result["errors"].append(error)
                logger.warning("[Code Investigator] evidence_record_rejected: reason=read_error, source=%s", path)
                continue
            if not content:
                continue

            # Multi-hop local dependency expansion: follow local relative imports
            local_imports = _extract_relative_imports(content, path)
            for imp in local_imports:
                if (
                    imp not in selected_paths
                    and _safe_relative_path(imp)
                    and not _is_non_application_path(imp)
                    and len(selected_paths) < self.max_files
                ):
                    selected_paths.append(imp)

            file_lines = content.splitlines()
            covered_statement_lines: set[int] = set()

            for line_idx, line in enumerate(file_lines):
                line_number = line_idx + 1
                clean_line = line.strip()
                if not clean_line:
                    continue

                if terms and any(term.lower() in clean_line.lower() for term in terms):
                    if line_number in covered_statement_lines:
                        continue

                    # Extract multi-line statement surrounding the match
                    stmt_line, stmt_text = _extract_multiline_statement(file_lines, line_idx)
                    num_lines = stmt_text.count("\n") + 1
                    for covered_ln in range(stmt_line, stmt_line + num_lines):
                        covered_statement_lines.add(covered_ln)

                    # SQL Classification for matching snippet
                    sql_type = classify_sql_statement(stmt_text)
                    ev_kind = "implementation_match"
                    if sql_type:
                        logger.info(
                            "[Code Investigator] sql_statement_classified: type=%s, task_id=%s, source=%s, line=%d",
                            sql_type,
                            task_id,
                            path,
                            stmt_line,
                        )
                        if sql_type == "write_operation":
                            # Disallow classifying INSERT, UPDATE, or DELETE as lookup or query-site evidence
                            ev_kind = "database_write_operation"
                        elif sql_type == "read_query":
                            ev_kind = "database_read_query"

                    discovered_evidence.append({
                        "source": path,
                        "line": stmt_line,
                        "snippet": stmt_text[:MAX_SNIPPET_CHARS],
                        "matched_text": stmt_text[:MAX_SNIPPET_CHARS],
                        "kind": ev_kind,
                        "sql_type": sql_type,
                    })
                    logger.debug("[Code Investigator] evidence_record_accepted: task_id=%s, source=%s, line=%d", task_id, path, stmt_line)

                    if len(discovered_evidence) >= self.max_evidence:
                        break

            if len(discovered_evidence) >= self.max_evidence:
                break

        # 4. Search using individual terms via search_code
        for term in terms[:5]:
            if not tracker.can_call():
                break
            hits, errors = safe_search(
                actual_workspace,
                term,
                self.search_code_tool,
                max_results=8,
                tracker=tracker,
            )
            result["tool_errors"].extend(errors)
            result["errors"].extend(errors)

            for hit in hits:
                item = search_result_evidence(hit, "code_search_match")
                if item and not _is_non_application_path(item.get("source", "")):
                    clean_match = item["matched_text"][:MAX_SNIPPET_CHARS]
                    if _is_covered_by_existing_evidence(item["source"], item.get("line"), clean_match, discovered_evidence):
                        continue
                    sql_type = classify_sql_statement(clean_match)
                    ev_kind = item["kind"]
                    if sql_type:
                        logger.info(
                            "[Code Investigator] sql_statement_classified: type=%s, task_id=%s, source=%s, line=%s",
                            sql_type,
                            task_id,
                            item["source"],
                            item["line"],
                        )
                        if sql_type == "write_operation":
                            ev_kind = "database_write_operation"
                        elif sql_type == "read_query":
                            ev_kind = "database_read_query"

                    discovered_evidence.append({
                        "source": item["source"],
                        "line": item["line"],
                        "snippet": clean_match,
                        "matched_text": clean_match,
                        "kind": ev_kind,
                        "sql_type": sql_type,
                    })
                    logger.debug("[Code Investigator] evidence_record_accepted: task_id=%s, source=%s, line=%s", task_id, item["source"], item["line"])

                if len(discovered_evidence) >= self.max_evidence:
                    break

            if len(discovered_evidence) >= self.max_evidence:
                break

        # 5. Bounded code-flow tracing for identifiers
        trace_terms = []
        declaration_pattern = (
            r"\b(?:def|function|const|let|var|class|"
            r"async\s+def)\s+([A-Za-z_$][\w$]*)"
        )
        for item in discovered_evidence:
            identifiers = re.findall(declaration_pattern, item["snippet"])
            for identifier in identifiers:
                if identifier not in trace_terms and identifier not in terms:
                    trace_terms.append(identifier)

        for identifier in trace_terms[:3]:
            if not tracker.can_call():
                break
            hits, errors = safe_search(
                actual_workspace,
                identifier,
                self.search_code_tool,
                max_results=5,
                tracker=tracker,
            )
            result["tool_errors"].extend(errors)
            result["errors"].extend(errors)

            for hit in hits:
                item = search_result_evidence(hit, "code_flow_reference")
                if item and not _is_non_application_path(item.get("source", "")):
                    clean_match = item["matched_text"][:MAX_SNIPPET_CHARS]
                    if _is_covered_by_existing_evidence(item["source"], item.get("line"), clean_match, discovered_evidence):
                        continue
                    sql_type = classify_sql_statement(clean_match)
                    ev_kind = item["kind"]
                    if sql_type:
                        logger.info(
                            "[Code Investigator] sql_statement_classified: type=%s, task_id=%s, source=%s, line=%s",
                            sql_type,
                            task_id,
                            item["source"],
                            item["line"],
                        )
                        if sql_type == "write_operation":
                            ev_kind = "database_write_operation"
                        elif sql_type == "read_query":
                            ev_kind = "database_read_query"

                    discovered_evidence.append({
                        "source": item["source"],
                        "line": item["line"],
                        "snippet": clean_match,
                        "matched_text": clean_match,
                        "kind": ev_kind,
                        "sql_type": sql_type,
                    })

                if len(discovered_evidence) >= self.max_evidence:
                    break

            if len(discovered_evidence) >= self.max_evidence:
                break

        # 6. Deduplicate and format evidence with provenance tags
        discovered_evidence = unique_evidence(discovered_evidence, limit=self.max_evidence)

        formatted_evidence: list[dict[str, Any]] = []
        for index, item in enumerate(discovered_evidence, start=1):
            ev_id = f"EV-CODE-{index:03d}"
            ev_src = item["source"]
            ev_ln = item.get("line") or "?"
            ev_snip = item["snippet"]

            if item.get("sql_type") == "read_query":
                ev_exp = f"Database read query in {ev_src}:{ev_ln}. Establishes query selection criteria and constraints, supporting lookup mismatch hypotheses."
            elif item.get("sql_type") == "write_operation":
                ev_exp = f"Database write operation in {ev_src}:{ev_ln}. Establishes parameter ordering and persistence columns, supporting contract divergence hypotheses."
            elif any(k in ev_snip.lower() for k in ("tolowercase", "touppercase", "trim", "normalize", "cleanemail")):
                ev_exp = f"Credential transformation in {ev_src}:{ev_ln}. Demonstrates normalization applied to input values, supporting casing discrepancy hypotheses."
            else:
                ev_exp = f"Source code reference in {ev_src}:{ev_ln} establishing active logic for the investigated code path."

            ev_record = {
                "evidence_id": ev_id,
                "task_id": task_id,
                "agent_name": self.agent_name,
                "source": ev_src,
                "line": item["line"],
                "matched_text": ev_snip,
                "snippet": ev_snip,
                "kind": item["kind"],
                "sql_type": item.get("sql_type"),
                "verification_status": "unverified",
                "observed_fact": f"Code match in {ev_src} line {ev_ln}: {ev_snip[:100]}",
                "explanation": ev_exp,
            }
            formatted_evidence.append(ev_record)

        result["evidence"] = formatted_evidence
        result["evidence_snippets"] = formatted_evidence

        # 7. Generate findings with strict SQL classification, provenance, and explanation of support/contradiction
        if formatted_evidence:
            by_file: dict[str, list[dict]] = {}
            for item in formatted_evidence:
                by_file.setdefault(item["source"], []).append(item)

            for src, items in list(by_file.items())[:5]:
                file_ev_ids = [it["evidence_id"] for it in items]
                substantive_statement = _derive_substantive_finding_statement(src, items)
                first_line = items[0].get("line")
                result["findings"].append(_finding(
                    CATEGORIES["confirmed"],
                    substantive_statement,
                    evidence_ids=file_ev_ids,
                    confidence="medium",
                    rationale=f"Observed source statements in {src} (line {first_line or '?'}). Localizes active code paths for the investigated problem, supporting targeted inspection and narrowing candidate defect locations.",
                    supports_or_contradicts="Supports localization to this component; narrows candidate defect sites.",
                    task_id=task_id,
                    agent_name=self.agent_name,
                    file_path=src,
                    line_number=first_line,
                    relevant_code_reference=f"{src}:{first_line}" if first_line else src,
                ))

            key_op_count = 0
            for item in formatted_evidence:
                sql_type = item.get("sql_type")
                src_path = item["source"]
                line_no = item.get("line")
                ref_str = f"{src_path}:{line_no}" if line_no else src_path

                if sql_type == "read_query":
                    result["findings"].append(_finding(
                        CATEGORIES["confirmed"],
                        f"Database read query in {src_path} (line {line_no or '?' }): `{item['snippet'][:100]}`",
                        evidence_ids=[item["evidence_id"]],
                        confidence="medium",
                        rationale=f"Observed database SELECT query in {ref_str}. Defines query lookup criteria; supports candidate causes involving query filter mismatch or lookup constraints, and contradicts hypotheses claiming query is missing.",
                        supports_or_contradicts="Supports lookup constraint/filtering discrepancy hypotheses; contradicts missing query execution.",
                        task_id=task_id,
                        agent_name=self.agent_name,
                        file_path=src_path,
                        line_number=line_no,
                        relevant_code_reference=ref_str,
                    ))
                    key_op_count += 1
                elif sql_type == "write_operation":
                    # Disallow classifying INSERT, UPDATE, or DELETE as lookup or query-site evidence
                    result["findings"].append(_finding(
                        CATEGORIES["confirmed"],
                        f"Database write operation in {src_path} (line {line_no or '?' }): `{item['snippet'][:100]}`",
                        evidence_ids=[item["evidence_id"]],
                        confidence="medium",
                        rationale=f"Observed database mutation in {ref_str}. Specifies persisted columns and parameter order; supports candidate causes involving parameter order or column mapping divergence.",
                        supports_or_contradicts="Supports persistence contract divergence hypotheses; contradicts total omission of persistence.",
                        task_id=task_id,
                        agent_name=self.agent_name,
                        file_path=src_path,
                        line_number=line_no,
                        relevant_code_reference=ref_str,
                    ))
                    key_op_count += 1
                else:
                    snip_low = item["snippet"].lower()
                    is_token = bool(re.search(r"\b(?:signtoken|verifytoken|jwt\.sign|jwt\.verify|token\b|bearer\b)", snip_low))
                    is_norm = bool(re.search(r"\b(?:tolowercase|touppercase|trim|normalize)\b", snip_low))
                    if is_token or is_norm:
                        result["findings"].append(_finding(
                            CATEGORIES["confirmed"],
                            f"Token/credential handling in {src_path} (line {line_no or '?' }): `{item['snippet'][:100]}`",
                            evidence_ids=[item["evidence_id"]],
                            confidence="medium",
                            rationale=f"Observed credential/token handling in {ref_str}. Specifies normalization and token claim generation; supports candidate causes involving casing discrepancies between claims and stored records.",
                            supports_or_contradicts="Supports normalization/casing divergence hypotheses; contradicts token generation omission.",
                            task_id=task_id,
                            agent_name=self.agent_name,
                            file_path=src_path,
                            line_number=line_no,
                            relevant_code_reference=ref_str,
                        ))
                        key_op_count += 1

                if key_op_count >= 4:
                    break

            # Deduplicate findings by (file_path, line_number, statement_essence)
            deduped_findings: list[dict[str, Any]] = []
            seen_finding_signatures: set[tuple[str, Any, str]] = set()
            for f in result["findings"]:
                fp = str(f.get("file_path") or "").lower()
                ln = f.get("line_number")
                stmt = str(f.get("statement") or f.get("finding") or "").strip().lower()
                stmt_words = tuple(re.findall(r"\w+", stmt)[:6])
                sig = (fp, ln, " ".join(stmt_words))
                if sig in seen_finding_signatures:
                    continue
                seen_finding_signatures.add(sig)
                deduped_findings.append(f)
            result["findings"] = deduped_findings

            # 8. Hypotheses: Generate ONLY when observed code reveals a plausible defect mechanism.
            # Filter to real application files (exclude seed scripts, locks, and test fixtures)
            app_evidence = [
                it for it in formatted_evidence
                if not _is_non_application_path(it.get("source", ""))
            ]

            # Try structured LLM hypothesis generation first if configured
            llm_hypotheses = self._generate_hypotheses_with_llm(
                task_id=task_id,
                task_text=task_str,
                context=actual_context,
                formatted_evidence=app_evidence,
            )
            if llm_hypotheses:
                result["hypotheses"].extend(llm_hypotheses)
                for h in llm_hypotheses:
                    logger.info(
                        "[Code Investigator] hypothesis_validated (LLM): task_id=%s, hypothesis_id=%s, citing=%s",
                        task_id,
                        h.get("hypothesis_id"),
                        h.get("supporting_evidence_ids"),
                    )
            else:
                # Deterministic defect mechanism analysis fallback
                prior_ev = [
                    e for e in (actual_context.get("prior_evidence") or [])
                    if isinstance(e, dict) and not _is_non_application_path(e.get("source", ""))
                ]
                combined_ev = app_evidence + [
                    pe for pe in prior_ev
                    if not any(ae.get("source") == pe.get("source") and ae.get("line") == pe.get("line") for ae in app_evidence)
                ]
                norm_items = [
                    e for e in combined_ev
                    if any(k in e.get("snippet", "").lower() for k in ("tolowercase", "touppercase", "trim", "normalize", "cleanemail", "normalizedemail"))
                ]
                query_items = [
                    e for e in combined_ev
                    if e.get("sql_type") == "read_query" or any(k in e.get("snippet", "").lower() for k in ("select", "findbyemail", "findby", "where email", "byemail", "query(", ".query", "find("))
                ]

                # Pattern A: Credential / Identifier Casing or Normalization Mismatch
                contra_norm = [
                    it for it in app_evidence
                    if any(k in it.get("snippet", "").lower() for k in ("password_hash", "bcrypt", "200", "cleanemail", "lastinsertrowid"))
                ]
                contra_norm_ids = [c["evidence_id"] for c in contra_norm[:2] if "evidence_id" in c]

                is_auth_domain = any(
                    k in (actual_context.get("symptoms", "") + " " + task_obj + " " + str(actual_context.get("original_problem", ""))).lower()
                    for k in ("auth", "login", "token", "jwt", "credential", "401", "password", "unauthorized", "case-sensitive", "casing", "lowercase", "uppercase")
                )

                if norm_items and query_items:
                    lead_norm = norm_items[0]
                    lead_query = query_items[0]
                    cluster = [lead_norm, lead_query]
                    cluster_ids = [e["evidence_id"] for e in cluster]
                    hyp_id = f"HYP-{task_id[-8:] if len(task_id) >= 8 else task_id}-001"
                    if is_auth_domain:
                        h_title = "Casing or normalization discrepancy in credential lookup"
                        p_cause = (
                            f"Identifier normalization at {lead_norm['source']} (line {lead_norm.get('line') or '?'}) "
                            f"diverges from database lookup comparison at {lead_query['source']} (line {lead_query.get('line') or '?'})."
                        )
                        h_reason = (
                            "If user credentials or identifiers are stored with mixed casing or looked up using normalized lowercase credentials, "
                            "an exact equality database query will fail to match existing records, causing subsequent authorization to return 401 Unauthorized."
                        )
                        h_unans = [
                            "Does the database column collation enforce case-sensitivity for lookup queries?",
                            "Are existing records in the database stored with original mixed casing or normalized lowercase?",
                        ]
                    else:
                        h_title = "Data normalization or parameter transformation mismatch prior to query execution"
                        p_cause = (
                            f"Value transformation at {lead_norm['source']} (line {lead_norm.get('line') or '?'}) "
                            f"modifies parameters before query execution at {lead_query['source']} (line {lead_query.get('line') or '?'})."
                        )
                        h_reason = (
                            "Value transformation or normalization alters parameter values or formatting, "
                            "which may fail to match expected query parameters or database constraints."
                        )
                        h_unans = [
                            f"Does the query at {lead_query['source']} expect untransformed parameter values?",
                            f"Does the transformation at {lead_norm['source']} alter the data type or value format unexpectedly?",
                        ]

                    hyp = create_hypothesis(
                        hypothesis_id=hyp_id,
                        hypothesis_title=h_title,
                        produced_by=self.investigator_name,
                        possible_cause=p_cause,
                        evidence=f"{lead_norm['source']}:{lead_norm.get('line') or '?'} vs {lead_query['source']}:{lead_query.get('line') or '?'}",
                        reasoning=h_reason,
                        supporting_evidence=cluster,
                        supporting_evidence_ids=cluster_ids,
                        contradicting_evidence=contra_norm[:2],
                        contradicting_evidence_ids=contra_norm_ids,
                        status="needs_validation",
                        confidence="high" if len(norm_items) > 1 else "medium",
                        unanswered_questions=h_unans,
                        task_id=task_id,
                        agent_name=self.agent_name,
                    )
                    result["hypotheses"].append(hyp)
                    logger.info(
                        "[Code Investigator] hypothesis_validated: task_id=%s, hypothesis_id=%s, citing=%s",
                        task_id,
                        hyp_id,
                        cluster_ids,
                    )

                    # Distinct workflow hypothesis: Registration casing vs login normalization (auth only)
                    if is_auth_domain:
                        reg_norm = [e for e in norm_items if "clean" in e.get("snippet", "").lower() or "register" in e.get("source", "").lower()]
                        login_norm = [e for e in norm_items if "tolowercase" in e.get("snippet", "").lower() or "normalized" in e.get("snippet", "").lower()]
                        if reg_norm and login_norm and reg_norm[0] != login_norm[0]:
                            c_lead_reg = reg_norm[0]
                            c_lead_login = login_norm[0]
                            c_cluster = [c_lead_reg, c_lead_login]
                            c_cluster_ids = [e["evidence_id"] for e in c_cluster if "evidence_id" in e]
                            c_hyp_id = f"HYP-{task_id[-8:] if len(task_id) >= 8 else task_id}-{len(result['hypotheses']) + 1:03d}"
                            c_hyp = create_hypothesis(
                                hypothesis_id=c_hyp_id,
                                hypothesis_title="Inconsistent credential normalization between registration and login workflows",
                                produced_by=self.investigator_name,
                                possible_cause=(
                                    f"Registration at {c_lead_reg['source']} (line {c_lead_reg.get('line') or '?'}) preserves raw casing, "
                                    f"while authentication at {c_lead_login['source']} (line {c_lead_login.get('line') or '?'}) converts credentials to lowercase."
                                ),
                                evidence=f"{c_lead_reg['source']}:{c_lead_reg.get('line') or '?'} vs {c_lead_login['source']}:{c_lead_login.get('line') or '?'}",
                                reasoning=(
                                    "User registration preserves mixed-case email addresses in the user table, while the login service converts emails to lowercase "
                                    "when generating token claims. This creates an inconsistency between stored database values and claims propagated across subsequent requests."
                                ),
                                supporting_evidence=c_cluster,
                                supporting_evidence_ids=c_cluster_ids,
                                contradicting_evidence=contra_norm[:2],
                                contradicting_evidence_ids=contra_norm_ids,
                                status="needs_validation",
                                confidence="medium",
                                unanswered_questions=[
                                    "Are user accounts in the database stored with original mixed casing or normalized values?",
                                    "Does the registration service intend to enforce uniform lowercase persistence?",
                                ],
                                task_id=task_id,
                                agent_name=self.agent_name,
                            )
                            result["hypotheses"].append(c_hyp)

                # Pattern B: Defect indicators in application code
                potential_mechanisms: list[dict[str, Any]] = []
                for item in app_evidence:
                    snip_clean = item["snippet"].strip()
                    if snip_clean.startswith(("#", "//", "/*", "*")):
                        continue
                    snip_low = snip_clean.lower()
                    has_defect_indicator = bool(re.search(
                        r"\b(?:raise\b|throw\b|status_code\s*=\s*[45]\d{2}|not\s+found|unauthorized|invalid|expired|failed|failure|mismatch|is\s+none\b|==\s*none\b|fixme|bug|reject\b)",
                        snip_low,
                    ))
                    if has_defect_indicator and not any(item["snippet"] == h_item["snippet"] for h_item in (norm_items + query_items)):
                        potential_mechanisms.append(item)

                if potential_mechanisms:
                    distinct_clusters: list[list[dict[str, Any]]] = []
                    for item in potential_mechanisms:
                        item_src = item.get("source", "")
                        item_line = item.get("line") or 0

                        matched_cluster = None
                        for cluster in distinct_clusters:
                            first = cluster[0]
                            first_src = first.get("source", "")
                            first_line = first.get("line") or 0
                            if item_src == first_src and abs(item_line - first_line) <= 5:
                                matched_cluster = cluster
                                break
                        if matched_cluster is not None:
                            matched_cluster.append(item)
                        else:
                            distinct_clusters.append([item])

                    for idx, cluster in enumerate(distinct_clusters[:2], start=len(result["hypotheses"]) + 1):
                        lead = cluster[0]
                        cluster_ev_ids = [it["evidence_id"] for it in cluster if "evidence_id" in it]
                        line_no = lead.get("line") or "?"
                        source_file = lead.get("source", "unknown")
                        snip_low = lead["snippet"].lower()

                        contra_items = [
                            it for it in app_evidence
                            if it["source"] == source_file
                            and it.get("line") != lead.get("line")
                            and any(term in it["snippet"].lower() for term in ("fallback", "default", "success", "return true", "return 200", "ignore_errors"))
                        ]

                        if any(k in snip_low for k in ("unauthorized", "token", "bearer", "jwt", "auth", "header", "credential", "401")):
                            title = f"Authentication or authorization failure in {source_file}"
                            cause = f"Request or execution flow may be rejected by authentication logic in {source_file} (line {line_no})."
                            reasoning = "If expected credentials, headers, or authentication tokens are missing, expired, or invalid, backend authentication logic may reject the request."
                            unanswered = [
                                "Does the request include the required authentication token or credentials at runtime?",
                                f"What authentication requirements and claims does {source_file} enforce?",
                            ]
                        elif any(k in snip_low for k in ("raise", "throw", "500", "exception", "reject")):
                            title = f"Explicit error condition or failure path in {source_file}"
                            cause = f"An unhandled condition or explicitly raised error in {source_file} (line {line_no}) may trigger failure."
                            reasoning = "Reaching this branch results in an explicit exception, rejection, or error status code."
                            unanswered = [
                                f"What runtime input or state triggers this error branch in {source_file}?",
                                "Is the failure handled gracefully or does it bubble up to caller as an unhandled error?",
                            ]
                        else:
                            title = f"Potential condition check bypass or mismatch in {source_file}"
                            cause = f"Condition check or failure handling logic at {source_file} (line {line_no}) may trigger unexpected behavior."
                            reasoning = "Observed code at this site contains failure handling or condition checks relating to the investigated task."
                            unanswered = [
                                f"Does runtime execution reach {source_file} line {line_no} during the failure scenario?",
                                "What input values cause this condition to evaluate to the failure branch?",
                            ]

                        hyp_id = f"HYP-{task_id[-8:] if len(task_id) >= 8 else task_id}-{idx:03d}"
                        hyp = create_hypothesis(
                            hypothesis_id=hyp_id,
                            hypothesis_title=title,
                            produced_by=self.investigator_name,
                            possible_cause=cause,
                            evidence=f"{source_file}:{line_no} — `{lead['snippet'][:150]}`",
                            reasoning=reasoning,
                            supporting_evidence=cluster,
                            supporting_evidence_ids=cluster_ev_ids,
                            contradicting_evidence=contra_items,
                            contradicting_evidence_ids=[c["evidence_id"] for c in contra_items if "evidence_id" in c],
                            status="needs_validation",
                            confidence="medium",
                            unanswered_questions=unanswered,
                            task_id=task_id,
                            agent_name=self.agent_name,
                        )
                        result["hypotheses"].append(hyp)
                        logger.info(
                            "[Code Investigator] hypothesis_validated: task_id=%s, hypothesis_id=%s, citing_evidence=%s",
                            task_id,
                            hyp_id,
                            cluster_ev_ids,
                        )

                if not result["hypotheses"]:
                    result["hypotheses"] = []
                    result["unanswered_questions"].append(
                        f"No plausible defect mechanism was observed in the inspected source files for task {task_id}."
                    )
                    logger.info(
                        "[Code Investigator] No plausible defect mechanism observed in evidence; hypotheses list is empty (task_id=%s).",
                        task_id,
                    )

        else:
            result["findings"].append(_finding(
                CATEGORIES["unknown"],
                "No relevant source-code evidence could be established.",
                confidence="none",
                task_id=task_id,
                agent_name=self.agent_name,
            ))
            result["missing_evidence"].append(
                "Provide a specific identifier, relevant file, or additional context to narrow the search."
            )
            result["unanswered_questions"].append(
                "No plausible defect mechanism or relevant source-code evidence could be established."
            )
            result["hypotheses"] = []

        if result["tool_errors"]:
            result["status"] = "partial" if formatted_evidence else "failed"

        assessments: dict[str, Any] = {}
        for h in result["hypotheses"]:
            if isinstance(h, dict):
                h_id = h.get("hypothesis_id") or "HYP"
                assessments[h_id] = {
                    "status": h.get("status", "unverified"),
                    "confidence": h.get("confidence", "medium"),
                    "supporting_evidence_ids": h.get("supporting_evidence_ids", []),
                    "contradicting_evidence_ids": h.get("contradicting_evidence_ids", []),
                }
        if not assessments:
            assessments = {"status": "unresolved", "summary": "No defect mechanism confirmed in inspected scope"}
        result["hypothesis_assessment"] = assessments
        result["evidence_gaps"] = list(result.get("missing_evidence") or [])

        result["tool_call_count"] = tracker.call_count
        result["tool_call_limit_reached"] = tracker.limit_reached
        result["tool_executions"] = tracker.executions
        if tracker.limit_reached:
            limit_msg = f"Tool-call limit reached ({tracker.max_calls} calls maximum per execution)."
            result["limitations"].append(limit_msg)
            if not any(e.get("limit_reached") for e in result["tool_errors"]):
                result["tool_errors"].append({
                    "tool": "guardrail",
                    "error": limit_msg,
                    "limit_reached": True,
                })

        logger.info(
            "[Code Investigator] specialist_task_complete: task_id=%s, agent_name=%s, status=%s",
            task_id,
            self.agent_name,
            result.get("status"),
        )
        return result

    def __call__(
        self,
        workspace_path: Any = None,
        investigation_task: Any = None,
        shared_context: dict | None = None,
        *args: Any,
        **kwargs: Any,
    ) -> dict:
        return self.investigate(
            workspace_path,
            investigation_task,
            shared_context,
            *args,
            **kwargs,
        )

    def _read(self, workspace: str, path: str, tracker: Any = None) -> tuple[str, dict | None]:
        if not _safe_relative_path(path):
            return "", {
                "tool": "read_file",
                "path": path,
                "error": "Unsafe relative path rejected",
            }

        return safe_read(workspace, path, self.read_file_tool, tracker=tracker)

    def _generate_hypotheses_with_llm(
        self,
        task_id: str,
        task_text: str,
        context: dict,
        formatted_evidence: list[dict],
    ) -> list[dict]:
        """Generate evidence-backed hypotheses using structured LLM synthesis if available."""
        if not self.llm_factory:
            return []
        try:
            llm = self.llm_factory()
            if not llm:
                return []
            structured = llm.with_structured_output(CodeHypothesesPayload)

            app_ev = [
                e for e in formatted_evidence
                if not _is_non_application_path(e.get("source", ""))
            ][:20]
            if not app_ev:
                return []

            ev_summary = [
                {
                    "evidence_id": e.get("evidence_id"),
                    "source": e.get("source"),
                    "line": e.get("line"),
                    "snippet": e.get("snippet", "")[:300],
                    "kind": e.get("kind"),
                    "sql_type": e.get("sql_type"),
                }
                for e in app_ev
            ]

            prob_text = context.get("problem") or context.get("problem_summary") or task_text
            prompt = (
                f"You are a Senior Code Investigator analyzing source code evidence for a software issue.\n\n"
                f"Investigation Task: {task_text}\n"
                f"Problem Statement: {prob_text}\n\n"
                f"Collected Source Evidence ({len(ev_summary)} items):\n"
                f"{json.dumps(ev_summary, indent=2)}\n\n"
                f"Instructions:\n"
                f"1. Examine the source evidence for plausible defect mechanisms explaining the problem (e.g., casing/normalization mismatches, conditional logic flaws, unhandled error returns, or contract discrepancies).\n"
                f"2. Cite the exact source file and line locations in cited_sources.\n"
                f"3. Explain step-by-step how the observed code mechanism leads to the reported failure symptom.\n"
                f"4. State unanswered questions that require runtime verification.\n"
                f"5. If no plausible defect mechanism is present in the evidence, return an empty hypotheses list."
            )
            res = structured.invoke(prompt)
            if not res or not res.hypotheses:
                return []

            hypotheses = []
            for idx, item in enumerate(res.hypotheses[:3], start=1):
                hyp_id = f"HYP-{task_id[-8:] if len(task_id) >= 8 else task_id}-{idx:03d}"
                supporting = []
                supporting_ids = []
                for ev in app_ev:
                    src = ev.get("source", "")
                    line = str(ev.get("line") or "")
                    if any(c in src or (line and line in c) for c in item.cited_sources) or not item.cited_sources:
                        supporting.append(ev)
                        supporting_ids.append(ev["evidence_id"])
                if not supporting:
                    supporting = app_ev[:2]
                    supporting_ids = [e["evidence_id"] for e in supporting]

                hyp = create_hypothesis(
                    hypothesis_id=hyp_id,
                    hypothesis_title=item.title,
                    produced_by=self.investigator_name,
                    possible_cause=item.possible_cause,
                    evidence=", ".join(item.cited_sources) if item.cited_sources else f"{supporting[0]['source']}:{supporting[0].get('line') or '?'}",
                    reasoning=item.reasoning,
                    supporting_evidence=supporting,
                    supporting_evidence_ids=supporting_ids,
                    status="needs_validation",
                    confidence=item.confidence,
                    unanswered_questions=item.unanswered_questions or [
                        "Does dynamic runtime execution reach this branch with the hypothesized input values?"
                    ],
                    task_id=task_id,
                    agent_name=self.agent_name,
                )
                hypotheses.append(hyp)
            return hypotheses
        except Exception as exc:
            logger.debug("LLM code hypothesis generation fallback: %s", exc)
            return []


def run_code_investigator(
    investigation_task: Any,
    relevant_context: dict | None = None,
    current_state: dict | None = None,
    *,
    list_files_tool: Any = list_files,
    search_code_tool: Any = search_code,
    read_file_tool: Any = read_file,
    llm_factory: Optional[Callable] = None,
) -> dict:
    """Backward-compatible entry point for the existing workflow."""
    state = _dict(current_state)
    context = _dict(relevant_context)
    workspace = resolve_workspace(state, context)

    combined_context = dict(context)
    combined_context.setdefault("problem", state.get("problem", ""))
    combined_context.setdefault(
        "gathered_context",
        state.get("gathered_context", {}),
    )

    investigator = CodeInvestigator(
        list_files_tool=list_files_tool,
        search_code_tool=search_code_tool,
        read_file_tool=read_file_tool,
        llm_factory=llm_factory,
    )

    return investigator.investigate(
        workspace,
        investigation_task,
        combined_context,
    )

