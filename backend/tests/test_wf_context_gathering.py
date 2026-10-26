"""Tests for Phase 1: Pure Context Gathering & Completeness Gate Refactor.

Verifies:
1. Pure context gathering outputs only problem and repository information.
2. Complete absence of prohibited structures: evidence candidates, trace flows,
   producer-consumer pairs, unresolved questions, evidence gaps, hypotheses, and findings.
3. Refined completeness gate distinguishes unusable input from incomplete initial search results.
4. Structured logging reuses existing configuration, sanitizes secrets, and truncates payloads.
"""
from pathlib import Path
import logging
import pytest

from app.graph.context_gathering import (
    gather_context,
    create_empty_gathered_context,
    evaluate_gathered_context_quality,
    context_gathering_node,
)
from app.repository.analyzer import analyze_repository
from app.tools.investigation_tools import list_files, search_code, read_file
from app.logging_config import sanitize_log_text


@pytest.fixture
def sample_workspace(tmp_path: Path) -> Path:
    """Create a minimal mock repository workspace for testing."""
    ws = tmp_path / "mock_repo"
    ws.mkdir(parents=True, exist_ok=True)
    
    src = ws / "src"
    src.mkdir(parents=True, exist_ok=True)
    
    (src / "auth.py").write_text(
        "def authenticate(user_token: str):\n"
        "    if not user_token:\n"
        "        raise ValueError('Missing token')\n"
        "    return {'status': 'authenticated', 'user': 'alice'}\n",
        encoding="utf-8",
    )
    (src / "service.py").write_text(
        "from .auth import authenticate\n"
        "def handle_login(request):\n"
        "    token = request.get('token')\n"
        "    return authenticate(token)\n",
        encoding="utf-8",
    )
    (ws / "requirements.txt").write_text("fastapi>=0.100.0\npydantic>=2.0\n", encoding="utf-8")
    return ws


def test_empty_gathered_context_schema():
    """Verify create_empty_gathered_context conforms strictly to Phase 1 pure schema."""
    ctx = create_empty_gathered_context()
    
    # Required keys
    assert "problem_understanding" in ctx
    assert "investigation_concepts" in ctx
    assert "relevant_files" in ctx
    assert "file_contents" in ctx
    assert "technical_terms" in ctx
    assert "related_components" in ctx
    assert "frameworks_detected" in ctx
    assert "context_summary" in ctx
    assert "search_history" in ctx

    # Strictly prohibited keys
    assert "evidence_candidates" not in ctx
    assert "trace_flows" not in ctx
    assert "symptom_sites" not in ctx
    assert "producer_consumer_pairs" not in ctx
    assert "write_read_pairs" not in ctx
    assert "rejected_producer_candidates" not in ctx
    assert "unresolved_questions" not in ctx
    assert "evidence_gaps" not in ctx
    assert "hypotheses" not in ctx
    assert "findings" not in ctx


def test_pure_context_output(sample_workspace: Path):
    """Verify gather_context strictly outputs problem and repo information with zero prohibited fields."""
    repo_summary = analyze_repository(sample_workspace)
    validated_problem = {
        "problem_description": "User login fails when missing token in authenticate function.",
        "observed_behavior": "authenticate raises ValueError('Missing token').",
        "expected_behavior": "Should return authentication status.",
        "session_id": "test-session-pure-context",
    }

    result = gather_context(
        validated_problem,
        repo_summary,
        sample_workspace,
        list_files_tool=list_files,
        search_code_tool=search_code,
        read_file_tool=read_file,
    )

    # Core context properties
    assert isinstance(result["investigation_concepts"], list)
    assert len(result["investigation_concepts"]) > 0
    assert isinstance(result["relevant_files"], list)
    assert len(result["relevant_files"]) > 0
    assert isinstance(result["file_contents"], dict)
    assert len(result["file_contents"]) > 0
    assert isinstance(result["frameworks_detected"], list)
    assert isinstance(result["context_summary"], str)
    assert len(result["context_summary"]) > 0

    # Strictly prohibited fields must NOT exist in the output dictionary
    prohibited_fields = [
        "evidence_candidates",
        "trace_flows",
        "symptom_sites",
        "producer_consumer_pairs",
        "write_read_pairs",
        "rejected_producer_candidates",
        "unresolved_questions",
        "evidence_gaps",
        "hypotheses",
        "findings",
        "relationships",
        "implementation_context",
    ]
    for field in prohibited_fields:
        assert field not in result, f"Prohibited field '{field}' was found in gathered_context!"


