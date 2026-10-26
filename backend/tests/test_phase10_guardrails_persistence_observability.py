import os
import json
import tempfile
import pytest
from unittest.mock import MagicMock, patch

from app.config import settings
from app.core.config import settings as core_settings
from app.agents.investigator_contract import ToolCallTracker, safe_search, safe_read, safe_list
from app.agents.code_investigator import CodeInvestigator
from app.agents.runtime_error_investigator import RuntimeErrorInvestigator
from app.agents.dependency_impact_investigator import DependencyImpactInvestigator
from app.agents.investigation_orchestrator import InvestigationOrchestrator
from app.graph.state import InvestigationState
from app.graph.graph import initial_node, reviewer_node, route_after_reviewer, inconclusive_result_node
from app.tracing import is_tracing_enabled, get_tracing_callbacks, get_tracing_config
from app.api import _persist_session, _load_sessions, sessions


# ==============================================================================
# 1. Investigator Tool-Call Limit Tests (Max 10 calls)
# ==============================================================================

def test_tool_call_tracker_normal_and_limit():
    tracker = ToolCallTracker(max_calls=10)
    assert tracker.can_call() is True
    assert tracker.tool_call_count == 0
    assert tracker.is_limit_reached is False

    # Perform 10 calls
    for i in range(10):
        assert tracker.can_call() is True
        tracker.record_execution(
            tool_name="test_tool",
            arguments={"arg": i},
            output=f"output_{i}",
            status="success",
            duration_ms=5.0,
        )

    assert tracker.tool_call_count == 10
    assert tracker.is_limit_reached is True
    # 11th call should be blocked
    assert tracker.can_call() is False

    # Blocked call record
    record = tracker.record_limit_reached("test_tool", {"arg": 10})
    assert record["status"] == "blocked_limit_reached"
    assert "Tool call limit (10) reached" in record["error"]
    assert len(tracker.executions) == 11


def test_safe_tools_respect_tool_tracker():
    with tempfile.TemporaryDirectory() as tmp_dir:
        test_file = os.path.join(tmp_dir, "sample.py")
        with open(test_file, "w", encoding="utf-8") as f:
            f.write("print('hello world')\n")

        tracker = ToolCallTracker(max_calls=2)
        mock_read = MagicMock(return_value="print('hello world')\n")
        mock_search = MagicMock(return_value=[{"file": "sample.py", "line": 1, "match": "hello"}])
        mock_list = MagicMock(return_value=[{"name": "sample.py", "type": "file"}])

        # Call 1: safe_read
        content, err = safe_read(tmp_dir, "sample.py", mock_read, tracker=tracker)
        assert content == "print('hello world')\n"
        assert err is None
        assert tracker.tool_call_count == 1

        # Call 2: safe_search
        items, errs = safe_search(tmp_dir, "hello", mock_search, tracker=tracker)
        assert len(items) == 1
        assert len(errs) == 0
        assert tracker.tool_call_count == 2
        assert tracker.is_limit_reached is True

        # Call 3: safe_list should be blocked by tracker
        items3, errs3 = safe_list(tmp_dir, mock_list, subpath=".", tracker=tracker)
        assert len(items3) == 0
        assert len(errs3) == 1
        assert "limit" in errs3[0]["error"].lower()
        assert tracker.is_limit_reached is True


def test_investigators_track_tool_executions():
    with tempfile.TemporaryDirectory() as tmp_dir:
        code_file = os.path.join(tmp_dir, "app.py")
        with open(code_file, "w", encoding="utf-8") as f:
            f.write("import os\ndef run():\n    return os.environ.get('SECRET')\n")

        task = {
            "id": "TASK-1",
            "assigned_agent": "code_investigator",
            "description": "inspect code and hypotheses",
            "hypotheses": ["check run"],
        }

        # 1. CodeInvestigator
        code_inv = CodeInvestigator()
        res_code = code_inv.investigate(
            workspace_path=tmp_dir,
            investigation_task=task,
            shared_context={},
        )
        assert "tool_executions" in res_code
        assert "tool_call_count" in res_code
        assert res_code["tool_call_count"] <= 10

        # 2. RuntimeErrorInvestigator
        runtime_inv = RuntimeErrorInvestigator()
        task_runtime = dict(task, id="TASK-2", assigned_agent="runtime_error_investigator")
        res_runtime = runtime_inv.investigate(
            workspace_path=tmp_dir,
            investigation_task=task_runtime,
            shared_context={},
        )
        assert "tool_executions" in res_runtime
        assert "tool_call_count" in res_runtime
        assert res_runtime["tool_call_count"] <= 10

        # 3. DependencyImpactInvestigator
        dep_inv = DependencyImpactInvestigator()
        task_dep = dict(task, id="TASK-3", assigned_agent="dependency_impact_investigator")
        res_dep = dep_inv.investigate(
            workspace_path=tmp_dir,
            investigation_task=task_dep,
            shared_context={},
        )
        assert "tool_executions" in res_dep
        assert "tool_call_count" in res_dep
        assert res_dep["tool_call_count"] <= 10


# ==============================================================================
# 2. Re-planning Iteration Limits (Max 5, bounded [1, 5])
# ==============================================================================

