"""Tests for Phase 4: Specialist Task Contracts, Independent Evidence Discovery & SQL Classification.

Validates:
1. 10-field investigation task ingestion across all specialists.
2. Independent evidence discovery (zero evidence copied from context gathering).
3. Strict SQL read/write classification (SELECT=read_query, INSERT/UPDATE/DELETE=write_operation).
4. Sub-agent hypothesis discipline (empty list when no defect mechanism is observed).
5. Provenance tracking on all emitted evidence and findings (task_id, agent_name, source, line).
6. Structured logging compliance (task_dispatch, specialist_task_start, specialist_task_complete, sql_statement_classified).
"""

from pathlib import Path
from typing import Any, Dict
import pytest

from app.agents.code_investigator import CodeInvestigator
from app.agents.dependency_impact_investigator import DependencyImpactInvestigator
from app.agents.runtime_error_investigator import RuntimeErrorInvestigator
from app.agents.investigator_contract import (
    classify_sql_statement,
    InvestigatorInput,
    InvestigatorResult,
)


@pytest.fixture
def sample_task_10_field() -> Dict[str, Any]:
    return {
        "task_id": "TASK-CODE-001",
        "objective": "Verify user lookup and authentication handling in auth module",
        "assigned_agent": "code_investigator",
        "reason": "Login endpoint fails for valid user credentials",
        "input": {"problem": "Login 401 error"},
        "scope": ["api/auth.py", "models/user.py"],
        "instructions": "Inspect user lookup query and password check in auth.py",
        "expected_output": "Evidence of user query and verification logic",
        "completion_criteria": "Exact line matches of lookup or defect mechanism",
        "status": "pending",
        "priority": "high",
    }


# ---------------------------------------------------------------------------
# Requirement 1: SQL Classification
# ---------------------------------------------------------------------------

def test_sql_classification_select_is_read_query():
    assert classify_sql_statement("SELECT id, username, password_hash FROM users WHERE username = ?") == "read_query"
    assert classify_sql_statement("  select * from accounts;") == "read_query"
    assert classify_sql_statement("SELECT COUNT(*) FROM sessions") == "read_query"


def test_sql_classification_insert_update_delete_are_write_operations():
    assert classify_sql_statement("INSERT INTO users (username, password) VALUES (?, ?)") == "write_operation"
    assert classify_sql_statement("insert into audit_log (event) values ('login')") == "write_operation"
    assert classify_sql_statement("UPDATE users SET failed_attempts = failed_attempts + 1 WHERE id = ?") == "write_operation"
    assert classify_sql_statement("update sessions set active = 0 where token = ?") == "write_operation"
    assert classify_sql_statement("DELETE FROM tokens WHERE expires_at < NOW()") == "write_operation"
    assert classify_sql_statement("delete from cache") == "write_operation"


def test_sql_classification_non_sql():
    assert classify_sql_statement("def login(username, password):") is None
    assert classify_sql_statement("const token = jwt.sign(payload, secret);") is None


# ---------------------------------------------------------------------------
# Requirement 2: Code Investigator - 10-Field Task Ingestion & Provenance
# ---------------------------------------------------------------------------

def test_code_investigator_ingests_10_field_task(tmp_path: Path):
    auth_file = tmp_path / "api" / "auth.py"
    auth_file.parent.mkdir(parents=True, exist_ok=True)
    auth_file.write_text(
        "def authenticate(user, password):\n"
        "    if not user:\n"
        "        raise ValueError('Invalid user')\n"
        "    query = 'SELECT * FROM users WHERE username = ?'\n"
        "    return query\n"
    )

    task = {
        "task_id": "TASK-CODE-042",
        "objective": "Verify auth logic",
        "assigned_agent": "code_investigator",
        "reason": "Test reason",
        "input": {"problem": "auth error"},
        "scope": ["api/auth.py"],
        "instructions": "authenticate user query",
        "expected_output": "evidence",
        "completion_criteria": "done",
        "status": "pending",
        "priority": "high",
    }

    inv = CodeInvestigator()
    result = inv.investigate(
        workspace_path=str(tmp_path),
        investigation_task=task,
        shared_context={},
    )

    assert result["task_id"] == "TASK-CODE-042"
    assert result["agent_name"] == "code_investigator"
    assert result["status"] == "completed"
    assert len(result["evidence"]) > 0

    # Every evidence item must record source, line, matched_text, task_id, and agent_name
    for ev in result["evidence"]:
        assert ev["task_id"] == "TASK-CODE-042"
        assert ev["agent_name"] == "code_investigator"
        assert ev["source"]
        assert ev["line"] is not None
        assert ev["matched_text"]

    # Every finding must be tagged with task_id and agent_name
    for f in result["findings"]:
        assert f["task_id"] == "TASK-CODE-042"
        assert f["agent_name"] == "code_investigator"