def test_no_evidence_or_hypotheses_in_context(sample_workspace: Path):
    """Deep check: Ensure no item inside gathered_context is tagged as confirmed evidence or hypothesis."""
    repo_summary = analyze_repository(sample_workspace)
    validated_problem = {
        "problem_description": "Authentication failure with token validation.",
        "session_id": "test-session-no-evidence",
    }

    result = gather_context(
        validated_problem,
        repo_summary,
        sample_workspace,
        list_files_tool=list_files,
        search_code_tool=search_code,
        read_file_tool=read_file,
    )

    def scan_for_prohibited_tags(obj, path=""):
        if isinstance(obj, dict):
            # Check for evidence tags
            assert obj.get("status") != "confirmed", f"Found 'status: confirmed' at {path}"
            assert "evidence_id" not in obj, f"Found evidence_id at {path}"
            assert "hypothesis_id" not in obj, f"Found hypothesis_id at {path}"
            for k, v in obj.items():
                scan_for_prohibited_tags(v, f"{path}.{k}")
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                scan_for_prohibited_tags(item, f"{path}[{i}]")

    scan_for_prohibited_tags(result)


def test_unusable_input_halts_gate(sample_workspace: Path):
    """Verify completeness gate halts on completely unusable input (empty problem or missing workspace)."""
    # 1. Empty problem description
    empty_problem_context = {
        "problem_understanding": {"problem_summary": "", "observed_behavior": "", "expected_behavior": ""},
        "workspace": sample_workspace,
    }
    gate_result_1 = evaluate_gathered_context_quality(empty_problem_context, workspace=sample_workspace)
    assert gate_result_1["is_sufficient"] is False
    assert gate_result_1["context_status"] == "incomplete"
    assert "Unusable input" in gate_result_1["reason"]

    # 2. Non-existent workspace directory
    missing_ws = sample_workspace / "non_existent_dir_12345"
    valid_problem_context = {
        "problem_understanding": {"problem_summary": "Authentication failure"},
        "workspace": missing_ws,
    }
    gate_result_2 = evaluate_gathered_context_quality(valid_problem_context, workspace=missing_ws)
    assert gate_result_2["is_sufficient"] is False
    assert gate_result_2["context_status"] == "incomplete"
    assert "Unusable input" in gate_result_2["reason"]


def test_incomplete_initial_search_allows_planning(sample_workspace: Path):
    """Verify that when problem and workspace are valid, but initial keyword searches yield zero hits, planning is allowed."""
    sparse_context = {
        "problem_understanding": {"problem_summary": "Issue with unusual_obscure_service_symbol"},
        "relevant_files": [],  # Zero hits from initial keyword searches
        "search_history": [{"query": "unusual_obscure_service_symbol", "hits": 0}],
        "workspace": sample_workspace,
    }

    gate_result = evaluate_gathered_context_quality(sparse_context, workspace=sample_workspace)
    assert gate_result["is_sufficient"] is True
    assert gate_result["context_status"] == "sufficient"
    assert "initial keyword hits incomplete" in gate_result["reason"]
    assert "broader exploration" in gate_result["reason"]


def test_context_gathering_node_state_integration(sample_workspace: Path):
    """Verify context_gathering_node integrates cleanly with state and outputs context_completeness."""
    repo_summary = analyze_repository(sample_workspace)
    state = {
        "validated_problem_context": {
            "problem_description": "User token authentication defect",
            "session_id": "test-node-session",
        },
        "repository_summary": repo_summary,
        "workspace": sample_workspace,
    }

    node_output = context_gathering_node(
        state,
        list_files_tool=list_files,
        search_code_tool=search_code,
        read_file_tool=read_file,
    )

    assert node_output["current_step"] == "context_gathered"
    assert node_output["context_status"] == "sufficient"
    assert "context_completeness" in node_output
    assert node_output["context_completeness"]["is_sufficient"] is True
    
    gathered = node_output["gathered_context"]
    assert "evidence_candidates" not in gathered
    assert "trace_flows" not in gathered
    assert "unresolved_questions" not in gathered


def test_sanitized_logging(sample_workspace: Path, caplog: pytest.LogCaptureFixture):
    """Verify logging reuses app.logging_config, sanitizes sensitive tokens, and truncates long problem text."""
    caplog.set_level(logging.INFO)

    secret_key = "sk-1234567890abcdef12345678"
    long_desc = "Authentication fails with OpenAI API key " + secret_key + " " + ("longtext " * 50)
    
    repo_summary = analyze_repository(sample_workspace)
    validated_problem = {
        "problem_description": long_desc,
        "session_id": "test-session-sanitized-log",
    }

    gather_context(
        validated_problem,
        repo_summary,
        sample_workspace,
        list_files_tool=list_files,
        search_code_tool=search_code,
        read_file_tool=read_file,
    )

    log_text = caplog.text

    # 1. Sensitive pattern must be redacted (never logged in cleartext)
    assert secret_key not in log_text, "Sensitive API key was logged in cleartext!"
    assert "sk-[REDACTED]" in log_text or "[REDACTED" in log_text or "sk-" not in log_text

    # 2. Key Phase 1 log events must be emitted
    assert "context_gathering_start" in log_text
    assert "scan_statistics" in log_text
    assert "completeness_gate_result" in log_text
    assert "context_gathering_complete" in log_text

    # 3. Full problem description must be truncated (never dumped in full)
    assert ("longtext " * 40) not in log_text
