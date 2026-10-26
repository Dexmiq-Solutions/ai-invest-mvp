"""Tests for Phase 5: Single-Pass Shared-State Aggregator.

Covers:
1. test_single_pass_aggregation: Initial plan tasks executed in a single deterministic pass.
2. test_evidence_provenance_preservation: Evidence retains task_id, agent_name, originating_tasks, originating_agents.
3. test_sha256_evidence_deduplication: Identical source excerpts deduplicated via stable SHA-256 fingerprint.
4. test_execution_status_completed_and_partial: Precise execution_status ('completed', 'partial', 'failed') and workflow_status ('aggregated').
5. test_conflicting_hypotheses_preserved: Conflicting/distinct hypotheses from specialists retained separately without dropping.
6. test_no_replanning_loop_invoked: No iterative while-loop or reviewer-driven replanning executed.
7. test_workflow_terminates_after_aggregation: Workflow halts immediately after aggregation with workflow_status == "aggregated".
8. test_synthesizer_not_invoked: Root-cause synthesizer is not invoked during execution.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch
import pytest

from app.agents.investigation_orchestrator import (
    InvestigationOrchestrator,
    compute_sha256_fingerprint,
    _fingerprint,
)


@pytest.fixture
def mock_workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "test_project"
    ws.mkdir(parents=True)
    (ws / "userModel.js").write_text(
        "const db = require('./db');\n"
        "function findByEmail(email) {\n"
        "  return db.query('SELECT * FROM users WHERE email = ?', [email]);\n"
        "}\n",
        encoding="utf-8",
    )
    (ws / "authService.js").write_text(
        "const userModel = require('./userModel');\n"
        "function login(email, password) {\n"
        "  const user = userModel.findByEmail(email);\n"
        "  return signToken({ email: user.email });\n"
        "}\n",
        encoding="utf-8",
    )
    return ws


def test_sha256_evidence_deduplication():
    """Identical evidence items are deduplicated using stable SHA-256 fingerprinting."""
    orchestrator = InvestigationOrchestrator()

    item1 = {
        "source": "src/models/user.py",
        "line": 42,
        "matched_text": "SELECT * FROM users WHERE id = %s",
        "task_id": "TASK-CODE-001",
        "agent_name": "code_investigator",
    }
    item2 = {
        "source": "src/models/user.py",
        "line": 42,
        "matched_text": "SELECT * FROM users WHERE id = %s",
        "task_id": "TASK-DEP-001",
        "agent_name": "dependency_impact_investigator",
    }
    item3 = {
        "source": "src/services/auth.py",
        "line": 15,
        "matched_text": "verify_password(password, user.hash)",
        "task_id": "TASK-CODE-001",
        "agent_name": "code_investigator",
    }

    # Verify SHA-256 fingerprint calculation directly
    fp1 = compute_sha256_fingerprint(item1)
    fp2 = compute_sha256_fingerprint(item2)
    fp3 = compute_sha256_fingerprint(item3)

    assert fp1 == fp2
    assert fp1 != fp3

    expected_hash = hashlib.sha256("SELECT * FROM users WHERE id = %s".encode("utf-8")).hexdigest()
    assert fp1 == ("src/models/user.py", 42, expected_hash)

    # Deduplication test
    deduped = orchestrator.deduplicate_evidence([item1, item2, item3])
    assert len(deduped) == 2


def test_evidence_provenance_preservation():
    """Deduplicated evidence preserves originating_tasks and originating_agents lists."""
    orchestrator = InvestigationOrchestrator()

    item_code = {
        "source": "src/models/user.py",
        "line": 10,
        "matched_text": "db.select('users')",
        "task_id": "TASK-CODE-001",
        "agent_name": "code_investigator",
    }
    item_dep = {
        "source": "src/models/user.py",
        "line": 10,
        "matched_text": "db.select('users')",
        "task_id": "TASK-DEP-002",
        "agent_name": "dependency_impact_investigator",
    }

    deduped = orchestrator.deduplicate_evidence([item_code, item_dep])
    assert len(deduped) == 1

    merged = deduped[0]
    assert "TASK-CODE-001" in merged["originating_tasks"]
    assert "TASK-DEP-002" in merged["originating_tasks"]
    assert "code_investigator" in merged["originating_agents"]
    assert "dependency_impact_investigator" in merged["originating_agents"]


def test_conflicting_hypotheses_preserved():
    """Conflicting hypotheses from different agents or tasks are both retained in shared state."""
    mock_code_agent = MagicMock(return_value={
        "status": "completed",
        "findings": [],
        "evidence": [],
        "hypotheses": [
            {
                "hypothesis_id": "HYP-001",
                "statement": "Defect is in user model SQL query collation.",
                "supporting_evidence_ids": ["EV-001"],
                "status": "unverified",
            }
        ],
    })
    mock_dep_agent = MagicMock(return_value={
        "status": "completed",
        "findings": [],
        "evidence": [],
        "hypotheses": [
            {
                "hypothesis_id": "HYP-002",
                "statement": "Defect is in token payload encoding before database lookup.",
                "supporting_evidence_ids": ["EV-002"],
                "status": "unverified",
            }
        ],
    })

    orchestrator = InvestigationOrchestrator(
        code_investigator=mock_code_agent,
        dependency_investigator=mock_dep_agent,
    )

    plan = [
        {
            "task_id": "TASK-CODE-001",
            "objective": "Inspect query collation",
            "assigned_agent": "code_investigator",
            "reason": "Verify database query collation",
            "input": {"problem": "Token mismatch"},
            "scope": ["userModel.js"],
            "instructions": "Inspect userModel.js query",
            "expected_output": "Query collation",
            "completion_criteria": "Verified collation",
            "status": "pending",
        },
        {
            "task_id": "TASK-DEP-001",
            "objective": "Inspect caller pipeline",
            "assigned_agent": "dependency_impact_investigator",
            "reason": "Verify caller chain",
            "input": {"problem": "Token mismatch"},
            "scope": ["authService.js"],
            "instructions": "Inspect caller pipeline in authService.js",
            "expected_output": "Caller chain",
            "completion_criteria": "Verified callers",
            "status": "pending",
        },
    ]

    result = orchestrator.execute_plan_single_pass(
        plan=plan,
        state={"problem": "Token email mismatch", "workspace_path": "."},
        context={"relevant_files": ["userModel.js", "authService.js"]},
    )

    assert result["execution_status"] == "completed"
    assert result["workflow_status"] == "aggregated"
    assert len(result["aggregated_hypotheses"]) == 2

    statements = [h["statement"] for h in result["aggregated_hypotheses"]]
    assert "Defect is in user model SQL query collation." in statements
    assert "Defect is in token payload encoding before database lookup." in statements


def test_single_pass_aggregation(mock_workspace: Path):
    """Executes all tasks in the initial plan in a single pass without looping."""
    call_counts = {"code": 0, "dep": 0}

    def counting_code_agent(step, context, state):
        call_counts["code"] += 1
        return {
            "status": "completed",
            "findings": [{"statement": f"Finding from code {call_counts['code']}"}],
            "evidence": [{
                "source": "userModel.js",
                "line": 2,
                "matched_text": "function findByEmail",
                "kind": "direct_code",
            }],
            "hypotheses": [],
        }

    def counting_dep_agent(step, context, state):
        call_counts["dep"] += 1
        return {
            "status": "completed",
            "findings": [{"statement": f"Finding from dep {call_counts['dep']}"}],
            "evidence": [{
                "source": "authService.js",
                "line": 3,
                "matched_text": "userModel.findByEmail",
                "kind": "direct_code",
            }],
            "hypotheses": [],
        }

    orchestrator = InvestigationOrchestrator(
        code_investigator=counting_code_agent,
        dependency_investigator=counting_dep_agent,
    )

    plan = [
        {
            "task_id": "TASK-CODE-001",
            "objective": "Inspect userModel logic",
            "assigned_agent": "code_investigator",
            "reason": "Verify user model",
            "input": {"problem": "Token mismatch"},
            "scope": ["userModel.js"],
            "instructions": "Inspect userModel.js findByEmail",
            "expected_output": "Function definition",
            "completion_criteria": "Function inspected",
            "status": "pending",
        },
        {
            "task_id": "TASK-DEP-001",
            "objective": "Inspect authService callers",
            "assigned_agent": "dependency_impact_investigator",
            "reason": "Verify callers",
            "input": {"problem": "Token mismatch"},
            "scope": ["authService.js"],
            "instructions": "Inspect authService callers",
            "expected_output": "Caller definitions",
            "completion_criteria": "Callers inspected",
            "status": "pending",
        },
    ]

    result = orchestrator.execute_plan_single_pass(
        plan=plan,
        state={"problem": "Token email mismatch", "workspace_path": str(mock_workspace)},
        context={"relevant_files": ["userModel.js", "authService.js"]},
    )

    # Exactly one execution per task in the initial plan
    assert call_counts["code"] == 1
    assert call_counts["dep"] == 1
    assert result["execution_status"] == "completed"
    assert result["workflow_status"] == "aggregated"
    assert len(result["completed_tasks"]) == 2
    assert len(result["evidence_pool"]) == 2
    assert len(result["aggregated_findings"]) == 2


def test_execution_status_completed_and_partial(mock_workspace: Path):
    """Verifies execution_status is 'completed' when all tasks pass, and 'partial' when some fail."""
    def success_code_agent(step, context, state):
        return {
            "status": "completed",
            "findings": [{"statement": "Code inspected successfully"}],
            "evidence": [{"source": "userModel.js", "line": 1, "matched_text": "require"}],
            "hypotheses": [],
        }

    def failing_dep_agent(step, context, state):
        raise RuntimeError("Network timeout or parse error in dependency specialist")

    orchestrator = InvestigationOrchestrator(
        code_investigator=success_code_agent,
        dependency_investigator=failing_dep_agent,
    )

    plan = [
        {
            "task_id": "TASK-CODE-001",
            "objective": "Inspect userModel",
            "assigned_agent": "code_investigator",
            "reason": "Check queries",
            "input": {"problem": "Token mismatch"},
            "scope": ["userModel.js"],
            "instructions": "Inspect userModel",
            "expected_output": "Queries",
            "completion_criteria": "Checked",
            "status": "pending",
        },
        {
            "task_id": "TASK-DEP-001",
            "objective": "Trace dependencies",
            "assigned_agent": "dependency_impact_investigator",
            "reason": "Trace imports",
            "input": {"problem": "Token mismatch"},
            "scope": ["authService.js"],
            "instructions": "Trace imports",
            "expected_output": "Imports",
            "completion_criteria": "Traced",
            "status": "pending",
        },
    ]

    # Partial execution: code succeeds, dep fails
    result = orchestrator.execute_plan_single_pass(
        plan=plan,
        state={"problem": "Token email mismatch", "workspace_path": str(mock_workspace)},
        context={"relevant_files": ["userModel.js", "authService.js"]},
    )

    assert result["execution_status"] == "partial"
    assert result["workflow_status"] == "aggregated"
    assert len(result["completed_tasks"]) == 1
    assert len(result["failed_tasks"]) == 1
    assert "code_investigator" in result["completed_investigators"]
    assert "dependency_impact_investigator" in result["failed_investigators"]
    assert len(result["evidence_pool"]) == 1


def test_no_replanning_loop_invoked(mock_workspace: Path):
    """Confirms that planner.plan is not called in a dynamic replanning while-loop."""
    orchestrator = InvestigationOrchestrator()
    orchestrator.planner = MagicMock()

    plan = [
        {
            "task_id": "TASK-CODE-001",
            "objective": "Inspect userModel",
            "assigned_agent": "code_investigator",
            "reason": "Check queries",
            "input": {"problem": "Token mismatch"},
            "scope": ["userModel.js"],
            "instructions": "Inspect userModel",
            "expected_output": "Queries",
            "completion_criteria": "Checked",
            "status": "pending",
        }
    ]

    result = orchestrator.execute_plan_single_pass(
        plan=plan,
        state={"problem": "Token email mismatch", "workspace_path": str(mock_workspace)},
        context={"relevant_files": ["userModel.js"]},
    )

    assert result["execution_status"] == "completed"
    assert result["workflow_status"] == "aggregated"
    # Planner was not invoked to re-plan
    orchestrator.planner.plan.assert_not_called()


def test_workflow_terminates_after_aggregation(mock_workspace: Path):
    """Workflow terminates immediately after aggregation with workflow_status == 'aggregated'."""
    orchestrator = InvestigationOrchestrator()

    plan = [
        {
            "task_id": "TASK-CODE-001",
            "objective": "Inspect userModel",
            "assigned_agent": "code_investigator",
            "reason": "Check queries",
            "input": {"problem": "Token mismatch"},
            "scope": ["userModel.js"],
            "instructions": "Inspect userModel",
            "expected_output": "Queries",
            "completion_criteria": "Checked",
            "status": "pending",
        }
    ]

    result = orchestrator.execute_plan_single_pass(
        plan=plan,
        state={"problem": "Token email mismatch", "workspace_path": str(mock_workspace)},
        context={"relevant_files": ["userModel.js"]},
    )

    assert result["workflow_status"] == "aggregated"
    assert result["stop_reason"] == "Investigation workflow completed after shared state aggregation"


def test_synthesizer_not_invoked(mock_workspace: Path):
    """Asserts DiagnosisSynthesizer.synthesize is NOT called and likely_root_cause is empty."""
    orchestrator = InvestigationOrchestrator()
    orchestrator.synthesizer = MagicMock()

    plan = [
        {
            "task_id": "TASK-CODE-001",
            "objective": "Inspect userModel",
            "assigned_agent": "code_investigator",
            "reason": "Check queries",
            "input": {"problem": "Token mismatch"},
            "scope": ["userModel.js"],
            "instructions": "Inspect userModel",
            "expected_output": "Queries",
            "completion_criteria": "Checked",
            "status": "pending",
        }
    ]

    result = orchestrator.execute_plan_single_pass(
        plan=plan,
        state={"problem": "Token email mismatch", "workspace_path": str(mock_workspace)},
        context={"relevant_files": ["userModel.js"]},
    )

    # Root-cause synthesis is strictly out of scope
    orchestrator.synthesizer.synthesize.assert_not_called()
    assert result["diagnosis"] == {}
    assert result["likely_root_cause"] == ""
