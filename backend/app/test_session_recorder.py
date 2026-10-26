"""Test session recorder for recording test execution results into session-output.json.

Preserves existing session_id, state, status, and historical data while recording
complete test run entries.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("app.test_session_recorder")


def get_default_session_output_path() -> Path:
    """Returns absolute path to backend/session-output.json."""
    return Path(__file__).resolve().parent.parent / "session-output.json"


def record_test_run(
    *,
    session_file_path: Optional[Path | str] = None,
    phase: str = "phase_2",
    phase_name: str = "Initial Plan Creation, Task Schema & Test Logging",
    command: str,
    test_files: Optional[List[str]] = None,
    total_tests: int,
    passed: int,
    failed: int = 0,
    skipped: int = 0,
    errors: int = 0,
    duration_seconds: float = 0.0,
    exit_code: int = 0,
    status: Optional[str] = None,
    failure_details: Optional[List[Dict[str, Any]]] = None,
    started_at: Optional[str] = None,
    completed_at: Optional[str] = None,
) -> Dict[str, Any]:
    """Appends a test run record to the existing session-output.json file."""
    path = Path(session_file_path) if session_file_path else get_default_session_output_path()

    if not path.exists():
        raise FileNotFoundError(f"Session output file not found at: {path}")

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    session_id = data.get("session_id", "unknown_session")
    now_iso = datetime.now(timezone.utc).isoformat()

    computed_status = status
    if not computed_status:
        if exit_code == 0 and failed == 0 and errors == 0:
            computed_status = "passed"
        elif errors > 0 or exit_code not in (0, 1):
            computed_status = "error"
        else:
            computed_status = "failed"

    entry: Dict[str, Any] = {
        "session_id": session_id,
        "phase": phase,
        "phase_name": phase_name,
        "started_at": started_at or now_iso,
        "completed_at": completed_at or now_iso,
        "timestamp": now_iso,
        "command": command,
        "test_files": test_files or [],
        "total_tests": total_tests,
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "errors": errors,
        "duration_seconds": round(duration_seconds, 3),
        "exit_code": exit_code,
        "status": computed_status,
        "failure_details": failure_details or [],
    }

    if "test_runs" not in data or not isinstance(data["test_runs"], list):
        data["test_runs"] = []

    data["test_runs"].append(entry)

    # Atomic write to prevent corruption
    temp_path = path.with_suffix(".tmp")
    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    temp_path.replace(path)

    logger.info("Recorded test run to %s (status: %s, total: %d, passed: %d)", path, computed_status, total_tests, passed)
    return entry
