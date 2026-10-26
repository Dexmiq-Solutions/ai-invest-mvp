import json
import logging
import re
import time
import uuid
from functools import wraps
from pathlib import Path
from typing import Any, Callable

from app.logging_config import sanitize_log_text

LOG_PATH = Path(__file__).resolve().parent.parent / "logs" / "node-behavior.log"
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

_logger = logging.getLogger("node_behavior")
_logger.setLevel(logging.INFO)
_logger.propagate = False

if not any(
    isinstance(handler, logging.FileHandler)
    and Path(handler.baseFilename) == LOG_PATH.resolve()
    for handler in _logger.handlers
):
    handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(message)s"))
    _logger.addHandler(handler)

_SECRET_KEYS = re.compile(
    r"password|passwd|secret|api.?key|access.?token|auth.?token|credential",
    re.IGNORECASE,
)


def _safe(value: Any, depth: int = 0) -> Any:
    """Build a bounded, redacted representation of runtime values."""
    if depth > 4:
        return "[MAX_DEPTH]"

    if isinstance(value, dict):
        result = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= 40:
                result["_truncated_keys"] = len(value) - 40
                break
            name = str(key)
            result[name] = (
                "[REDACTED]"
                if _SECRET_KEYS.search(name)
                else _safe(item, depth + 1)
            )
        return result

    if isinstance(value, (list, tuple, set)):
        items = list(value)
        result = [_safe(item, depth + 1) for item in items[:15]]
        if len(items) > 15:
            result.append(f"[{len(items) - 15} more items]")
        return result

    if isinstance(value, str):
        return sanitize_log_text(value, max_len=350)

    if value is None or isinstance(value, (bool, int, float)):
        return value

    return sanitize_log_text(repr(value), max_len=350)


def _emit(event: dict) -> None:
    """Logging failures must not break the investigation."""
    try:
        _logger.info(
            "%s",
            json.dumps(event, ensure_ascii=False, default=str),
        )
    except Exception:
        pass


def trace_node(node_name: str, function: Callable) -> Callable:
    """Record actual inputs, outputs, state changes, timing and failures."""

    @wraps(function)
    def wrapped(state, *args, **kwargs):
        started = time.perf_counter()
        timestamp = time.time()
        run_id = (
            state.get("session_id")
            or state.get("run_id")
            or state.get("thread_id")
            or "unidentified-run"
        )
        execution_id = str(uuid.uuid4())
        before_keys = set(state) if isinstance(state, dict) else set()

        _emit({
            "event": "node_started",
            "run_id": run_id,
            "execution_id": execution_id,
            "node": node_name,
            "timestamp": timestamp,
            "input": _safe(state),
        })

        try:
            result = function(state, *args, **kwargs)
            elapsed = round(time.perf_counter() - started, 4)

            if isinstance(result, dict):
                after_keys = set(result)
                changed = {
                    key: result[key]
                    for key in after_keys
                    if key not in state or result[key] != state.get(key)
                }
                state_changes = {
                    "added_keys": sorted(after_keys - before_keys),
                    "removed_keys": sorted(before_keys - after_keys),
                    "changed_keys": sorted(changed),
                    "changed_values": _safe(changed),
                }
                output = _safe(result)
            else:
                state_changes = {}
                output = _safe(result)

            _emit({
                "event": "node_completed",
                "run_id": run_id,
                "execution_id": execution_id,
                "node": node_name,
                "timestamp": time.time(),
                "duration_seconds": elapsed,
                "status": "completed",
                "output": output,
                "state_changes": state_changes,
            })
            return result

        except Exception as exc:
            _emit({
                "event": "node_failed",
                "run_id": run_id,
                "execution_id": execution_id,
                "node": node_name,
                "timestamp": time.time(),
                "duration_seconds": round(time.perf_counter() - started, 4),
                "status": "failed",
                "error_type": type(exc).__name__,
                "error": sanitize_log_text(str(exc), max_len=1000),
            })
            raise

    return wrapped
