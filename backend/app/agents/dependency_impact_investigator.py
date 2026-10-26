
"""Phase 5: Dependency / Impact Investigator.

Discovers imports, callers, endpoint references, and potential cross-component
impact paths. Findings distinguish observed relationships from hypotheses.
"""

from __future__ import annotations

import json
import logging
from pathlib import PurePosixPath
import posixpath
import re
import time
from typing import Any, Callable, List, Literal, Optional

from pydantic import BaseModel, Field


class DependencyHypothesisItem(BaseModel):
    title: str = Field(description="Clear title of the hypothesized cross-component defect or contract mismatch")
    possible_cause: str = Field(description="Plausible defect mechanism across component boundaries")
    reasoning: str = Field(description="Step-by-step reasoning linking cross-component interactions to the reported symptom")
    cited_sources: List[str] = Field(default_factory=list, description="Source paths from the evidence")
    unanswered_questions: List[str] = Field(default_factory=list, description="Questions needing runtime/test verification")
    confidence: Literal["low", "medium", "high"] = Field(default="medium")


class DependencyHypothesesPayload(BaseModel):
    hypotheses: List[DependencyHypothesisItem] = Field(default_factory=list)


def _is_non_application_path(path: str) -> bool:
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


def _is_boilerplate_error_handler(snippet: str) -> bool:
    low = snippet.lower()
    return any(b in low for b in (
        "err, req, res, next",
        "errorhandler",
        "app.use(errorhandler)",
        "app.use(express.json",
        "middleware/errorhandler",
    ))

from app.agents.investigator_contract import (
    ToolCallTracker,
    create_hypothesis,
    extract_terms,
    normalize_items,
    resolve_workspace,
    safe_list,
    safe_read,
    safe_search,
    unique_evidence,
)
from app.logging_config import sanitize_log_text
from app.tools.investigation_tools import list_files, read_file, search_code

logger = logging.getLogger("app.agents.dependency_impact_investigator")


INVESTIGATOR_NAME = "Dependency / Impact Investigator"

CATEGORIES = {
    "confirmed": "Confirmed Finding",
    "possible": "Possible Cause",
    "assumption": "Assumption",
    "unknown": "Unknown",
}

SOURCE_EXTENSIONS = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs",
    ".java", ".go", ".rs", ".php", ".rb", ".cs", ".cpp",
    ".c", ".h", ".sql", ".html", ".json", ".yaml", ".yml",
}

IGNORED_PARTS = {
    ".git", ".venv", "venv", "node_modules", "__pycache__",
    "dist", "build", ".pytest_cache", "coverage",
}

MAX_FILES = 100
MAX_EVIDENCE = 60
MAX_TRACE_TERMS = 12
MAX_SNIPPET_CHARS = 500