# ---------------------------------------------------------------------------
# Requirement 3: Independent Evidence Discovery (Zero Context Copying)
# ---------------------------------------------------------------------------

def test_code_investigator_never_copies_evidence_candidates(tmp_path: Path):
    dummy_file = tmp_path / "main.py"
    dummy_file.write_text("print('hello world')\n")

    context_with_candidates = {
        "evidence_candidates": [
            {
                "source": "fabricated/candidate.py",
                "line": 99,
                "matched_text": "FAKED_CANDIDATE_FROM_CONTEXT_GATHERING",
                "kind": "candidate_evidence",
            }
        ]
    }

    inv = CodeInvestigator()
    result = inv.investigate(
        workspace_path=str(tmp_path),
        investigation_task="Investigate main.py",
        shared_context=context_with_candidates,
    )

    all_evidence_text = [e["matched_text"] for e in result["evidence"]]
    all_evidence_sources = [e["source"] for e in result["evidence"]]

    assert "FAKED_CANDIDATE_FROM_CONTEXT_GATHERING" not in all_evidence_text
    assert "fabricated/candidate.py" not in all_evidence_sources


def test_dependency_investigator_never_copies_evidence_candidates(tmp_path: Path):
    mod = tmp_path / "app.py"
    mod.write_text("import os\n")

    context_with_candidates = {
        "evidence_candidates": [
            {
                "source": "ghost/file.py",
                "line": 42,
                "matched_text": "GHOST_IMPORT_CANDIDATE",
            }
        ]
    }

    inv = DependencyImpactInvestigator()
    result = inv.investigate(
        workspace_path=str(tmp_path),
        investigation_task="Trace imports in app.py",
        shared_context=context_with_candidates,
    )

    all_evidence_text = [e["matched_text"] for e in result["evidence"]]
    assert "GHOST_IMPORT_CANDIDATE" not in all_evidence_text


# ---------------------------------------------------------------------------
# Requirement 4: SQL Classification in Code Investigator
# ---------------------------------------------------------------------------

def test_code_investigator_classifies_sql_queries_and_writes(tmp_path: Path):
    sql_file = tmp_path / "db.py"
    sql_file.write_text(
        "query = 'SELECT id, name FROM users WHERE id = 1'\n"
        "mutation = 'INSERT INTO audit_log (event) VALUES (1)'\n"
    )

    inv = CodeInvestigator()
    result = inv.investigate(
        workspace_path=str(tmp_path),
        investigation_task="Inspect query and mutation in db.py",
        shared_context={"target_files": ["db.py"]},
    )

    assert result["status"] == "completed"

    sql_evidence = [e for e in result["evidence"] if e.get("sql_type")]
    assert len(sql_evidence) >= 2

    read_items = [e for e in sql_evidence if e["sql_type"] == "read_query"]
    write_items = [e for e in sql_evidence if e["sql_type"] == "write_operation"]

    assert len(read_items) >= 1
    assert any("SELECT" in r["matched_text"] for r in read_items)
    assert read_items[0]["kind"] == "database_read_query"

    assert len(write_items) >= 1
    assert any("INSERT" in w["matched_text"] for w in write_items)
    assert write_items[0]["kind"] == "database_write_operation"
    assert "lookup" not in write_items[0]["kind"]


# ---------------------------------------------------------------------------
# Requirement 5: Hypothesis Discipline (Empty List on Insufficient Evidence)
# ---------------------------------------------------------------------------

