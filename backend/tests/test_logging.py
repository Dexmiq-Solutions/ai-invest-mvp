"""Tests for comprehensive logging across Phases 1–6.

Validates:
- Centralized logging configuration and idempotency (no duplicate handlers)
- Sensitive data masking (API keys, bearer tokens, credentials)
- Workflow event logs (context check, tool execution, planning decisions)
- Error and failure path logging (insufficient context, path traversal, investigator failures)
- API endpoint logging
"""

import logging
import pytest
from fastapi.testclient import TestClient

from app.logging_config import SensitiveDataFilter, sanitize_log_text, setup_logging
from app.api import app
from app.graph.context import check_context
from app.tools.investigation_tools import list_files, read_file
from app.agents.code_investigator import CodeInvestigator
from app.agents.investigation_orchestrator import InvestigationOrchestrator


def test_sensitive_data_filter_masks_secrets():
    filt = SensitiveDataFilter()
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname="test.py",
        lineno=1,
        msg="Connecting with key sk-1234567890abcdef12345678\nAuthorization: Bearer secret-token-xyz\npassword=super_secret!",
        args=(),
        exc_info=None,
    )
    assert filt.filter(record) is True
    assert "sk-1234567890abcdef12345678" not in record.msg
    assert "sk-[REDACTED]" in record.msg
    assert "secret-token-xyz" not in record.msg
    assert "Authorization: [REDACTED]" in record.msg
    assert "super_secret!" not in record.msg
    assert "password=[REDACTED]" in record.msg


def test_sanitize_log_text():
    text = "Authorization: Bearer mytoken123 and api_key=sk-abcdef1234567890" + "x" * 2000
    sanitized = sanitize_log_text(text, max_len=100)
    assert len(sanitized) <= 150
    assert "mytoken123" not in sanitized
    assert "sk-abcdef1234567890" not in sanitized


def test_setup_logging_idempotence():
    # Calling setup_logging multiple times should not add duplicate handlers
    setup_logging()
    h_count_1 = len(logging.getLogger().handlers)
    setup_logging()
    h_count_2 = len(logging.getLogger().handlers)
    assert h_count_1 == h_count_2


def test_logging_insufficient_context_path(caplog):
    caplog.set_level(logging.INFO)
    result = check_context({"problem": "my application is not working", "project_path": "some/path"})
    assert result.get("context_status") == "insufficient"
    assert any("detected vague problem description" in rec.message or "Context sufficiency check starting" in rec.message for rec in caplog.records)


def test_logging_tool_calls_and_errors(caplog, tmp_path):
    caplog.set_level(logging.DEBUG)
    (tmp_path / "sample.py").write_text("def hello(): return 'world'", encoding="utf-8")

    # Successful tool call
    res = list_files(str(tmp_path))
    assert any("list_files finished in" in rec.message for rec in caplog.records)

    # Path traversal / invalid path error
    caplog.clear()
    with pytest.raises(ValueError):
        read_file(str(tmp_path), "../outside.py")
    assert any("Path traversal attempted outside workspace" in rec.message for rec in caplog.records)


def test_logging_investigator_execution_and_exception(caplog, tmp_path):
    caplog.set_level(logging.INFO)
    investigator = CodeInvestigator()
    # Missing required problem
    res = investigator.investigate(
        investigation_task="",
        workspace_path=str(tmp_path),
    )
    assert res.get("status") == "failed"
    assert any("Investigation aborted: investigation_task is empty" in rec.message for rec in caplog.records)


def test_logging_orchestrator_planner_decisions(caplog, tmp_path):
    caplog.set_level(logging.INFO)
    orchestrator = InvestigationOrchestrator(max_iterations=1)
    plan = orchestrator.create_initial_plan({
        "problem": "Runtime crash with NullPointerException in auth service",
        "repository_summary": {"primary_language": "Java"},
    })
    assert len(plan) > 0
    assert any("[Orchestrator] Initial investigation plan prepared" in rec.message for rec in caplog.records)


def test_logging_api_requests(caplog):
    caplog.set_level(logging.INFO)
    client = TestClient(app)
    response = client.post(
        "/api/investigation/start",
        data={"problem": "Application crashes with 500 error on /users", "project_path": "default"},
    )
    assert response.status_code == 200
    assert any("Investigation start request received" in rec.message for rec in caplog.records)