def _as_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _as_path(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("path") or value.get("file") or value.get("source")

    if not isinstance(value, str):
        return ""

    return value.replace("\\", "/").strip()


def _safe_relative_path(path: str) -> bool:
    if not path or "\x00" in path:
        return False

    parsed = PurePosixPath(path.replace("\\", "/"))
    return not parsed.is_absolute() and ".." not in parsed.parts


def _line_number(value: Any) -> int | None:
    try:
        line = int(value)
        return line if line > 0 else None
    except (TypeError, ValueError):
        return None


def _context_text(task: str, context: dict) -> str:
    parts = [task]

    for key in (
        "problem",
        "problem_summary",
        "observed_behavior",
        "expected_behavior",
        "error_message",
        "endpoint",
        "function_name",
        "target_symbol",
        "target_files",
        "relevant_files",
    ):
        value = context.get(key)

        if isinstance(value, str):
            parts.append(value)
        elif isinstance(value, (list, tuple)):
            for item in value:
                if isinstance(item, dict):
                    path = _as_path(item)
                    if path:
                        parts.append(path)
                elif item:
                    parts.append(str(item))

    understanding = _as_dict(context.get("problem_understanding"))

    for key in (
        "problem_summary",
        "observed_behavior",
        "expected_behavior",
        "target_areas",
    ):
        value = understanding.get(key)

        if isinstance(value, list):
            parts.extend(str(item) for item in value if item)
        elif value:
            parts.append(str(value))

    return " ".join(parts)


def _finding(
    category: str,
    statement: str,
    evidence_ids: list[str] | None = None,
    *,
    confidence: str = "medium",
    rationale: str | None = None,
    task_id: str | None = None,
    agent_name: str | None = "dependency_impact_investigator",
    file_path: str | None = None,
    line_number: int | None = None,
) -> dict:
    finding = {
        "category": category,
        "statement": statement,
        "description": statement,
        "confidence": confidence,
        "evidence_ids": evidence_ids or [],
        "task_id": task_id,
        "agent_name": agent_name,
    }

    if file_path:
        finding["file_path"] = file_path
    if line_number is not None:
        finding["line_number"] = line_number
    if rationale:
        finding["rationale"] = rationale

    return finding


def _source_extension(path: str) -> bool:
    return PurePosixPath(path).suffix.lower() in SOURCE_EXTENSIONS


def _extract_identifiers(text: str) -> list[str]:
    """Extract endpoint paths and code identifiers from the task/context."""
    identifiers = []

    endpoint_pattern = r"/[A-Za-z0-9_./{}:-]+"

    for endpoint in re.findall(endpoint_pattern, text or ""):
        endpoint = endpoint.rstrip(".,;)")
        if len(endpoint) > 1 and endpoint not in identifiers:
            identifiers.append(endpoint)

    ignored_words = {
        "trace", "find", "identify", "investigate", "all", "callers",
        "dependencies", "dependency", "impact", "across", "frontend",
        "backend", "service", "component", "file", "route", "controller",
        "model", "database", "profile", "request", "update", "where",
        "from", "with", "that", "this", "the", "and", "for", "into",
        "what", "how", "who", "which", "are", "was", "has", "have",
        "root", "cause", "error", "issue", "fails", "failure",
        "references", "reference", "related",
    }

    for word in re.findall(r"\b[A-Za-z_$][A-Za-z0-9_$]*\b", text or ""):
        if word.lower() in ignored_words or len(word) < 3:
            continue

        if word not in identifiers:
            identifiers.append(word)

    return identifiers[:MAX_TRACE_TERMS]


def _relationship_kind(line: str) -> str:
    lowered = line.lower()

    if re.search(r"\b(import|from)\b", line) or "require(" in line:
        return "import_reference"

    if re.search(r"\b(fetch|axios|request|httpx|requests\.)\b", lowered):
        return "api_request"

    if re.search(
        r"\b(router|route|app)\.(get|post|put|patch|delete)\b",
        lowered,
    ):
        return "route_declaration"

    if re.search(
        r"\b(next|prisma|sequelize|mongoose|db|database|query)\b",
        lowered,
    ):
        return "data_access_reference"

    return "call_reference"


def _resolve_import_targets(
    source: str,
    module: str,
    known_paths: set[str],
) -> set[str]:
    """Resolve a module specifier against discovered repository files."""
    source = source.replace("\\", "/")
    module = module.replace("\\", "/")

    extensions = (
        "",
        ".js",
        ".jsx",
        ".ts",
        ".tsx",
        ".mjs",
        ".cjs",
        ".py",
        ".java",
        "/index.js",
        "/index.jsx",
        "/index.ts",
        "/index.tsx",
        "/__init__.py",
    )

    if module.startswith("."):
        # Critical fix: normalize paths containing ../ and ./.
        base = posixpath.normpath(
            posixpath.join(posixpath.dirname(source), module)
        )

        possible_targets = {
            posixpath.normpath(base + extension)
            for extension in extensions
        }

        return known_paths.intersection(possible_targets)

    # Handle Python package imports and non-relative module specifiers.
    module_path = module.replace(".", "/")

    possible_targets = {
        posixpath.normpath(module_path + extension)
        for extension in extensions
    }

    return known_paths.intersection(possible_targets)


class DependencyImpactInvestigator:
    """Trace observed code relationships and estimate potential impact."""

    investigator_name = INVESTIGATOR_NAME

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
        self.max_files = max(1, min(int(max_files), 300))
        self.max_evidence = max(1, min(int(max_evidence), 150))

    agent_name = "dependency_impact_investigator"

    def investigate(
        self,
        workspace_path: Any = None,
        investigation_task: Any = None,
        shared_context: dict | None = None,
        *args: Any,
        **kwargs: Any,
    ) -> dict:
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
            actual_context = _as_dict(shared_context)
        elif isinstance(workspace_path, dict):
            # Calling convention: investigate(task, context, state)
            actual_task_dict = dict(workspace_path)
            actual_context = _as_dict(investigation_task)
            state_dict = _as_dict(shared_context)
            actual_workspace = resolve_workspace(state_dict, actual_context)
        elif "workspace_path" in kwargs:
            actual_workspace = str(kwargs["workspace_path"] or "").strip()
            if isinstance(investigation_task, dict):
                actual_task_dict = dict(investigation_task)
            elif isinstance(investigation_task, str):
                actual_task_dict = {"instructions": investigation_task.strip()}
            actual_context = _as_dict(shared_context or kwargs.get("shared_context"))

        if not actual_workspace and actual_context:
            actual_workspace = resolve_workspace({}, actual_context)

        step_from_ctx = actual_context.get("investigation_step") if isinstance(actual_context.get("investigation_step"), dict) else {}
        task_id = str(
            actual_task_dict.get("task_id")
            or step_from_ctx.get("task_id")
            or actual_context.get("task_id")
            or "TASK-DEP-001"
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
            "[Dependency / Impact Investigator] specialist_task_start: task_id=%s, agent_name=%s, workspace=%s",
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
            "flow_edges": [],
            "unanswered_questions": [],
            "evidence_gaps": [],
            # Backward compatibility fields:
            "investigator_name": self.investigator_name,
            "investigation_task": task_str,
            "evidence_snippets": [],
            "tool_errors": [],
            "missing_evidence": [],
        }

        if not task_str:
            logger.warning("[Dependency / Impact Investigator] specialist_task_failed: investigation_task is empty (task_id=%s)", task_id)
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
                "Cannot trace dependencies without an investigation task.",
                confidence="none",
                task_id=task_id,
                agent_name=self.agent_name,
            ))
            return result

        if not actual_workspace:
            logger.warning("[Dependency / Impact Investigator] specialist_task_failed: workspace_path is missing (task_id=%s)", task_id)
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
                "Cannot trace dependencies because workspace_path is missing.",
                confidence="none",
                task_id=task_id,
                agent_name=self.agent_name,
            ))
            return result

        # Dependency search identifiers include both gathered repository context and the investigation task
        identifier_values = [task_str]
        for key in ("discovered_identifiers", "code_identifiers", "function_names", "class_names", "routes", "endpoints"):
            value = actual_context.get(key) or []
            identifier_values.extend(value if isinstance(value, (list, tuple, set)) else [value])
        for key in ("relevant_files", "target_files", "symptom_sites"):
            value = actual_context.get(key) or []
            for entry in value if isinstance(value, list) else [value]:
                path = _as_path(entry)
                if path:
                    identifier_values.append(PurePosixPath(path).stem)

        # Incorporate prior findings and evidence from dependent tasks if available
        prior_findings = actual_context.get("prior_findings") or []
        for pf in prior_findings if isinstance(prior_findings, list) else []:
            if isinstance(pf, dict):
                stmt = str(pf.get("statement") or pf.get("finding") or pf.get("description") or "")
                if stmt:
                    identifier_values.append(stmt)
                fp = pf.get("file_path")
                if fp:
                    identifier_values.append(PurePosixPath(fp).stem)

        # NOTE (Phase 4): ZERO evidence items or search identifiers originate from context.get("evidence_candidates")
        identifiers = _extract_identifiers(" ".join(str(v) for v in identifier_values if v))
        evidence: list[dict] = []

        # 1. Collect relevant paths from task scope and shared context
        candidates = []

        task_scope = actual_task_dict.get("scope") or actual_task_dict.get("target_files") or []
        if isinstance(task_scope, (list, tuple)):
            for item in task_scope:
                path = _as_path(item)
                if path and _safe_relative_path(path) and path not in candidates:
                    candidates.append(path)
        elif isinstance(task_scope, str):
            path = _as_path(task_scope)
            if path and _safe_relative_path(path) and path not in candidates:
                candidates.append(path)

        for key in ("relevant_files", "target_files", "symptom_sites"):
            values = actual_context.get(key, []) or []
            if not isinstance(values, list):
                continue
            for value in values:
                path = _as_path(value)
                if (
                    path
                    and _safe_relative_path(path)
                    and path not in candidates
                ):
                    candidates.append(path)

        # Phase 10 Guardrail: Maximum 10 tool calls per investigator execution
        tracker = ToolCallTracker(max_calls=10, investigator_name=self.investigator_name, task_id=task_id)

        # 2. Discover source files across the repository.
        listing, list_errors = safe_list(workspace_path, self.list_files_tool, tracker=tracker)
        if list_errors:
            result["tool_errors"].extend(list_errors)
        else:
            for item in listing:
                if item.get("type") == "directory":
                    continue

                path = _as_path(item)

                if not path or not _safe_relative_path(path):
                    continue

                if any(
                    part in IGNORED_PARTS
                    for part in PurePosixPath(path).parts
                ):
                    continue

                if _source_extension(path) and path not in candidates:
                    candidates.append(path)

        candidates = list(dict.fromkeys(candidates))[:self.max_files]

        # 3. Read discovered files.
        source_lines: dict[str, list[str]] = {}

        for path in candidates:
            if not tracker.can_call():
                break

            content, error = self._read(workspace_path, path, tracker=tracker)

            if error:
                result["tool_errors"].append(error)
                continue

            if content:
                source_lines[path.replace("\\", "/")] = content.splitlines()

        # 4. Find task identifiers in exact source lines.
        for path, lines in source_lines.items():
            for number, line in enumerate(lines, start=1):
                if not line.strip():
                    continue

                matching = [
                    term for term in identifiers
                    if term.lower() in line.lower()
                ]

                if not matching:
                    continue

                relationship = _relationship_kind(line)

                evidence.append({
                    "source": path,
                    "line": number,
                    "snippet": line.strip()[:MAX_SNIPPET_CHARS],
                    "kind": (
                        "dependency_match"
                        if relationship in {
                            "import_reference",
                            "data_access_reference",
                        }
                        else "call_reference"
                    ),
                    "relationship": relationship,
                    "matched_terms": matching,
                })

                if len(evidence) >= self.max_evidence:
                    break

            if len(evidence) >= self.max_evidence:
                break

        # 5. Search for inbound/outbound references.
        for term in identifiers:
            if len(evidence) >= self.max_evidence:
                break
            if not tracker.can_call():
                break

            hits, errors = self._search(workspace_path, term, tracker=tracker)
            result["tool_errors"].extend(errors)

            for hit in hits:
                path = _as_path(hit)

                snippet = (
                    hit.get("matched_text")
                    or hit.get("content")
                    or hit.get("text")
                    or hit.get("snippet")
                )

                if (
                    not path
                    or not isinstance(snippet, str)
                    or not snippet.strip()
                    or not _safe_relative_path(path)
                    or _is_non_application_path(path)
                ):
                    continue

                relationship = _relationship_kind(snippet)

                evidence.append({
                    "source": path,
                    "line": _line_number(
                        hit.get("line") or hit.get("line_number")
                    ),
                    "snippet": snippet.strip()[:MAX_SNIPPET_CHARS],
                    "kind": (
                        "dependency_match"
                        if relationship in {
                            "import_reference",
                            "data_access_reference",
                        }
                        else "call_reference"
                    ),
                    "relationship": relationship,
                    "matched_terms": [term],
                })

                if len(evidence) >= self.max_evidence:
                    break

        # 6. Build the local import graph using normalized paths.
        flow_edges = self._build_flow_edges(source_lines)
        result["flow_edges"] = flow_edges

        existing_keys = {
            (item.get("source"), item.get("line"), item.get("snippet"))
            for item in evidence
        }

        for edge in flow_edges:
            key = (edge["source"], edge["line"], edge["snippet"])

            if key in existing_keys:
                continue

            evidence.append({
                "source": edge["source"],
                "line": edge["line"],
                "snippet": edge["snippet"],
                "kind": "dependency_match",
                "relationship": edge["relationship"],
                "matched_terms": [],
            })

            existing_keys.add(key)

            if len(evidence) >= self.max_evidence:
                break

        # 7. Deduplicate evidence and assign stable IDs with full provenance
        evidence = unique_evidence(evidence, limit=self.max_evidence)

        formatted_evidence: list[dict[str, Any]] = []
        for index, item in enumerate(evidence, start=1):
            ev_id = f"EV-DEP-{index:03d}"
            ev_record = {
                "evidence_id": ev_id,
                "task_id": task_id,
                "agent_name": self.agent_name,
                "source": item["source"],
                "line": item["line"],
                "matched_text": item["snippet"],
                "snippet": item["snippet"],
                "kind": item["kind"],
                "relationship": item.get("relationship", "call_reference"),
                "verification_status": "unverified",
                "observed_fact": f"Dependency match in {item['source']} line {item.get('line') or '?'}: {item['snippet'][:100]}",
                "explanation": f"Dependency reference in {item['source']} (line {item.get('line') or '?'}) establishing call/import connectivity.",
            }
            formatted_evidence.append(ev_record)

        result["evidence"] = formatted_evidence
        result["evidence_snippets"] = formatted_evidence

        # 8. Report observed imports as confirmed relationships with provenance
        for edge in flow_edges:
            matching = [
                item for item in formatted_evidence
                if item["source"] == edge["source"]
                and item["line"] == edge["line"]
                and item["snippet"] == edge["snippet"]
            ]
            if not matching:
                continue

            result["findings"].append(_finding(
                CATEGORIES["confirmed"],
                f"Observed import from {edge['source']} to {edge['target_hint']}: `{edge['snippet'][:80]}` attaches module interface.",
                [matching[0]["evidence_id"]],
                confidence="high",
                rationale="The import relationship is directly visible in source code.",
                task_id=task_id,
                agent_name=self.agent_name,
                file_path=edge["source"],
                line_number=edge["line"],
            ))

        # Report relevant source references without asserting they are causal
        paths_with_evidence = {}
        for item in formatted_evidence:
            paths_with_evidence.setdefault(item["source"], []).append(item)

        for path, items in paths_with_evidence.items():
            result["findings"].append(_finding(
                CATEGORIES["confirmed"],
                f"Observed interface references in {path} (line {items[0].get('line') or '?'}): `{items[0]['snippet'][:80]}`.",
                [item["evidence_id"] for item in items],
                confidence="high",
                rationale="The source references are directly observed. Their causal role is not established by static analysis alone.",
                task_id=task_id,
                agent_name=self.agent_name,
                file_path=path,
                line_number=items[0].get("line"),
            ))

        if flow_edges:
            result["findings"].append(_finding(
                CATEGORIES["possible"],
                "Observed import relationships may propagate changes across component boundaries. Verify runtime behavior and configuration before attributing the reported issue.",
                [item["evidence_id"] for item in formatted_evidence if item["kind"] == "dependency_match"],
                confidence="medium",
                task_id=task_id,
                agent_name=self.agent_name,
            ))
        elif len(paths_with_evidence) > 1:
            result["findings"].append(_finding(
                CATEGORIES["possible"],
                "Relevant references were found in multiple files, but a complete dependency chain could not be established.",
                [item["evidence_id"] for item in formatted_evidence],
                confidence="low",
                task_id=task_id,
                agent_name=self.agent_name,
            ))

        if not formatted_evidence:
            result["findings"].append(_finding(
                CATEGORIES["unknown"],
                "No relevant dependency or call-reference evidence was established.",
                confidence="none",
                task_id=task_id,
                agent_name=self.agent_name,
            ))
            result["missing_evidence"].append(
                "Provide a target endpoint, function name, file, or more specific context."
            )

        if not flow_edges:
            result["findings"].append(_finding(
                CATEGORIES["unknown"],
                "No local import edges were resolved. Dynamic imports, framework routing, configuration, and service boundaries may need additional tracing.",
                confidence="none",
                task_id=task_id,
                agent_name=self.agent_name,
            ))

        # 9. Hypotheses: Only generate if plausible cross-component defect mechanism is observed
        app_evidence = [
            it for it in formatted_evidence
            if not _is_non_application_path(it.get("source", ""))
            and not _is_boilerplate_error_handler(it.get("snippet", ""))
        ]

        llm_hypotheses = self._generate_hypotheses_with_llm(
            task_id=task_id,
            task_text=task_str,
            context=actual_context,
            formatted_evidence=app_evidence,
            flow_edges=flow_edges,
        )
        if llm_hypotheses:
            result["hypotheses"].extend(llm_hypotheses)
            for h in llm_hypotheses:
                logger.info(
                    "[Dependency / Impact Investigator] hypothesis_validated (LLM): task_id=%s, hypothesis_id=%s, citing=%s",
                    task_id,
                    h.get("hypothesis_id"),
                    h.get("supporting_evidence_ids"),
                )
        else:
            potential_defect_items = [
                it for it in app_evidence
                if any(term in it["snippet"].lower() for term in ("mismatch", "deprecated", "broken", "unhandled rejection", "incompatible", "schema conflict"))
            ]
            if potential_defect_items:
                distinct_clusters: list[list[dict[str, Any]]] = []
                for item in potential_defect_items:
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

                for idx, cluster in enumerate(distinct_clusters[:3], start=1):
                    lead = cluster[0]
                    cluster_ev_ids = [it["evidence_id"] for it in cluster if "evidence_id" in it]
                    line_no = lead.get("line") or "?"
                    source_file = lead.get("source", "unknown")

                    contra_items = [
                        it for it in app_evidence
                        if it["source"] == source_file
                        and it.get("line") != lead.get("line")
                        and any(term in it["snippet"].lower() for term in ("compatible", "valid", "success", "resolved"))
                    ]

                    hyp_id = f"HYP-{task_id[-8:] if len(task_id) >= 8 else task_id}-{idx:03d}"
                    hyp = create_hypothesis(
                        hypothesis_id=hyp_id,
                        hypothesis_title=f"Cross-component interface discrepancy in {source_file}",
                        produced_by=self.investigator_name,
                        possible_cause=(
                            f"Call sequence or contract reference at {source_file} (line {line_no}) "
                            f"shows interface or parameter discrepancy indicators across component boundaries."
                        ),
                        evidence=f"{source_file}:{line_no} — `{lead['snippet'][:150]}`",
                        reasoning=(
                            "Caller and callee components may rely on incompatible parameters or unhandled failures across the component boundary."
                        ),
                        supporting_evidence=cluster,
                        supporting_evidence_ids=cluster_ev_ids,
                        contradicting_evidence=contra_items,
                        contradicting_evidence_ids=[c["evidence_id"] for c in contra_items if "evidence_id" in c],
                        status="needs_validation",
                        confidence="medium",
                        unanswered_questions=[
                            "Does the caller pass compatible arguments across the component boundary at runtime?",
                            "Is error propagation between these components handled gracefully without causing failure?",
                        ],
                        task_id=task_id,
                        agent_name=self.agent_name,
                    )
                    result["hypotheses"].append(hyp)
                    logger.info(
                        "[Dependency / Impact Investigator] hypothesis_validated: task_id=%s, hypothesis_id=%s, citing=%s",
                        task_id,
                        hyp_id,
                        cluster_ev_ids,
                    )
            else:
                auth_flow_edges = [
                    e for e in flow_edges
                    if any(k in e["source"].lower() or k in e.get("target_hint", "").lower() for k in ("auth", "user", "middleware", "token", "route"))
                ]
                prior_findings_text = " ".join(
                    str(f.get("statement") or f.get("finding") or "") for f in (actual_context.get("prior_findings") or [])
                ).lower()
                has_casing_or_norm = any(k in prior_findings_text or any(k in e.get("snippet", "").lower() for e in app_evidence) for k in ("tolowercase", "casing", "normalization", "normalize", "trim"))

                if auth_flow_edges and has_casing_or_norm:
                    seen_edges = set()
                    for edge in auth_flow_edges:
                        pair = (edge["source"], edge.get("target_hint", ""))
                        if pair in seen_edges:
                            continue
                        seen_edges.add(pair)
                        lead_edge = edge
                        matching_ev = [
                            item for item in formatted_evidence
                            if item["source"] == lead_edge["source"]
                        ]
                        cluster = matching_ev[:3] if matching_ev else formatted_evidence[:2]
                        cluster_ev_ids = [it["evidence_id"] for it in cluster]
                        h_idx = len(result["hypotheses"]) + 1
                        hyp_id = f"HYP-{task_id[-8:] if len(task_id) >= 8 else task_id}-{h_idx:03d}"
                        target_name = lead_edge.get("target_hint", "target component")
                        is_auth_problem = any(
                            k in (actual_context.get("symptoms", "") + " " + task_obj + " " + str(actual_context.get("original_problem", ""))).lower()
                            for k in ("auth", "login", "token", "jwt", "credential", "401", "password", "unauthorized", "bearer")
                        )
                        if is_auth_problem:
                            title = "Cross-module credential propagation and lookup discrepancy" if h_idx == 1 else f"Interface contract discrepancy between {lead_edge['source']} and {target_name}"
                            cause = (
                                f"Cross-module dependency between {lead_edge['source']} and {target_name} "
                                "propagates transformed token claims into a database lookup interface that expects exact match."
                            )
                            reasoning = (
                                f"Authentication middleware extracts token claims originating from authentication service and passes them across module "
                                f"boundaries to {target_name}. Discrepancies between value normalization in token generation and query criteria in models "
                                "cause authenticated requests to fail with HTTP 401 Unauthorized."
                            )
                            unanswered = [
                                f"Does the receiving model query in {target_name} perform case-sensitive text comparison?",
                                "Is the token payload email normalized before or during token generation?",
                            ]
                        else:
                            title = f"Interface contract discrepancy between {lead_edge['source']} and {target_name}"
                            cause = (
                                f"Cross-module dependency between {lead_edge['source']} and {target_name} "
                                f"propagates data across component boundaries without verifying contract conformity."
                            )
                            reasoning = (
                                f"Component {lead_edge['source']} invokes or imports {target_name}. "
                                f"Data transformations or mismatched schema contracts across this boundary may cause downstream errors or corrupted state."
                            )
                            unanswered = [
                                f"Does the interface or schema in {target_name} match the expected contract from {lead_edge['source']}?",
                                f"Are required parameters correctly formatted when passed across the {lead_edge['source']} -> {target_name} boundary?",
                            ]
                        hyp = create_hypothesis(
                            hypothesis_id=hyp_id,
                            hypothesis_title=title,
                            produced_by=self.investigator_name,
                            possible_cause=cause,
                            evidence=f"{lead_edge['source']} -> {target_name}: `{lead_edge['snippet'][:120]}`",
                            reasoning=reasoning,
                            supporting_evidence=cluster,
                            supporting_evidence_ids=cluster_ev_ids,
                            status="needs_validation",
                            confidence="medium",
                            unanswered_questions=unanswered,
                            task_id=task_id,
                            agent_name=self.agent_name,
                        )
                        result["hypotheses"].append(hyp)
                        logger.info(
                            "[Dependency / Impact Investigator] hypothesis_validated (flow): task_id=%s, hypothesis_id=%s, citing=%s",
                            task_id,
                            hyp_id,
                            cluster_ev_ids,
                        )
                        if len(result["hypotheses"]) >= 2:
                            break
                else:
                    result["hypotheses"] = []
                    result["unanswered_questions"].append(
                        f"No cross-component defect mechanism or interface mismatch was observed in the dependency graph for task {task_id}."
                    )
                    logger.info(
                        "[Dependency / Impact Investigator] No plausible defect mechanism observed in dependencies; hypotheses list is empty (task_id=%s).",
                        task_id,
                    )

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
            assessments = {"status": "unresolved", "summary": "No dependency defect mechanism confirmed"}
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
            "[Dependency / Impact Investigator] specialist_task_complete: task_id=%s, agent_name=%s, status=%s",
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

    def _search(
        self,
        workspace: str,
        query: str,
        tracker: Any = None,
    ) -> tuple[list[dict], list[dict]]:
        return safe_search(
            workspace,
            query,
            self.search_code_tool,
            max_results=15,
            tracker=tracker,
        )

    @staticmethod
    def _build_flow_edges(
        source_lines: dict[str, list[str]],
    ) -> list[dict]:
        """Build edges for imports that resolve to known repository files."""
        known_paths = {
            path.replace("\\", "/")
            for path in source_lines
        }

        edges = []

        for source, lines in source_lines.items():
            source = source.replace("\\", "/")

            for number, line in enumerate(lines, start=1):
                # JS/TS import, dynamic import and require.
                modules = re.findall(
                    r"""(?:from\s*|import\s*\(|require\s*\()\s*['"]([^'"]+)['"]""",
                    line,
                )

                # Python from-package import.
                modules.extend(re.findall(
                    r"^\s*from\s+([A-Za-z0-9_.]+)\s+import\b",
                    line,
                ))

                for module in modules:
                    targets = _resolve_import_targets(
                        source,
                        module,
                        known_paths,
                    )

                    for target in sorted(targets):
                        if source == target:
                            continue

                        edges.append({
                            "source": source,
                            "target_hint": target,
                            "line": number,
                            "snippet": line.strip()[:MAX_SNIPPET_CHARS],
                            "relationship": "imports",
                        })

        unique = {}

        for edge in edges:
            key = (
                edge["source"],
                edge["target_hint"],
                edge["line"],
                edge["relationship"],
            )
            unique[key] = edge

        return list(unique.values())

    def _generate_hypotheses_with_llm(
        self,
        task_id: str,
        task_text: str,
        context: dict,
        formatted_evidence: list[dict],
        flow_edges: list[dict],
    ) -> list[dict]:
        """Generate cross-component hypotheses using structured LLM synthesis if available."""
        if not self.llm_factory:
            return []
        try:
            llm = self.llm_factory()
            if not llm:
                return []
            structured = llm.with_structured_output(DependencyHypothesesPayload)

            app_ev = [
                e for e in formatted_evidence
                if not _is_non_application_path(e.get("source", ""))
                and not _is_boilerplate_error_handler(e.get("snippet", ""))
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
                }
                for e in app_ev
            ]
            edge_summary = [
                {
                    "source": edge.get("source"),
                    "target": edge.get("target_hint"),
                    "relationship": edge.get("relationship"),
                }
                for edge in flow_edges[:10]
            ]

            prob_text = context.get("problem") or context.get("problem_summary") or task_text
            prompt = (
                f"You are a Senior Dependency & Impact Investigator analyzing component interactions for a software issue.\n\n"
                f"Investigation Task: {task_text}\n"
                f"Problem Statement: {prob_text}\n\n"
                f"Observed Component Dependencies:\n{json.dumps(edge_summary, indent=2)}\n\n"
                f"Observed Dependency Evidence ({len(ev_summary)} items):\n"
                f"{json.dumps(ev_summary, indent=2)}\n\n"
                f"Instructions:\n"
                f"1. Examine the cross-component flow, imports, callers, and middleware chains.\n"
                f"2. Identify any plausible defect mechanism that crosses component boundaries (e.g., mismatched parameter expectations, token verification contract mismatch, or unhandled cross-component error propagation).\n"
                f"3. Cite the exact source file and line locations in cited_sources.\n"
                f"4. Explain step-by-step how the interaction causes the reported failure symptom.\n"
                f"5. If the dependencies and interactions operate as standard boilerplate with no defect mechanism, return an empty hypotheses list. Do NOT blame generic error handlers or standard imports."
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
                        "Does runtime execution pass incompatible parameters across this component boundary?"
                    ],
                    task_id=task_id,
                    agent_name=self.agent_name,
                )
                hypotheses.append(hyp)
            return hypotheses
        except Exception as exc:
            logger.debug("LLM dependency hypothesis generation fallback: %s", exc)
            return []


def run_dependency_impact_investigator(
    investigation_task: str,
    relevant_context: dict | None,
    current_state: dict | None,
    *,
    list_files_tool: Any = list_files,
    search_code_tool: Any = search_code,
    read_file_tool: Any = read_file,
    llm_factory: Optional[Callable] = None,
) -> dict:
    """Backward-compatible entry point for the existing workflow."""
    state = _as_dict(current_state)
    context = _as_dict(relevant_context)
    workspace = resolve_workspace(state, context)

    combined_context = dict(context)
    combined_context.setdefault("problem", state.get("problem", ""))
    combined_context.setdefault(
        "gathered_context",
        state.get("gathered_context", {}),
    )

    investigator = DependencyImpactInvestigator(
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