def test_replanning_iteration_defaults_and_bounds():
    # Initial node defaults max_iterations to 5
    state = initial_node({"problem": "sample bug"})
    assert state["max_iterations"] == 5
    assert state["iteration"] == 0

    # Custom valid limit within 1..5 is preserved
    state_custom = initial_node({"problem": "sample bug", "max_iterations": 3})
    assert state_custom["max_iterations"] == 3

    # Out of bounds (<1) clamped to 1
    state_low = initial_node({"problem": "sample bug", "max_iterations": 0})
    assert state_low["max_iterations"] == 1

    # Out of bounds (>5) clamped to 5
    state_high = initial_node({"problem": "sample bug", "max_iterations": 10})
    assert state_high["max_iterations"] == 5


def test_replanning_routing_stops_at_limit_and_marks_partial():
    # When iteration reaches max_iterations, route_after_reviewer must route to inconclusive
    state: InvestigationState = {
        "problem": "Unresolved tricky bug",
        "iteration": 5,
        "max_iterations": 5,
        "review_decision": "replan",
        "is_sufficient": False,
        "findings": [{"id": "f1", "description": "partial finding"}],
    }

    route = route_after_reviewer(state)
    assert route == "inconclusive_result"

    # Inconclusive result node sets PARTIAL_DIAGNOSIS_MAX_ITERATIONS_REACHED
    final_state = inconclusive_result_node(state)
    assert final_state["is_partial_diagnosis"] is True
    assert "PARTIAL_DIAGNOSIS_MAX_ITERATIONS_REACHED" in final_state["guardrail_status"]
    assert "PARTIAL_DIAGNOSIS_MAX_ITERATIONS_REACHED" in final_state["stop_reason"]


# ==============================================================================
# 3. Structured Tool Execution Telemetry & Sensitive Data Redaction
# ==============================================================================

def test_tool_telemetry_redacts_sensitive_arguments():
    tracker = ToolCallTracker(max_calls=5)
    tracker.record_execution(
        tool_name="safe_auth_check",
        arguments={
            "filepath": "config.py",
            "api_key": "secret_abc123",
            "db_password": "super_secret_pw",
            "auth_token": "token_xyz",
        },
        output="authenticated",
        status="success",
        duration_ms=12.4,
    )

    record = tracker.executions[0]
    assert record["tool_name"] == "safe_auth_check"
    assert record["status"] == "success"
    assert record["duration_ms"] == 12.4
    assert record["arguments"]["filepath"] == "config.py"
    assert record["arguments"]["api_key"] == "[REDACTED]"
    assert record["arguments"]["db_password"] == "[REDACTED]"
    assert record["arguments"]["auth_token"] == "[REDACTED]"


def test_tool_executions_persistence_and_reload():
    test_session_id = "test_telemetry_session_phase10"
    session_data = {
        "problem": "Test telemetry persistence",
        "tool_executions": [
            {
                "tool_name": "safe_search",
                "arguments": {"query": "auth"},
                "status": "success",
                "duration_ms": 15.2,
                "output_summary": "Found 3 matches",
            }
        ],
        "guardrail_status": ["PARTIAL_DIAGNOSIS_MAX_ITERATIONS_REACHED"],
        "is_partial_diagnosis": True,
    }

    # Persist session
    _persist_session(test_session_id, session_data)

    # Reload sessions from disk
    loaded = _load_sessions()
    assert test_session_id in loaded
    reloaded_data = loaded[test_session_id]
    assert len(reloaded_data["tool_executions"]) == 1
    assert reloaded_data["tool_executions"][0]["tool_name"] == "safe_search"
    assert reloaded_data["guardrail_status"] == ["PARTIAL_DIAGNOSIS_MAX_ITERATIONS_REACHED"]
    assert reloaded_data["is_partial_diagnosis"] is True


# ==============================================================================
# 4. Optional LangSmith Tracing Tests
# ==============================================================================

def test_langsmith_tracing_disabled_by_default():
    with patch.object(settings, "LANGCHAIN_TRACING_V2", False):
        assert is_tracing_enabled() is False
        assert get_tracing_callbacks() == []
        assert get_tracing_config() == {}


def test_langsmith_tracing_enabled_missing_key():
    with patch.object(settings, "LANGCHAIN_TRACING_V2", True), \
         patch.object(settings, "LANGCHAIN_API_KEY", ""):
        assert is_tracing_enabled() is False
        # Should gracefully return empty list without crashing
        assert get_tracing_callbacks() == []
        assert get_tracing_config() == {}


def test_langsmith_tracing_enabled_with_key():
    with patch.object(settings, "LANGCHAIN_TRACING_V2", True), \
         patch.object(settings, "LANGCHAIN_API_KEY", "ls_test_key_12345"), \
         patch.object(settings, "LANGCHAIN_PROJECT", "test-project-10"):
        assert is_tracing_enabled() is True
        callbacks = get_tracing_callbacks()
        assert len(callbacks) == 1
        config = get_tracing_config()
        assert "callbacks" in config
        assert len(config["callbacks"]) == 1
        assert os.environ.get("LANGCHAIN_PROJECT") == "test-project-10"


def test_langsmith_tracing_safe_failure_handling():
    with patch.object(settings, "LANGCHAIN_TRACING_V2", True), \
         patch.object(settings, "LANGCHAIN_API_KEY", "fake_key"):
        with patch("langchain_core.tracers.LangChainTracer", side_effect=Exception("Network error")):
            # Must not crash, must return empty list
            callbacks = get_tracing_callbacks()
            assert callbacks == []
            assert get_tracing_config() == {}


def test_core_config_reexport():
    assert core_settings.APP_ENV == settings.APP_ENV
    assert hasattr(core_settings, "LANGCHAIN_TRACING_V2")
    assert hasattr(core_settings, "LANGCHAIN_API_KEY")
    assert hasattr(core_settings, "LANGCHAIN_PROJECT")
