"""Shared contracts and safe helpers for specialized investigators.

Keep this module independent from LangGraph and from any particular LLM provider.
Specialized investigators may return dictionaries or Pydantic models; the
orchestrator should normalize both forms at its boundary.
"""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Callable, Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field


STOP_WORDS = {
    "the", "and", "that", "this", "with", "from", "after", "before",
    "when", "then", "into", "have", "has", "been", "does", "did",
    "not", "but", "for", "are", "was", "were", "its", "page",
    "application", "project", "problem", "issue", "error", "working",
    "works", "using", "used", "should", "would", "could", "where",
    "which", "what", "how", "why", "their", "there", "they", "them",
    "some", "also", "only", "than", "while", "through", "instead",
    "expected", "observed", "returns", "return", "opening", "open",
    "your", "our", "an", "to", "of", "in", "on", "is", "it", "as",
    "inspect", "inspecting", "trace", "tracing", "identify", "identifying",
    "verify", "verifying", "check", "checking", "analyze", "analyzing",
    "handler", "invocation", "succeeds", "succeed", "matching", "match",
    "code", "file", "files", "additional", "developer", "context", "reported",
}



def as_text(value: Any) -> str:
    """Convert a value to text without turning None into the string 'None'."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def resolve_workspace(
    current_state: Optional[dict],
    relevant_context: Optional[dict],
) -> str:
    """Resolve a workspace path from the state or gathered shared context."""
    state = current_state or {}
    context = relevant_context or {}

    for source in (state, context):
        for key in ("workspace_path", "workspace"):
            value = source.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

    return ""


def extract_terms(text: str, limit: int = 8) -> List[str]:
    """Extract distinct, non-generic terms useful for repository searches."""
    if limit <= 0 or not text:
        return []

    raw_terms = re.findall(
        r"[A-Za-z_$][A-Za-z0-9_$./:-]{2,}|\\b\\d{3,}\\b",
        text,
    )

    seen = set()
    terms: List[str] = []

    for raw in raw_terms:
        term = raw.strip(".,:;()[]{}<>\\\"'")
        if not term:
            continue

        key = term.lower()
        # Ignore a generic term, and also ignore a generic path-like token
        # when its final component is generic.
        final_component = key.replace("\\\\", "/").rsplit("/", 1)[-1]
        if key in STOP_WORDS or final_component in STOP_WORDS or key in seen:
            continue

        seen.add(key)
        terms.append(term)
        if len(terms) >= limit:
            break

    return terms


def normalize_items(raw: Any) -> List[dict]:
    """Normalize common list/search tool response shapes into dictionaries."""
    if raw is None:
        return []

    if isinstance(raw, BaseModel):
        raw = raw.model_dump()

    if isinstance(raw, list):
        return [
            item.model_dump() if isinstance(item, BaseModel)
            else item if isinstance(item, dict)
            else {"content": as_text(item)}
            for item in raw
        ]

    if not isinstance(raw, dict):
        return []

    for key in ("results", "entries", "files", "items", "matches"):
        value = raw.get(key)
        if isinstance(value, list):
            return [
                item.model_dump() if isinstance(item, BaseModel)
                else item if isinstance(item, dict)
                else {"content": as_text(item)}
                for item in value
            ]

    return [raw]


def _normalized_source(value: Any) -> str:
    """Normalize a source path for evidence comparison without resolving it."""
    return as_text(value).strip().replace("\\\\", "/").casefold()


def unique_evidence(items: List[dict], limit: int = 30) -> List[dict]:
    """Deduplicate evidence using normalized source, line, kind and snippet."""
    if limit <= 0:
        return []

    seen = set()
    result: List[dict] = []

    for item in items:
        if not isinstance(item, dict):
            continue

        text = as_text(
            item.get("snippet")
            or item.get("matched_text")
            or item.get("content")
            or ""
        ).strip()

        key = (
            _normalized_source(
                item.get("source")
                or item.get("path")
                or item.get("file")
                or item.get("filename")
            ),
            item.get("line") or item.get("line_number") or item.get("lineno"),
            text.casefold(),
        )

        if key in seen:
            continue

        seen.add(key)
        result.append(item)
        if len(result) >= limit:
            break

    return result


class ToolCallTracker:
    """Thread-safe tool-call budget and telemetry tracker for an investigator execution.
    
    Enforces a strict maximum of tool calls (default 10) per execution pass.
    Records structured telemetry for both successful and failed tool calls.
    """

    def __init__(self, max_calls: int = 10, investigator_name: str = "", task_id: str = ""):
        self.max_calls = max(1, int(max_calls))
        self.investigator_name = investigator_name
        self.task_id = task_id
        self.call_count: int = 0
        self.limit_reached: bool = False
        self.executions: List[Dict[str, Any]] = []

    @property
    def tool_call_count(self) -> int:
        return self.call_count

    @property
    def is_limit_reached(self) -> bool:
        return self.limit_reached or (self.call_count >= self.max_calls)

    def can_call(self) -> bool:
        """Check if an additional tool call is allowed under the budget."""
        if self.call_count >= self.max_calls:
            self.limit_reached = True
            return False
        return True

    def record_limit_reached(
        self,
        tool: str = "tool",
        arguments: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Record when a tool call attempt is blocked because the limit was reached."""
        self.limit_reached = True
        return self.record_execution(
            tool=tool,
            input_args=arguments or {},
            status="blocked_limit_reached",
            duration_ms=0.0,
            output_summary="",
            error=f"Tool call limit ({self.max_calls}) reached",
        )

    def record_execution(
        self,
        tool: str = "",
        input_args: Optional[Dict[str, Any]] = None,
        status: str = "success",
        duration_ms: float = 0.0,
        output_summary: str = "",
        error: Optional[str] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Record a structured tool execution record."""
        tool_name = tool or kwargs.get("tool_name", "unknown")
        args_dict = input_args if input_args is not None else kwargs.get("arguments", {})
        summary = output_summary or str(kwargs.get("output", ""))

        # Sanitize and redact sensitive arguments
        sanitized_args: Dict[str, Any] = {}
        for k, v in (args_dict or {}).items():
            if any(secret in k.lower() for secret in ("key", "secret", "password", "token", "auth")):
                sanitized_args[k] = "[REDACTED]"
            elif isinstance(v, str) and len(v) > 200:
                sanitized_args[k] = v[:200] + "... [truncated]"
            else:
                sanitized_args[k] = v

        if status != "blocked_limit_reached" and len(self.executions) >= self.call_count:
            self.call_count += 1
        if self.call_count >= self.max_calls:
            self.limit_reached = True

        record = {
            "tool": tool_name,
            "tool_name": tool_name,
            "investigator": self.investigator_name,
            "task_id": self.task_id,
            "arguments": sanitized_args,
            "status": status,
            "duration_ms": round(duration_ms, 2),
            "output_summary": summary[:300] if summary else "",
            "error": error,
            "call_index": self.call_count,
        }
        self.executions.append(record)
        return record


def safe_search(
    workspace: str,
    query: str,
    search_tool: Callable[..., Any],
    *,
    max_results: int = 10,
    tracker: Optional[ToolCallTracker] = None,
) -> Tuple[List[dict], List[dict]]:
    """Call search_code safely and normalize its response with budget & telemetry."""
    if not workspace.strip() or not query.strip():
        return [], []

    if tracker is not None:
        if not tracker.can_call():
            err_dict = {
                "tool": "search_code",
                "query": query,
                "error": f"Tool call limit reached ({tracker.max_calls} calls maximum per investigator execution).",
                "limit_reached": True,
            }
            tracker.record_execution(
                tool="search_code",
                input_args={"workspace": workspace, "query": query, "max_results": max_results},
                status="blocked",
                duration_ms=0.0,
                output_summary="Call blocked by guardrail tool-call limit",
                error=err_dict["error"],
            )
            return [], [err_dict]
        tracker.call_count += 1

    import time
    t0 = time.time()
    try:
        response = search_tool(
            workspace,
            query,
            max_results=max(1, max_results),
        )
        duration_ms = (time.time() - t0) * 1000.0

        if isinstance(response, dict) and response.get("success") is False:
            err_msg = as_text(response.get("error") or "Search failed")
            if tracker is not None:
                tracker.record_execution(
                    tool="search_code",
                    input_args={"workspace": workspace, "query": query, "max_results": max_results},
                    status="failed",
                    duration_ms=duration_ms,
                    output_summary=f"Failed search: {err_msg}",
                    error=err_msg,
                )
            return [], [{
                "tool": "search_code",
                "query": query,
                "error": err_msg,
            }]

        items = normalize_items(response)
        if tracker is not None:
            tracker.record_execution(
                tool="search_code",
                input_args={"workspace": workspace, "query": query, "max_results": max_results},
                status="success",
                duration_ms=duration_ms,
                output_summary=f"Found {len(items)} matches for query '{query[:50]}'",
            )
        return items, []

    except Exception as exc:
        duration_ms = (time.time() - t0) * 1000.0
        err_msg = f"{type(exc).__name__}: {exc}"
        if tracker is not None:
            tracker.record_execution(
                tool="search_code",
                input_args={"workspace": workspace, "query": query, "max_results": max_results},
                status="error",
                duration_ms=duration_ms,
                output_summary="Exception during search_code",
                error=err_msg,
            )
        return [], [{
            "tool": "search_code",
            "query": query,
            "error": err_msg,
        }]


def _validate_relative_path(workspace: str, path: str) -> Optional[str]:
    """Reject absolute paths and traversal before passing a path to a reader.

    This is lexical validation. The actual filesystem reader should additionally
    resolve the candidate path and verify it remains under the workspace root,
    especially when symlinks may exist.
    """
    if not workspace or not path or "\x00" in path:
        return "Workspace or file path is missing or invalid"

    normalized = path.replace("\\", "/")
    posix_path = PurePosixPath(normalized)
    windows_path = PureWindowsPath(path)

    if (
        posix_path.is_absolute()
        or windows_path.is_absolute()
        or windows_path.drive
        or ".." in posix_path.parts
        or ".." in windows_path.parts
    ):
        return "Unsafe relative path rejected"

    return None


def safe_read(
    workspace: str,
    path: str,
    read_tool: Callable[..., Any],
    *,
    tracker: Optional[ToolCallTracker] = None,
) -> Tuple[str, Optional[dict]]:
    """Read a source file and return its content plus a structured error with budget & telemetry."""
    if tracker is not None:
        if not tracker.can_call():
            err_dict = {
                "tool": "read_file",
                "path": path,
                "error": f"Tool call limit reached ({tracker.max_calls} calls maximum per investigator execution).",
                "limit_reached": True,
            }
            tracker.record_execution(
                tool="read_file",
                input_args={"workspace": workspace, "path": path},
                status="blocked",
                duration_ms=0.0,
                output_summary="Call blocked by guardrail tool-call limit",
                error=err_dict["error"],
            )
            return "", err_dict
        tracker.call_count += 1

    validation_error = _validate_relative_path(workspace, path)
    if validation_error:
        err_dict = {
            "tool": "read_file",
            "path": path,
            "error": validation_error,
        }
        if tracker is not None:
            tracker.record_execution(
                tool="read_file",
                input_args={"workspace": workspace, "path": path},
                status="failed",
                duration_ms=0.0,
                output_summary="Lexical validation rejected path",
                error=validation_error,
            )
        return "", err_dict

    import time
    t0 = time.time()
    try:
        response = read_tool(workspace, path)
        duration_ms = (time.time() - t0) * 1000.0

        if isinstance(response, BaseModel):
            response = response.model_dump()

        if isinstance(response, dict):
            if response.get("success") is False or response.get("error"):
                err_msg = as_text(response.get("error") or "Read failed")
                if tracker is not None:
                    tracker.record_execution(
                        tool="read_file",
                        input_args={"workspace": workspace, "path": path},
                        status="failed",
                        duration_ms=duration_ms,
                        output_summary=f"Read failed: {err_msg}",
                        error=err_msg,
                    )
                return "", {
                    "tool": "read_file",
                    "path": path,
                    "error": err_msg,
                }

            content = response.get("content")
            if content is None:
                err_msg = "Reader response did not contain a content field"
                if tracker is not None:
                    tracker.record_execution(
                        tool="read_file",
                        input_args={"workspace": workspace, "path": path},
                        status="failed",
                        duration_ms=duration_ms,
                        output_summary=err_msg,
                        error=err_msg,
                    )
                return "", {
                    "tool": "read_file",
                    "path": path,
                    "error": err_msg,
                }
            text_content = as_text(content)
            if tracker is not None:
                tracker.record_execution(
                    tool="read_file",
                    input_args={"workspace": workspace, "path": path},
                    status="success",
                    duration_ms=duration_ms,
                    output_summary=f"Read {len(text_content)} chars from {path}",
                )
            return text_content, None

        if response is None:
            err_msg = "Reader returned no content"
            if tracker is not None:
                tracker.record_execution(
                    tool="read_file",
                    input_args={"workspace": workspace, "path": path},
                    status="failed",
                    duration_ms=duration_ms,
                    output_summary=err_msg,
                    error=err_msg,
                )
            return "", {
                "tool": "read_file",
                "path": path,
                "error": err_msg,
            }

        text_content = as_text(response)
        if tracker is not None:
            tracker.record_execution(
                tool="read_file",
                input_args={"workspace": workspace, "path": path},
                status="success",
                duration_ms=duration_ms,
                output_summary=f"Read {len(text_content)} chars from {path}",
            )
        return text_content, None

    except Exception as exc:
        duration_ms = (time.time() - t0) * 1000.0
        err_msg = f"{type(exc).__name__}: {exc}"
        if tracker is not None:
            tracker.record_execution(
                tool="read_file",
                input_args={"workspace": workspace, "path": path},
                status="error",
                duration_ms=duration_ms,
                output_summary="Exception during read_file",
                error=err_msg,
            )
        return "", {
            "tool": "read_file",
            "path": path,
            "error": err_msg,
        }


def safe_list(
    workspace: str,
    list_tool: Callable[..., Any],
    *,
    subpath: str = "",
    tracker: Optional[ToolCallTracker] = None,
) -> Tuple[List[dict], List[dict]]:
    """Call list_files safely and normalize its response with budget & telemetry."""
    if not workspace.strip():
        return [], []

    if tracker is not None:
        if not tracker.can_call():
            err_dict = {
                "tool": "list_files",
                "path": subpath,
                "error": f"Tool call limit reached ({tracker.max_calls} calls maximum per investigator execution).",
                "limit_reached": True,
            }
            tracker.record_execution(
                tool="list_files",
                input_args={"workspace": workspace, "path": subpath},
                status="blocked",
                duration_ms=0.0,
                output_summary="Call blocked by guardrail tool-call limit",
                error=err_dict["error"],
            )
            return [], [err_dict]
        tracker.call_count += 1

    import time
    t0 = time.time()
    try:
        response = list_tool(workspace) if not subpath else list_tool(workspace, path=subpath)
        duration_ms = (time.time() - t0) * 1000.0

        if isinstance(response, dict) and response.get("success") is False:
            err_msg = as_text(response.get("error") or "File discovery failed")
            if tracker is not None:
                tracker.record_execution(
                    tool="list_files",
                    input_args={"workspace": workspace, "path": subpath},
                    status="failed",
                    duration_ms=duration_ms,
                    output_summary=f"Failed listing: {err_msg}",
                    error=err_msg,
                )
            return [], [{
                "tool": "list_files",
                "error": err_msg,
            }]

        items = normalize_items(response)
        if tracker is not None:
            tracker.record_execution(
                tool="list_files",
                input_args={"workspace": workspace, "path": subpath},
                status="success",
                duration_ms=duration_ms,
                output_summary=f"Discovered {len(items)} file/directory entries",
            )
        return items, []

    except Exception as exc:
        duration_ms = (time.time() - t0) * 1000.0
        err_msg = f"{type(exc).__name__}: {exc}"
        if tracker is not None:
            tracker.record_execution(
                tool="list_files",
                input_args={"workspace": workspace, "path": subpath},
                status="error",
                duration_ms=duration_ms,
                output_summary="Exception during list_files",
                error=err_msg,
            )
        return [], [{
            "tool": "list_files",
            "error": err_msg,
        }]



def search_result_evidence(
    item: dict,
    kind: str = "code_match",
) -> Optional[dict]:
    """Convert a search match into a normalized, initially unverified record."""
    if not isinstance(item, dict):
        return None

    path = as_text(
        item.get("path")
        or item.get("file")
        or item.get("source")
        or item.get("filename")
    ).strip()

    if not path:
        return None

    raw_line = item.get("line") or item.get("line_number") or item.get("lineno")
    try:
        line = int(raw_line) if raw_line is not None else None
        if line is not None and line <= 0:
            line = None
    except (TypeError, ValueError):
        line = None

    content = as_text(
        item.get("matched_text")
        or item.get("match")
        or item.get("content")
        or item.get("line_text")
        or item.get("text")
        or item.get("snippet")
    ).strip()

    if not content:
        return None

    return {
        "source": path.replace("\\\\", "/"),
        "line": line,
        "matched_text": content,
        "kind": kind,
        "verification_status": "unverified",
    }


# ---------------------------------------------------------------------------
# Investigator data contracts & SQL Classification
# ---------------------------------------------------------------------------

def classify_sql_statement(snippet: str) -> Optional[Literal["read_query", "write_operation"]]:
    """
    Strict SQL statement classification according to Implementation Plan v4.2:
    - SELECT statements strictly classified as 'read_query'.
    - INSERT, UPDATE, DELETE statements strictly classified as 'write_operation'.
    - Never classify write operations as lookups or query sites.
    """
    if not snippet or not isinstance(snippet, str):
        return None

    clean = snippet.strip()

    # Write operations: INSERT, UPDATE, DELETE
    has_insert = bool(re.search(r"\bINSERT(?:\s+INTO)?\b", clean, re.IGNORECASE))
    has_update = bool(re.search(r"\bUPDATE\b", clean, re.IGNORECASE))
    has_delete = bool(re.search(r"\bDELETE(?:\s+FROM)?\b", clean, re.IGNORECASE))

    if has_insert or has_update or has_delete:
        return "write_operation"

    # Read queries: SELECT
    if re.search(r"\bSELECT\b", clean, re.IGNORECASE):
        return "read_query"

    return None


class InvestigatorInput(BaseModel):
    """Input contract shared by specialized investigators conforming to Phase 4."""

    model_config = ConfigDict(extra="ignore")

    workspace_path: str = Field(min_length=1)
    task: Optional[Dict[str, Any]] = None
    investigation_task: Optional[str] = None
    shared_context: Optional[Dict[str, Any]] = None

    def get_task_dict(self) -> Dict[str, Any]:
        """Returns the full task dictionary, synthesizing minimal structure if only text was passed."""
        if isinstance(self.task, dict):
            return dict(self.task)
        task_text = str(self.investigation_task or "").strip()
        return {
            "task_id": "TASK-DEFAULT-001",
            "objective": task_text or "Investigate repository",
            "assigned_agent": "code_investigator",
            "reason": "Investigate reported issue",
            "input": {"problem": task_text},
            "scope": [],
            "instructions": task_text,
            "expected_output": "Evidence and findings",
            "completion_criteria": "Inspection completed",
            "status": "pending",
        }


class Finding(BaseModel):
    """A claim produced by an investigator; category describes certainty."""

    model_config = ConfigDict(extra="ignore")

    category: Literal[
        "Confirmed Finding",
        "Possible Cause",
        "Assumption",
        "Unknown",
    ]
    description: str = Field(min_length=1)
    file_path: Optional[str] = None
    line_number: Optional[int] = Field(default=None, ge=1)
    snippet: Optional[str] = None
    evidence_ids: List[str] = Field(default_factory=list)
    confidence: str = "medium"
    rationale: Optional[str] = None
    task_id: Optional[str] = None
    agent_name: Optional[str] = None


class EvidenceRecord(BaseModel):
    """A traceable evidence item with provenance tagging."""

    model_config = ConfigDict(extra="ignore")

    task_id: Optional[str] = None
    agent_name: Optional[str] = None
    source: str = Field(min_length=1)
    line: Optional[int] = Field(default=None, ge=1)
    matched_text: str = Field(min_length=1)
    kind: str = "code_match"
    sql_type: Optional[Literal["read_query", "write_operation"]] = None
    verification_status: Literal[
        "unverified",
        "verified",
        "rejected",
        "source_validated",
    ] = "unverified"
    explanation: str = Field(default="", description="Explanation of what this evidence establishes")


class InvestigatorResult(BaseModel):
    """Normalized result contract returned by a specialized investigator conforming to Phase 4 & Phase 8."""

    model_config = ConfigDict(extra="ignore")

    task_id: str = ""
    agent_name: str = ""
    objective: str = Field(default="", description="Neutral objective/question the task must answer")
    status: Literal["completed", "partial", "failed", "unresolved"] = "completed"
    findings: List[Finding] = Field(default_factory=list)
    evidence: List[EvidenceRecord] = Field(default_factory=list)
    hypotheses: List[Dict[str, Any]] = Field(default_factory=list)
    hypothesis_assessment: Dict[str, Any] = Field(default_factory=dict, description="Assessment of hypotheses: supported, contradicted, or unresolved")
    confidence: str = "medium"
    verification_status: str = "unverified"
    supporting_evidence: List[str] = Field(default_factory=list)
    contradicting_evidence: List[str] = Field(default_factory=list)
    limitations: List[str] = Field(default_factory=list)
    errors: List[Dict[str, Any]] = Field(default_factory=list)
    unanswered_questions: List[str] = Field(default_factory=list)
    missing_evidence: List[str] = Field(default_factory=list)
    evidence_gaps: List[str] = Field(default_factory=list)

    # Backward compatibility aliases
    investigator_name: str = ""
    evidence_snippets: List[str] = Field(default_factory=list)
    tool_errors: List[Dict[str, Any]] = Field(default_factory=list)
    summary: str = ""


# ---------------------------------------------------------------------------
# Phase 7 Hypothesis Contract & Factory
# ---------------------------------------------------------------------------

class HypothesisRecord(BaseModel):
    """Structured hypothesis record conforming to Phase 7 specification."""

    model_config = ConfigDict(extra="ignore")

    hypothesis_id: str = Field(min_length=1)
    hypothesis_title: str = Field(min_length=1)
    produced_by: str = Field(min_length=1)
    possible_cause: str = Field(min_length=1)
    evidence: str = Field(default="")
    reasoning: str = Field(min_length=1)
    supporting_evidence: List[Any] = Field(default_factory=list)
    contradicting_evidence: List[Any] = Field(default_factory=list)
    status: Literal["needs_validation", "supported", "contradicted"] = "needs_validation"
    confidence: Literal["low", "medium", "high"] = "medium"
    unanswered_questions: List[str] = Field(default_factory=list)

    # Backward compatibility fields
    statement: str = ""
    category: str = "Possible Cause"
    task_id: Optional[str] = None
    agent_name: Optional[str] = None
    supporting_evidence_ids: List[str] = Field(default_factory=list)
    contradicting_evidence_ids: List[str] = Field(default_factory=list)
    verification_status: str = "unverified"
    contributing_specialists: List[str] = Field(default_factory=list)


def create_hypothesis(
    hypothesis_id: str,
    hypothesis_title: str,
    produced_by: str,
    possible_cause: str,
    evidence: str,
    reasoning: str,
    *,
    supporting_evidence: Optional[List[Any]] = None,
    contradicting_evidence: Optional[List[Any]] = None,
    supporting_evidence_ids: Optional[List[str]] = None,
    contradicting_evidence_ids: Optional[List[str]] = None,
    status: Literal["needs_validation", "supported", "contradicted"] = "needs_validation",
    confidence: Literal["low", "medium", "high"] = "medium",
    unanswered_questions: Optional[List[str]] = None,
    task_id: Optional[str] = None,
    agent_name: Optional[str] = None,
    contributing_specialists: Optional[List[str]] = None,
    statement: Optional[str] = None,
) -> Dict[str, Any]:
    """Create a validated, structured hypothesis dictionary conforming to Phase 7.

    Guarantees both Phase 7 structured fields and backward compatibility keys.
    """
    supp_ev = list(supporting_evidence or [])
    contra_ev = list(contradicting_evidence or [])
    supp_ids = list(supporting_evidence_ids or [])
    contra_ids = list(contradicting_evidence_ids or [])

    # Auto-extract evidence IDs if passed directly in supporting_evidence
    for item in supp_ev:
        if isinstance(item, dict) and "evidence_id" in item:
            eid = str(item["evidence_id"])
            if eid not in supp_ids:
                supp_ids.append(eid)
        elif isinstance(item, str) and item.startswith("EV-"):
            if item not in supp_ids:
                supp_ids.append(item)

    for item in contra_ev:
        if isinstance(item, dict) and "evidence_id" in item:
            eid = str(item["evidence_id"])
            if eid not in contra_ids:
                contra_ids.append(eid)
        elif isinstance(item, str) and item.startswith("EV-"):
            if item not in contra_ids:
                contra_ids.append(item)

    resolved_agent = agent_name or produced_by.lower().replace(" ", "_").replace("/", "_")
    resolved_stmt = statement or f"{hypothesis_title}: {possible_cause}"
    contributors = list(contributing_specialists or [produced_by])

    record = HypothesisRecord(
        hypothesis_id=hypothesis_id,
        hypothesis_title=hypothesis_title,
        produced_by=produced_by,
        possible_cause=possible_cause,
        evidence=evidence,
        reasoning=reasoning,
        supporting_evidence=supp_ev,
        contradicting_evidence=contra_ev,
        status=status,
        confidence=confidence,
        unanswered_questions=list(unanswered_questions or []),
        statement=resolved_stmt,
        category="Possible Cause",
        task_id=task_id,
        agent_name=resolved_agent,
        supporting_evidence_ids=supp_ids,
        contradicting_evidence_ids=contra_ids,
        verification_status="unverified" if status == "needs_validation" else status,
        contributing_specialists=contributors,
    )
    return record.model_dump()