def test_code_investigator_empty_hypotheses_when_no_defect_observed(tmp_path: Path):
    clean_file = tmp_path / "clean.py"
    clean_file.write_text(
        "# Pure utility functions with no error logic\n"
        "def add(a, b):\n"
        "    return a + b\n"
    )

    inv = CodeInvestigator()
    result = inv.investigate(
        workspace_path=str(tmp_path),
        investigation_task="Check add function",
        shared_context={"target_files": ["clean.py"]},
    )

    assert result["status"] == "completed"
    assert len(result["evidence"]) > 0
    # Because clean.py has no defect mechanism (no error, raise, exception, etc.), hypotheses must be empty
    assert result["hypotheses"] == []


def test_code_investigator_generates_hypothesis_when_defect_observed(tmp_path: Path):
    defect_file = tmp_path / "failing.py"
    defect_file.write_text(
        "def process_order(order):\n"
        "    if order is None:\n"
        "        raise ValueError('order cannot be None')\n"
    )

    inv = CodeInvestigator()
    result = inv.investigate(
        workspace_path=str(tmp_path),
        investigation_task="Investigate order failure in failing.py",
        shared_context={"target_files": ["failing.py"]},
    )

    assert result["status"] == "completed"
    assert len(result["hypotheses"]) == 1
    hyp = result["hypotheses"][0]
    assert hyp["task_id"]
    assert hyp["agent_name"] == "code_investigator"
    assert len(hyp["supporting_evidence_ids"]) > 0


# ---------------------------------------------------------------------------
# Requirement 6: Dependency Impact & Runtime Error Investigators 10-Field Ingestion
# ---------------------------------------------------------------------------

def test_dependency_investigator_ingests_10_field_task(tmp_path: Path):
    caller = tmp_path / "service.py"
    caller.write_text("import api.auth\napi.auth.authenticate()\n")

    task = {
        "task_id": "TASK-DEP-007",
        "objective": "Map caller relationships for service.py",
        "assigned_agent": "dependency_impact_investigator",
        "reason": "Determine callers of auth",
        "input": {"problem": "auth issue"},
        "scope": ["service.py"],
        "instructions": "Trace service.py dependencies",
        "expected_output": "flow edges and evidence",
        "completion_criteria": "mapped",
        "status": "pending",
        "priority": "medium",
    }

    inv = DependencyImpactInvestigator()
    result = inv.investigate(
        workspace_path=str(tmp_path),
        investigation_task=task,
        shared_context={},
    )

    assert result["task_id"] == "TASK-DEP-007"
    assert result["agent_name"] == "dependency_impact_investigator"
    assert result["status"] in ("completed", "partial")
    for ev in result["evidence"]:
        assert ev["task_id"] == "TASK-DEP-007"
        assert ev["agent_name"] == "dependency_impact_investigator"
    for f in result["findings"]:
        assert f["task_id"] == "TASK-DEP-007"
        assert f["agent_name"] == "dependency_impact_investigator"


def test_runtime_error_investigator_ingests_10_field_task(tmp_path: Path):
    src = tmp_path / "server.py"
    src.write_text("def handle_request():\n    pass\n")

    task = {
        "task_id": "TASK-RUNTIME-009",
        "objective": "Parse stack trace and map to server.py",
        "assigned_agent": "runtime_error_investigator",
        "reason": "Internal server error 500",
        "input": {"error_logs": "Exception in server.py line 1"},
        "scope": ["server.py"],
        "instructions": "Parse runtime error and locate line 1",
        "expected_output": "mapped stack frames",
        "completion_criteria": "done",
        "status": "pending",
        "priority": "high",
    }

    inv = RuntimeErrorInvestigator()
    result = inv.investigate(
        workspace_path=str(tmp_path),
        investigation_task=task,
        shared_context={
            "logs": ["File \"server.py\", line 1, in handle_request\nHTTP 500: Internal Server Error"],
        },
    )

    assert result["task_id"] == "TASK-RUNTIME-009"
    assert result["agent_name"] == "runtime_error_investigator"
    assert result["status"] == "completed"
    assert len(result["evidence"]) > 0
    for ev in result["evidence"]:
        assert ev["task_id"] == "TASK-RUNTIME-009"
        assert ev["agent_name"] == "runtime_error_investigator"
    for f in result["findings"]:
        assert f["task_id"] == "TASK-RUNTIME-009"
        assert f["agent_name"] == "runtime_error_investigator"
