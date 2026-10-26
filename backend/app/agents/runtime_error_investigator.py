
"""Phase 5: Runtime / Error Investigator.

Parses runtime logs, stack traces, and HTTP failures; maps stack frames
to repository source files; and reports evidence-backed findings.
"""

from __future__ import annotations

import json
import logging
from pathlib import PurePosixPath
import re
import time
from typing import Any, Callable, List, Literal, Optional

from pydantic import BaseModel, Field


class RuntimeHypothesisItem(BaseModel):
    title: str = Field(description="Clear title of the hypothesized runtime defect")
    possible_cause: str = Field(description="Plausible defect mechanism explaining the runtime failure")
    reasoning: str = Field(description="Step-by-step reasoning linking the runtime evidence to the reported symptom")
    cited_sources: List[str] = Field(default_factory=list, description="Source paths or status codes from the evidence")
    unanswered_questions: List[str] = Field(default_factory=list, description="Questions needing runtime/test verification")
    confidence: Literal["low", "medium", "high"] = Field(default="medium")


class RuntimeHypothesesPayload(BaseModel):
    hypotheses: List[RuntimeHypothesisItem] = Field(default_factory=list)

from app.agents.investigator_contract import (
    ToolCallTracker,
    create_hypothesis,
    normalize_items,
    resolve_workspace,
    safe_list,
    safe_read,
)
from app.logging_config import sanitize_log_text
from app.tools.investigation_tools import list_files, read_file

logger = logging.getLogger("app.agents.runtime_error_investigator")


INVESTIGATOR_NAME = "Runtime / Error Investigator"

CATEGORIES = {
    "confirmed": "Confirmed Finding",
    "possible": "Possible Cause",
    "assumption": "Assumption",
    "unknown": "Unknown",
}

HTTP_STATUS_MEANINGS = {
    400: "Bad Request: the server rejected the request as invalid.",
    401: "Unauthorized: authentication is missing or invalid.",
    403: "Forbidden: the request is not permitted.",
    404: "Not Found: the requested resource or route was not found.",
    408: "Request Timeout: the request did not complete in time.",
    409: "Conflict: the request conflicts with current resource state.",
    422: "Unprocessable Entity: the request could not be processed.",
    429: "Too Many Requests: request rate limits may have been exceeded.",
    500: "Internal Server Error: the server encountered an error.",
    502: "Bad Gateway: a gateway received an invalid upstream response.",
    503: "Service Unavailable: the service is unavailable.",
    504: "Gateway Timeout: an upstream service did not respond in time.",
}

MAX_STACK_FRAMES = 30
MAX_SOURCE_LINES = 5
MAX_SNIPPET_CHARS = 500


def _as_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)):
        return str(value)
    return ""


def _line_number(value: Any) -> int | None:
    try:
        number = int(value)
        return number if number > 0 else None
    except (TypeError, ValueError):
        return None


def _safe_relative_path(path: str) -> bool:
    if not path or "\x00" in path:
        return False

    normalized = path.replace("\\", "/")
    parsed = PurePosixPath(normalized)

    return not parsed.is_absolute() and ".." not in parsed.parts


def _finding(
    category: str,
    statement: str,
    evidence_ids: list[str] | None = None,
    *,
    confidence: str = "medium",
    rationale: str | None = None,
    task_id: str | None = None,
    agent_name: str | None = "runtime_error_investigator",
    file_path: str | None = None,
    line_number: int | None = None,
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
    if rationale:
        result["rationale"] = rationale

    return result


def _context_values(context: dict, *keys: str) -> list[str]:
    """Read log-like values from common shared-context field names."""
    values = []

    for key in keys:
        value = context.get(key)

        if isinstance(value, str) and value.strip():
            values.append(value.strip())
        elif isinstance(value, (list, tuple)):
            for item in value:
                if isinstance(item, str) and item.strip():
                    values.append(item.strip())
                elif isinstance(item, dict):
                    text = (
                        item.get("message")
                        or item.get("error")
                        or item.get("stack_trace")
                        or item.get("traceback")
                        or item.get("content")
                    )
                    if isinstance(text, str) and text.strip():
                        values.append(text.strip())

    return values


def _extract_status_codes(text: str) -> list[int]:
    """Extract HTTP status codes from common log formats."""
    codes = set()

    patterns = (
        r"\bHTTP(?:/\d(?:\.\d)?)?\s+([1-5]\d{2})\b",
        r"\bstatus(?:_code)?\s*[:=]\s*['\"]?([1-5]\d{2})\b",
        r"\bresponse\s+(?:status\s*)?[:=]?\s*([1-5]\d{2})\b",
        r"\b([45]\d{2})\s+(?:Internal Server Error|Unauthorized|"
        r"Forbidden|Not Found|Bad Request|Conflict|Service Unavailable)\b",
    )

    for pattern in patterns:
        for match in re.findall(pattern, text, flags=re.IGNORECASE):
            try:
                code = int(match)
            except (TypeError, ValueError):
                continue

            if 100 <= code <= 599:
                codes.add(code)

    return sorted(codes)


def _parse_stack_frames(text: str) -> list[dict]:
    """Parse common Node.js, Python, and Java stack-frame formats."""
    frames = []
    seen = set()

    patterns = [
        # Node.js:
        # at updateUser (C:\app\userController.js:42:17)
        # at C:\app\userController.js:42:17
        re.compile(
            r"""^\s*at\s+(?:(?P<function>.*?)\s+\()?"""
            r"""(?P<path>(?:[A-Za-z]:[\\/]|/|\.{1,2}[\\/])"""
            r"""[^()\r\n]+?):(?P<line>\d+):(?P<column>\d+)\)?\s*$"""
        ),
        # Python:
        # File "app/controllers/userController.py", line 42, in update_user
        re.compile(
            r"""File\s+["'](?P<path>[^"']+)["']\s*,\s*"""
            r"""line\s+(?P<line>\d+)"""
            r"""(?:,\s*in\s+(?P<function>[^\r\n]+))?"""
        ),
        # Java:
        # at com.example.UserController.update(UserController.java:42)
        re.compile(
            r"""^\s*at\s+(?P<function>[\w.$<>]+)\("""
            r"""(?P<path>[^():]+\.java):(?P<line>\d+)\)\s*$"""
        ),
    ]

    for line in text.splitlines():
        for pattern in patterns:
            match = pattern.search(line)

            if not match:
                continue

            raw_path = match.group("path").strip()
            function = (match.groupdict().get("function") or "").strip()
            line_number = _line_number(match.group("line"))
            column = _line_number(match.groupdict().get("column"))

            if not raw_path or not line_number:
                continue

            normalized = raw_path.replace("\\", "/")
            key = (normalized, line_number, function)

            if key in seen:
                break

            seen.add(key)

            frames.append({
                "path": normalized,
                "line": line_number,
                "column": column,
                "function": function or None,
                "raw_line": line.strip(),
            })

            break

        if len(frames) >= MAX_STACK_FRAMES:
            break

    return frames


def _error_messages(text: str) -> list[str]:
    """Extract common exception/error message lines."""
    result = []
    patterns = (
        r"\b(?:TypeError|ReferenceError|SyntaxError|RangeError|"
        r"ValueError|KeyError|AttributeError|ImportError|"
        r"ModuleNotFoundError|NameError|RuntimeError|"
        r"NullPointerException|SQLException|DatabaseError|"
        r"OperationalError|ConnectionError|TimeoutError)\b",
        r"\b(?:UnhandledPromiseRejection|Unhandled exception|"
        r"Internal Server Error|Request failed|"
        r"Traceback \(most recent call last\))\b",
    )

    for line in text.splitlines():
        stripped = line.strip()

        if stripped and any(
            re.search(pattern, stripped, flags=re.IGNORECASE)
            for pattern in patterns
        ):
            if stripped not in result:
                result.append(stripped[:MAX_SNIPPET_CHARS])

    return result


def _resolve_frame_path(
    frame_path: str,
    known_paths: set[str],
) -> str | None:
    """Resolve an absolute or relative stack path to a repository file."""
    normalized = frame_path.replace("\\", "/").lstrip("./")

    if normalized in known_paths:
        return normalized

    # Absolute machine paths often contain a repository-relative suffix.
    matches = [
        path for path in known_paths
        if normalized.endswith("/" + path) or normalized == path
    ]

    if len(matches) == 1:
        return matches[0]

    # If several files share a basename, prefer the longest matching suffix.
    suffix_matches = [
        path for path in known_paths
        if normalized.endswith("/" + path)
    ]

    if suffix_matches:
        suffix_matches.sort(key=len, reverse=True)

        if len(suffix_matches) == 1 or len(suffix_matches[0]) > len(suffix_matches[1]):
            return suffix_matches[0]

    # Fall back to a unique basename match only.
    basename = PurePosixPath(normalized).name
    basename_matches = [
        path for path in known_paths
        if PurePosixPath(path).name == basename
    ]

    if len(basename_matches) == 1:
        return basename_matches[0]

    return None


class RuntimeErrorInvestigator:
    """Analyze runtime logs and map stack frames to repository source."""

    investigator_name = INVESTIGATOR_NAME
    agent_name = "runtime_error_investigator"

    def __init__(
        self,
        *,
        list_files_tool: Callable[..., Any] = list_files,
        read_file_tool: Callable[..., Any] = read_file,
        max_stack_frames: int = MAX_STACK_FRAMES,
        llm_factory: Optional[Callable] = None,
    ) -> None:
        self.list_files_tool = list_files_tool
        self.read_file_tool = read_file_tool
        self.llm_factory = llm_factory
        self.max_stack_frames = max(
            1,
            min(int(max_stack_frames), 100),
        )

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
            or "TASK-RUNTIME-001"
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
            "[Runtime / Error Investigator] specialist_task_start: task_id=%s, agent_name=%s, workspace=%s",
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
            "stack_frames": [],
            "http_status_codes": [],
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
            logger.warning("[Runtime / Error Investigator] specialist_task_failed: investigation_task is empty (task_id=%s)", task_id)
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
            logger.warning("[Runtime / Error Investigator] specialist_task_failed: workspace_path is missing (task_id=%s)", task_id)
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

        # 1. Collect runtime artifacts from shared context.
        logs = _context_values(
            actual_context,
            "logs",
            "error_logs",
            "server_logs",
            "runtime_logs",
            "error_message",
            "exception",
            "errors",
        )
        traces = _context_values(
            actual_context,
            "stack_traces",
            "stack_trace",
            "traceback",
            "tracebacks",
        )
        responses = _context_values(
            actual_context,
            "http_response",
            "http_response_payload",
            "response_payload",
            "api_behavior",
            "response_body",
        )

        if isinstance(actual_context.get("http_status"), int):
            responses.append(f"HTTP status: {actual_context['http_status']}")

        if isinstance(actual_context.get("status_code"), int):
            responses.append(f"status_code={actual_context['status_code']}")

        all_runtime_text = "\n".join([*logs, *traces, *responses])
        if not all_runtime_text.strip():
            # The task itself may contain a status code or error message.
            all_runtime_text = task_str

        # 2. Parse frames, error messages, and status codes.
        frames = _parse_stack_frames(all_runtime_text)
        frames = frames[:self.max_stack_frames]
        result["stack_frames"] = frames

        status_codes = _extract_status_codes(
            all_runtime_text + "\n" + task_str
        )
        result["http_status_codes"] = status_codes

        error_lines = _error_messages(all_runtime_text)

        evidence = []

        def add_evidence(
            *,
            source: str,
            line: int | None,
            snippet: str,
            kind: str,
        ) -> None:
            snippet = snippet.strip()

            if not snippet:
                return

            evidence.append({
                "source": source,
                "line": line,
                "snippet": snippet[:MAX_SNIPPET_CHARS],
                "kind": kind,
            })

        # 3. Runtime log evidence is directly observed, not proof of cause.
        for log in logs:
            for line in log.splitlines():
                stripped = line.strip()

                if stripped and (
                    _error_messages(stripped)
                    or re.search(r"\b(?:error|exception|failed|failure)\b",
                                 stripped, flags=re.IGNORECASE)
                ):
                    add_evidence(
                        source="<runtime-log>",
                        line=None,
                        snippet=stripped,
                        kind="error_log",
                    )

        # 4. Stack-trace evidence includes the exact observed frame.
        for frame in frames:
            add_evidence(
                source=frame["path"],
                line=frame["line"],
                snippet=frame["raw_line"],
                kind="stack_trace",
            )

        # 5. HTTP error evidence and interpretation.
        for code in status_codes:
            description = HTTP_STATUS_MEANINGS.get(code)

            if description:
                add_evidence(
                    source="<http-response>",
                    line=None,
                    snippet=f"HTTP {code}: {description}",
                    kind="http_error",
                )
            else:
                add_evidence(
                    source="<http-response>",
                    line=None,
                    snippet=f"HTTP {code}: status code not specifically mapped.",
                    kind="http_error",
                )

        # Phase 10 Guardrail: Maximum 10 tool calls per investigator execution
        tracker = ToolCallTracker(max_calls=10, investigator_name=self.investigator_name, task_id=task_id)

        # 6. Discover repository paths so absolute stack paths can be resolved.
        known_paths = set()
        listing, list_errors = safe_list(actual_workspace, self.list_files_tool, tracker=tracker)
        if list_errors:
            result["tool_errors"].extend(list_errors)
        else:
            for item in listing:
                if item.get("type") == "directory":
                    continue

                path = item.get("path") or item.get("file")

                if not isinstance(path, str):
                    continue

                normalized = path.replace("\\", "/").strip()

                if (
                    normalized
                    and not normalized.startswith("/")
                    and ".." not in PurePosixPath(normalized).parts
                ):
                    known_paths.add(normalized)

        # Include explicitly provided relevant files.
        for item in actual_context.get("relevant_files", []) or []:
            if isinstance(item, dict):
                item = item.get("path")

            if isinstance(item, str) and _safe_relative_path(item):
                known_paths.add(item.replace("\\", "/"))

        # Include files specified in task scope.
        for item in actual_task_dict.get("scope", []) or []:
            if isinstance(item, str) and _safe_relative_path(item):
                known_paths.add(item.replace("\\", "/"))

        # 7. Resolve each frame and inspect source code near the reported line.
        mapped_frames = 0

        for frame in frames:
            if not tracker.can_call():
                break

            resolved = _resolve_frame_path(frame["path"], known_paths)

            if not resolved:
                result["missing_evidence"].append(
                    f"Could not map stack frame to a repository file: "
                    f"{frame['path']}:{frame['line']}"
                )
                continue

            mapped_frames += 1
            content, error = self._read(actual_workspace, resolved, tracker=tracker)
            if error:
                result["tool_errors"].append(error)
                result["missing_evidence"].append(
                    f"Could not inspect source for {resolved}:{frame['line']}."
                )
                continue

            source_lines = content.splitlines()
            reported_line = frame["line"]

            if reported_line > len(source_lines):
                result["missing_evidence"].append(
                    f"Stack trace points to line {reported_line}, but "
                    f"{resolved} has only {len(source_lines)} lines."
                )
                continue

            start = max(1, reported_line - 1)
            end = min(
                len(source_lines),
                reported_line + MAX_SOURCE_LINES - 1,
            )

            excerpt = "\n".join(
                f"{number}: {source_lines[number - 1]}"
                for number in range(start, end + 1)
            )

            add_evidence(
                source=resolved,
                line=reported_line,
                snippet=(
                    f"Source excerpt around stack frame "
                    f"{resolved}:{reported_line}\n{excerpt}"
                ),
                kind="source_mapping",
            )

            # Add a direct source-line observation for precise references.
            source_line = source_lines[reported_line - 1].strip()

            add_evidence(
                source=resolved,
                line=reported_line,
                snippet=source_line,
                kind="source_line",
            )

        # 8. Detect missing diagnostic information.
        if not logs and not traces:
            result["missing_evidence"].append(
                "No runtime logs or stack traces were provided."
            )

        if not frames:
            result["missing_evidence"].append(
                "No recognizable stack frames were found. Include the complete "
                "stack trace, including file paths and line numbers."
            )

        if status_codes and not logs and not traces:
            result["missing_evidence"].append(
                "HTTP status is available, but the server-side exception "
                "and stack trace are missing."
            )

        if frames and mapped_frames == 0:
            result["missing_evidence"].append(
                "None of the parsed stack frames could be mapped to a repository file."
            )

        if logs and not error_lines:
            result["missing_evidence"].append(
                "No recognizable exception type or error line was extracted "
                "from the supplied logs."
            )

        # 9. Deduplicate evidence and assign stable evidence IDs.
        deduplicated = []
        seen = set()

        for item in evidence:
            key = (
                item["source"],
                item["line"],
                item["kind"],
                item["snippet"],
            )

            if key in seen:
                continue

            seen.add(key)
            deduplicated.append(item)

        formatted_evidence: list[dict[str, Any]] = []
        for index, item in enumerate(deduplicated, start=1):
            ev_id = f"EV-RUNTIME-{index:03d}"
            ev_record = {
                "evidence_id": ev_id,
                "task_id": task_id,
                "agent_name": self.agent_name,
                "source": item["source"],
                "line": item["line"],
                "matched_text": item["snippet"],
                "snippet": item["snippet"],
                "kind": item["kind"],
                "verification_status": "unverified",
                "observed_fact": f"Runtime observation in {item['source']} line {item.get('line') or '?'}: {item['snippet'][:100]}",
                "explanation": f"Runtime diagnostic record in {item['source']} (line {item.get('line') or '?'}) establishing execution failure context.",
            }
            formatted_evidence.append(ev_record)

        result["evidence_snippets"] = formatted_evidence
        result["evidence"] = formatted_evidence

        # 10. Categorize findings and link each to evidence with full provenance
        for log in logs:
            relevant = [
                item for item in formatted_evidence
                if item["kind"] == "error_log"
                and item["snippet"] in log
            ]

            if relevant:
                result["findings"].append(_finding(
                    CATEGORIES["confirmed"],
                    f"Runtime log contains: {relevant[0]['snippet']}",
                    [item["evidence_id"] for item in relevant],
                    confidence="high",
                    rationale="This confirms the supplied log text, not its underlying cause.",
                    task_id=task_id,
                    agent_name=self.agent_name,
                    file_path=relevant[0]["source"],
                ))

        for frame in frames:
            related = [
                item for item in formatted_evidence
                if item["kind"] == "stack_trace"
                and item["source"] == frame["path"]
                and item["line"] == frame["line"]
            ]

            if related:
                result["findings"].append(_finding(
                    CATEGORIES["confirmed"],
                    f"Stack trace identifies {frame['path']} at line {frame['line']}.",
                    [related[0]["evidence_id"]],
                    confidence="high",
                    rationale="The file and line are directly stated in the supplied trace.",
                    task_id=task_id,
                    agent_name=self.agent_name,
                    file_path=frame["path"],
                    line_number=frame["line"],
                ))

        for code in status_codes:
            related = [
                item for item in formatted_evidence
                if item["kind"] == "http_error"
                and item["snippet"].startswith(f"HTTP {code}:")
            ]

            if code in HTTP_STATUS_MEANINGS:
                category = CATEGORIES["confirmed"]
                statement = (
                    f"HTTP status {code} was reported: "
                    f"{HTTP_STATUS_MEANINGS[code]}"
                )
            else:
                category = CATEGORIES["unknown"]
                statement = (
                    f"HTTP status {code} was observed, but its meaning "
                    "is not explicitly mapped by this investigator."
                )

            result["findings"].append(_finding(
                category,
                statement,
                [item["evidence_id"] for item in related],
                confidence="high" if related else "low",
                task_id=task_id,
                agent_name=self.agent_name,
            ))

        if frames and mapped_frames:
            source_evidence = [
                item for item in formatted_evidence
                if item["kind"] in {"source_mapping", "source_line"}
            ]

            if source_evidence:
                result["findings"].append(_finding(
                    CATEGORIES["confirmed"],
                    "One or more stack-trace locations were mapped to source files and inspected.",
                    [item["evidence_id"] for item in source_evidence],
                    confidence="high",
                    rationale="This confirms source mapping and inspection; it does not by itself confirm the root cause.",
                    task_id=task_id,
                    agent_name=self.agent_name,
                    file_path=source_evidence[0]["source"],
                    line_number=source_evidence[0].get("line"),
                ))

                result["findings"].append(_finding(
                    CATEGORIES["possible"],
                    "The mapped source lines may explain the runtime failure, but the causal relationship requires validation against the input data and execution path.",
                    [item["evidence_id"] for item in source_evidence],
                    confidence="medium",
                    task_id=task_id,
                    agent_name=self.agent_name,
                    file_path=source_evidence[0]["source"],
                    line_number=source_evidence[0].get("line"),
                ))

        if not frames and not status_codes and not error_lines:
            result["findings"].append(_finding(
                CATEGORIES["unknown"],
                "The supplied evidence does not contain a recognizable stack frame, exception, or HTTP status code.",
                confidence="none",
                task_id=task_id,
                agent_name=self.agent_name,
            ))

        if result["missing_evidence"] and result["findings"]:
            result["findings"].append(_finding(
                CATEGORIES["assumption"],
                "Diagnosis is limited to the supplied logs and repository. Environment variables, database state, request payloads, and upstream service behavior have not been independently verified.",
                confidence="low",
                task_id=task_id,
                agent_name=self.agent_name,
            ))

        if not result["findings"]:
            result["findings"].append(_finding(
                CATEGORIES["unknown"],
                "No actionable runtime finding could be established.",
                confidence="none",
                task_id=task_id,
                agent_name=self.agent_name,
            ))

        # 11. Hypotheses: Generate ONLY when observed runtime evidence reveals a plausible defect mechanism
        runtime_defect_evidence = [
            it for it in formatted_evidence
            if it["kind"] in {"stack_trace", "source_mapping", "error_log"}
            or (it["kind"] == "http_error" and any(code in it["snippet"] for code in ("500", "401", "403", "404", "422", "502", "503", "504")))
        ]

        llm_hypotheses = self._generate_hypotheses_with_llm(
            task_id=task_id,
            task_text=task_str,
            context=actual_context,
            formatted_evidence=runtime_defect_evidence,
        )
        if llm_hypotheses:
            result["hypotheses"].extend(llm_hypotheses)
            for h in llm_hypotheses:
                logger.info(
                    "[Runtime / Error Investigator] hypothesis_validated (LLM): task_id=%s, hypothesis_id=%s, citing=%s",
                    task_id,
                    h.get("hypothesis_id"),
                    h.get("supporting_evidence_ids"),
                )
        elif runtime_defect_evidence:
            distinct_clusters: list[list[dict[str, Any]]] = []
            for item in runtime_defect_evidence:
                item_src = item.get("source", "")
                item_line = item.get("line") or 0
                item_kind = item.get("kind", "")

                matched_cluster = None
                for cluster in distinct_clusters:
                    first = cluster[0]
                    first_src = first.get("source", "")
                    first_line = first.get("line") or 0
                    first_kind = first.get("kind", "")
                    if item_kind == first_kind and item_src == first_src and abs(item_line - first_line) <= 5:
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
                kind = lead.get("kind", "")

                # Contradicting evidence check
                contra_items = [
                    it for it in formatted_evidence
                    if it["source"] == source_file
                    and any(term in it["snippet"].lower() for term in ("success", "200 ok", "resolved", "recovered"))
                ]

                if kind == "http_error":
                    title = f"HTTP response condition: {lead['snippet'][:40]}"
                    cause = f"The server returned an HTTP failure response: {lead['snippet']}."
                    evidence_str = lead["snippet"]
                    reasoning = "The client request encountered rejection or a server-side failure resulting in this HTTP status code."
                    unanswered = [
                        "Which specific endpoint, route handler, or middleware emitted this HTTP status?",
                        "Were expected request headers or parameters provided by the client?",
                    ]
                elif kind in {"stack_trace", "source_mapping", "source_line"}:
                    title = f"Runtime exception at {source_file}:{line_no}"
                    cause = f"Execution encountered a runtime failure or active exception at {source_file} (line {line_no})."
                    evidence_str = f"{source_file}:{line_no} — `{lead['snippet'][:150]}`"
                    reasoning = "The captured stack trace directly identifies this source location as the site of execution failure."
                    unanswered = [
                        f"What runtime payload or state triggered the exception at {source_file}:{line_no}?",
                        "Is this frame the root origin of the fault or an intermediate caller in the stack?",
                    ]
                else:
                    title = f"Runtime error logged from {source_file}"
                    cause = f"An application error condition was recorded: `{lead['snippet'][:80]}`."
                    evidence_str = f"{source_file} — `{lead['snippet'][:150]}`"
                    reasoning = "The runtime logs demonstrate an active failure state during execution."
                    unanswered = [
                        "Under what specific execution conditions was this error log emitted?",
                        "Did execution terminate or recover following this error?",
                    ]

                hyp_id = f"HYP-{task_id[-8:] if len(task_id) >= 8 else task_id}-{idx:03d}"
                hyp = create_hypothesis(
                    hypothesis_id=hyp_id,
                    hypothesis_title=title,
                    produced_by=self.investigator_name,
                    possible_cause=cause,
                    evidence=evidence_str,
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
                    "[Runtime / Error Investigator] hypothesis_validated: task_id=%s, hypothesis_id=%s, citing=%s",
                    task_id,
                    hyp_id,
                    cluster_ev_ids,
                )
        else:
            result["hypotheses"] = []
            result["unanswered_questions"].append(
                f"No runtime error, stack trace, or HTTP failure condition was observed for task {task_id}."
            )
            logger.info(
                "[Runtime / Error Investigator] No plausible defect mechanism observed in runtime logs; hypotheses list is empty (task_id=%s).",
                task_id,
            )

        if result["tool_errors"]:
            result["status"] = (
                "partial" if formatted_evidence else "failed"
            )

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
            assessments = {"status": "unresolved", "summary": "No runtime defect mechanism confirmed"}
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
            "[Runtime / Error Investigator] specialist_task_complete: task_id=%s, agent_name=%s, status=%s",
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

    def _read(
        self,
        workspace: str,
        path: str,
        tracker: Any = None,
    ) -> tuple[str, dict | None]:
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
        """Generate runtime hypotheses using structured LLM synthesis if available."""
        if not self.llm_factory:
            return []
        try:
            llm = self.llm_factory()
            if not llm:
                return []
            structured = llm.with_structured_output(RuntimeHypothesesPayload)

            ev_summary = [
                {
                    "evidence_id": e.get("evidence_id"),
                    "source": e.get("source"),
                    "line": e.get("line"),
                    "snippet": e.get("snippet", "")[:300],
                    "kind": e.get("kind"),
                }
                for e in formatted_evidence[:15]
            ]

            prob_text = context.get("problem") or context.get("problem_summary") or task_text
            prompt = (
                f"You are a Senior Runtime & Error Investigator analyzing execution failures for a software issue.\n\n"
                f"Investigation Task: {task_text}\n"
                f"Problem Statement: {prob_text}\n\n"
                f"Observed Runtime Evidence ({len(ev_summary)} items):\n"
                f"{json.dumps(ev_summary, indent=2)}\n\n"
                f"Instructions:\n"
                f"1. Examine the runtime logs, stack traces, and HTTP status codes.\n"
                f"2. Explain what runtime failure or rejection condition is demonstrated.\n"
                f"3. Note what runtime verification or missing execution evidence is required.\n"
                f"4. If no runtime error or failure is present in the evidence, return an empty hypotheses list."
            )
            res = structured.invoke(prompt)
            if not res or not res.hypotheses:
                return []

            hypotheses = []
            for idx, item in enumerate(res.hypotheses[:2], start=1):
                hyp_id = f"HYP-{task_id[-8:] if len(task_id) >= 8 else task_id}-{idx:03d}"
                supporting = []
                supporting_ids = []
                for ev in formatted_evidence:
                    src = ev.get("source", "")
                    snip = ev.get("snippet", "")
                    if any(c in src or c in snip for c in item.cited_sources) or not item.cited_sources:
                        supporting.append(ev)
                        supporting_ids.append(ev["evidence_id"])
                if not supporting:
                    supporting = formatted_evidence[:2]
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
                        "Does runtime execution capture server-side logs and request payloads for this failure?"
                    ],
                    task_id=task_id,
                    agent_name=self.agent_name,
                )
                hypotheses.append(hyp)
            return hypotheses
        except Exception as exc:
            logger.debug("LLM runtime hypothesis generation fallback: %s", exc)
            return []


def run_runtime_error_investigator(
    investigation_task: str,
    relevant_context: dict | None,
    current_state: dict | None,
    *,
    list_files_tool: Any = list_files,
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

    investigator = RuntimeErrorInvestigator(
        list_files_tool=list_files_tool,
        read_file_tool=read_file_tool,
        llm_factory=llm_factory,
    )

    return investigator.investigate(
        workspace,
        investigation_task,
        combined_context,
    )
