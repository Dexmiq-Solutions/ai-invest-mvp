"""Phase 7 Test Suite: Specialist Hypothesis Generation and Shared-State Aggregation.

Covers all 12 acceptance criteria specified in Phase 7:
1. A specialist can return multiple structured hypotheses.
2. Each hypothesis retains its originating specialist.
3. Actual evidence is linked to the correct hypothesis.
4. Supporting and contradicting evidence can both be represented.
5. Results from multiple specialists are preserved in shared state.
6. The final state contains both individual specialist results and the consolidated hypotheses.
7. Multiple competing hypotheses are retained.
8. Duplicate hypotheses can be recognized without losing meaningful evidence or investigator attribution.
9. An investigator returning no hypotheses is handled explicitly.
10. Missing evidence is not fabricated.
11. Tool failures are not treated as evidence of an application defect.
12. Existing Phase 1-6 behavior remains intact.
"""

from pathlib import Path
import pytest

from app.agents.code_investigator import CodeInvestigator
from app.agents.dependency_impact_investigator import DependencyImpactInvestigator
from app.agents.runtime_error_investigator import RuntimeErrorInvestigator
from app.agents.investigation_orchestrator import InvestigationOrchestrator
from app.agents.investigator_contract import create_hypothesis, HypothesisRecord


# ---------------------------------------------------------------------------
# Criterion 1: A specialist can return multiple structured hypotheses
# ---------------------------------------------------------------------------

def test_specialist_multiple_structured_hypotheses(tmp_path: Path):
    """When a specialist investigates a workspace with multiple defect indicators,
    it produces multiple structured hypotheses with all required Phase 7 fields."""
    file_a = tmp_path / "auth_service.py"
    file_a.write_text(
        "def authenticate(token):\n"
        "    if token is None:\n"
        "        raise PermissionError('unauthorized: missing token')\n"
    )

    file_b = tmp_path / "data_handler.py"
    file_b.write_text(
        "def fetch_records(payload):\n"
        "    if payload == 'invalid':\n"
        "        raise ValueError('failed to parse records')\n"
    )

    investigator = CodeInvestigator()
    result = investigator.investigate(
        workspace_path=str(tmp_path),
        investigation_task="Investigate unauthorized and failed record processing",
        shared_context={"target_files": ["auth_service.py", "data_handler.py"]},
    )

    assert result["status"] == "completed"
    assert len(result["hypotheses"]) >= 2

    # Check each hypothesis structure conforms to Phase 7 contract
    for hyp in result["hypotheses"]:
        assert hyp["hypothesis_id"].startswith("HYP-")
        assert hyp["hypothesis_title"]
        assert hyp["produced_by"] == "Code Investigator"
        assert hyp["possible_cause"]
        assert hyp["evidence"]
        assert hyp["reasoning"]
        assert isinstance(hyp["supporting_evidence"], list)
        assert isinstance(hyp["contradicting_evidence"], list)
        assert hyp["status"] in ("needs_validation", "supported", "contradicted")
        assert hyp["confidence"] in ("low", "medium", "high")
        assert isinstance(hyp["unanswered_questions"], list)
        assert len(hyp["unanswered_questions"]) > 0


# ---------------------------------------------------------------------------
# Criterion 2: Each hypothesis retains its originating specialist
# ---------------------------------------------------------------------------

def test_each_hypothesis_retains_originating_specialist(tmp_path: Path):
    """Specialists must preserve their own identity on every hypothesis they produce."""
    src = tmp_path / "app.py"
    src.write_text("import utils\ndef run():\n    raise RuntimeError('failed')\n")

    code_inv = CodeInvestigator()
    res_code = code_inv.investigate(
        workspace_path=str(tmp_path),
        investigation_task="Check RuntimeError in app.py",
        shared_context={"target_files": ["app.py"]},
    )
    assert len(res_code["hypotheses"]) >= 1
    assert res_code["hypotheses"][0]["produced_by"] == "Code Investigator"
    assert res_code["hypotheses"][0]["agent_name"] == "code_investigator"

    runtime_inv = RuntimeErrorInvestigator()
    res_runtime = runtime_inv.investigate(
        workspace_path=str(tmp_path),
        investigation_task="Trace error in app.py",
        shared_context={
            "stack_trace": '  File "app.py", line 3, in run\n    raise RuntimeError(\'failed\')\nRuntimeError: failed',
        },
    )
    assert len(res_runtime["hypotheses"]) >= 1
    assert res_runtime["hypotheses"][0]["produced_by"] == "Runtime / Error Investigator"
    assert res_runtime["hypotheses"][0]["agent_name"] == "runtime_error_investigator"


# ---------------------------------------------------------------------------
# Criterion 3: Actual evidence is linked to the correct hypothesis
# ---------------------------------------------------------------------------

def test_evidence_linked_to_correct_hypothesis(tmp_path: Path):
    """Evidence records and evidence IDs must be correctly linked to the hypothesis."""
    src = tmp_path / "validator.py"
    src.write_text(
        "def check_credentials(user, password):\n"
        "    if password != 'secret':\n"
        "        return {'status': 'unauthorized', 'code': 401}\n"
    )

    investigator = CodeInvestigator()
    result = investigator.investigate(
        workspace_path=str(tmp_path),
        investigation_task="Investigate 401 unauthorized status in validator.py",
        shared_context={"target_files": ["validator.py"]},
    )

    assert len(result["hypotheses"]) >= 1
    hyp = result["hypotheses"][0]

    assert len(hyp["supporting_evidence_ids"]) >= 1
    linked_id = hyp["supporting_evidence_ids"][0]

    # Find the corresponding evidence record in result['evidence']
    matching_ev = [ev for ev in result["evidence"] if ev["evidence_id"] == linked_id]
    assert len(matching_ev) == 1
    assert "validator.py" in matching_ev[0]["source"]
    assert "unauthorized" in matching_ev[0]["matched_text"].lower() or "401" in matching_ev[0]["matched_text"]


# ---------------------------------------------------------------------------
# Criterion 4: Supporting and contradicting evidence can both be represented
# ---------------------------------------------------------------------------

def test_supporting_and_contradicting_evidence_represented():
    """Hypotheses can represent both supporting and contradicting evidence."""
    hyp = create_hypothesis(
        hypothesis_id="HYP-AUTH-001",
        hypothesis_title="Missing authorization header",
        produced_by="Code Investigator",
        possible_cause="Frontend request may omit authorization header",
        evidence="api/profile.js:14 — `fetch('/api/profile')`",
        reasoning="If endpoint requires bearer token and request omits it, backend returns 401.",
        supporting_evidence=[
            {"evidence_id": "EV-001", "source": "api/profile.js", "line": 14, "snippet": "fetch('/api/profile')"}
        ],
        contradicting_evidence=[
            {"evidence_id": "EV-002", "source": "api/client.js", "line": 28, "snippet": "headers.set('Authorization', token)"}
        ],
        status="needs_validation",
        confidence="medium",
        unanswered_questions=["Does the client wrapper attach the header automatically?"],
    )

    assert hyp["hypothesis_id"] == "HYP-AUTH-001"
    assert len(hyp["supporting_evidence"]) == 1
    assert len(hyp["supporting_evidence_ids"]) == 1
    assert "EV-001" in hyp["supporting_evidence_ids"]
    assert len(hyp["contradicting_evidence"]) == 1
    assert len(hyp["contradicting_evidence_ids"]) == 1
    assert "EV-002" in hyp["contradicting_evidence_ids"]
    assert hyp["status"] == "needs_validation"


# ---------------------------------------------------------------------------
# Criterion 5: Results from multiple specialists are preserved in shared state
# ---------------------------------------------------------------------------

def test_results_from_multiple_specialists_preserved_in_shared_state(tmp_path: Path):
    """When multiple specialists run, each specialist's findings and hypotheses
    are preserved together in task_results without overwriting earlier results."""
    file_code = tmp_path / "server.py"
    file_code.write_text("def handle_request():\n    if not auth:\n        raise PermissionError('unauthorized')\n")

    plan = [
        {
            "task_id": "TASK-CODE-001",
            "step_id": "step-1",
            "assigned_agent": "code_investigator",
            "investigator": "code_investigator",
            "objective": "Inspect server.py code for unauthorized errors",
            "instructions": "Inspect server.py for unauthorized error",
            "input": {"problem": "Application returns 401 unauthorized in server.py"},
            "scope": ["server.py"],
            "reason": "Code analysis of server.py",
            "expected_output": "Code evidence and findings",
            "completion_criteria": "Inspection complete",
            "status": "pending",
        },
        {
            "task_id": "TASK-RUNTIME-001",
            "step_id": "step-2",
            "assigned_agent": "runtime_error_investigator",
            "investigator": "runtime_error_investigator",
            "objective": "Inspect runtime logs and HTTP status 401",
            "instructions": "Inspect HTTP 401 status in server.py",
            "input": {"problem": "Application returns 401 unauthorized in server.py"},
            "scope": ["server.py"],
            "reason": "Runtime analysis of 401 response",
            "expected_output": "Runtime evidence and findings",
            "completion_criteria": "Inspection complete",
            "status": "pending",
        },
    ]

    orch = InvestigationOrchestrator(workspaces_root=tmp_path.parent)
    result = orch.execute_plan_single_pass(
        plan=plan,
        state={
            "problem": "Application returns 401 unauthorized in server.py",
            "workspace_path": str(tmp_path),
            "gathered_context": {
                "workspace_path": str(tmp_path),
                "api_response": {"status_code": 401, "error": "Unauthorized"},
            },
        },
    )

    assert result["execution_status"] == "completed"
    assert "TASK-CODE-001" in result["task_results"]
    assert "TASK-RUNTIME-001" in result["task_results"]

    # Earlier specialist result was not overwritten
    res1 = result["task_results"]["TASK-CODE-001"]
    res2 = result["task_results"]["TASK-RUNTIME-001"]

    assert res1["agent_name"] == "code_investigator"
    assert res2["agent_name"] == "runtime_error_investigator"
    assert len(res1["findings"]) > 0
    assert len(res2["findings"]) > 0


# ---------------------------------------------------------------------------
# Criterion 6: Final state contains individual specialist results & consolidated hypotheses
# ---------------------------------------------------------------------------

def test_final_state_contains_individual_and_consolidated_hypotheses(tmp_path: Path):
    """The final state must expose task_results (individual results) and
    aggregated_hypotheses / hypotheses (consolidated results)."""
    auth_file = tmp_path / "auth.py"
    auth_file.write_text("def check_token(t):\n    if not t:\n        raise ValueError('missing token')\n")

    plan = [
        {
            "task_id": "TASK-CODE-001",
            "assigned_agent": "code_investigator",
            "objective": "Inspect auth.py token logic",
            "instructions": "Inspect auth.py token logic for failures",
            "input": {"problem": "Auth fails with missing token"},
            "scope": ["auth.py"],
            "reason": "Code check of auth.py",
            "expected_output": "Evidence and findings",
            "completion_criteria": "Done",
            "status": "pending",
        }
    ]

    orch = InvestigationOrchestrator(workspaces_root=tmp_path.parent)
    result = orch.execute_plan_single_pass(
        plan=plan,
        state={"problem": "Auth fails with missing token", "workspace_path": str(tmp_path)},
    )

    # 1. Individual results accessible
    assert "task_results" in result
    assert "TASK-CODE-001" in result["task_results"]
    assert "hypotheses" in result["task_results"]["TASK-CODE-001"]

    # 2. Consolidated collection accessible
    assert "aggregated_hypotheses" in result
    assert "hypotheses" in result
    assert len(result["aggregated_hypotheses"]) > 0
    assert "unanswered_questions" in result


# ---------------------------------------------------------------------------
# Criterion 7: Multiple competing hypotheses are retained
# ---------------------------------------------------------------------------

def test_multiple_competing_hypotheses_retained(tmp_path: Path):
    """Competing hypotheses proposing different causes must be preserved together
    rather than having one prematurely picked as the winner."""
    orch = InvestigationOrchestrator(workspaces_root=tmp_path.parent)

    # Simulate two specialist outputs with competing explanations
    task_results = {
        "TASK-CODE-001": {
            "task_id": "TASK-CODE-001",
            "agent_name": "code_investigator",
            "investigator_name": "Code Investigator",
            "findings": [],
            "evidence": [{"source": "frontend/auth.js", "line": 10, "matched_text": "no token"}],
            "hypotheses": [
                create_hypothesis(
                    hypothesis_id="HYP-001",
                    hypothesis_title="Frontend omitted token",
                    produced_by="Code Investigator",
                    possible_cause="Frontend request omitted the Authorization header",
                    evidence="frontend/auth.js:10",
                    reasoning="Missing token causes 401 response from backend.",
                    supporting_evidence_ids=["EV-001"],
                    status="needs_validation",
                )
            ],
            "unanswered_questions": ["Is token stored in local storage?"],
        },
        "TASK-RUNTIME-001": {
            "task_id": "TASK-RUNTIME-001",
            "agent_name": "runtime_error_investigator",
            "investigator_name": "Runtime / Error Investigator",
            "findings": [],
            "evidence": [{"source": "backend/jwt.py", "line": 45, "matched_text": "expired signature"}],
            "hypotheses": [
                create_hypothesis(
                    hypothesis_id="HYP-002",
                    hypothesis_title="Token expired validation failure",
                    produced_by="Runtime / Error Investigator",
                    possible_cause="Backend rejected token because its signature expired",
                    evidence="backend/jwt.py:45",
                    reasoning="Expired token triggers 401 response even if header is present.",
                    supporting_evidence_ids=["EV-002"],
                    status="needs_validation",
                )
            ],
            "unanswered_questions": ["What is the server clock skew?"],
        },
    }

    plan = [
        {
            "task_id": "TASK-CODE-001",
            "assigned_agent": "code_investigator",
            "objective": "Inspect frontend auth token construction",
            "instructions": "Inspect frontend/auth.js for 401 unauthorized token issue",
            "input": {"problem": "401 Unauthorized token error"},
            "scope": ["frontend/auth.js"],
            "reason": "Frontend check",
            "expected_output": "Evidence and findings",
            "completion_criteria": "Inspection complete",
            "status": "pending",
        },
        {
            "task_id": "TASK-RUNTIME-001",
            "assigned_agent": "runtime_error_investigator",
            "objective": "Inspect backend token validation runtime trace",
            "instructions": "Inspect backend/jwt.py for 401 unauthorized runtime error",
            "input": {"problem": "401 Unauthorized token error"},
            "scope": ["backend/jwt.py"],
            "reason": "Backend runtime check",
            "expected_output": "Evidence and findings",
            "completion_criteria": "Inspection complete",
            "status": "pending",
        },
    ]

    orch.code_agent = lambda step, ctx, st: task_results["TASK-CODE-001"]
    orch.runtime_agent = lambda step, ctx, st: task_results["TASK-RUNTIME-001"]

    res = orch.execute_plan_single_pass(
        plan=plan,
        state={"problem": "401 Unauthorized token error", "workspace_path": str(tmp_path)},
    )

    # Both competing hypotheses are preserved
    assert len(res["aggregated_hypotheses"]) == 2
    titles = [h["hypothesis_title"] for h in res["aggregated_hypotheses"]]
    assert "Frontend omitted token" in titles
    assert "Token expired validation failure" in titles


# ---------------------------------------------------------------------------
# Criterion 8: Duplicate hypotheses recognized without losing attribution
# ---------------------------------------------------------------------------

def test_duplicate_hypotheses_recognized_without_losing_attribution(tmp_path: Path):
    """When two specialists propose the same defect hypothesis, the duplicates
    are consolidated while preserving both investigators' contributions and evidence."""
    orch = InvestigationOrchestrator(workspaces_root=tmp_path.parent)

    task_1 = {
        "task_id": "TASK-CODE-001",
        "agent_name": "code_investigator",
        "investigator_name": "Code Investigator",
        "status": "completed",
        "findings": [],
        "evidence": [{"source": "backend/auth.py", "line": 25, "matched_text": "expired token check"}],
        "hypotheses": [
            create_hypothesis(
                hypothesis_id="HYP-001",
                hypothesis_title="Token validation failure in auth.py",
                produced_by="Code Investigator",
                possible_cause="Token signature validation fails in backend/auth.py",
                evidence="backend/auth.py:25",
                reasoning="Token validation fails causing 401.",
                supporting_evidence_ids=["EV-CODE-001"],
            )
        ],
        "unanswered_questions": ["Is token expired?"],
    }

    task_2 = {
        "task_id": "TASK-RUNTIME-001",
        "agent_name": "runtime_error_investigator",
        "investigator_name": "Runtime / Error Investigator",
        "status": "completed",
        "findings": [],
        "evidence": [{"source": "backend/auth.py", "line": 25, "matched_text": "JWTExpiredError"}],
        "hypotheses": [
            create_hypothesis(
                hypothesis_id="HYP-002",
                hypothesis_title="Token validation failure in auth.py",
                produced_by="Runtime / Error Investigator",
                possible_cause="Token signature validation fails in backend/auth.py",
                evidence="backend/auth.py:25",
                reasoning="Token validation fails causing 401.",
                supporting_evidence_ids=["EV-RUNTIME-001"],
            )
        ],
        "unanswered_questions": ["What was the token expiration timestamp?"],
    }

    orch.code_agent = lambda s, c, st: task_1
    orch.runtime_agent = lambda s, c, st: task_2

    plan = [
        {
            "task_id": "TASK-CODE-001",
            "assigned_agent": "code_investigator",
            "objective": "Inspect backend auth code failure",
            "instructions": "Inspect backend/auth.py token validation failure",
            "input": {"problem": "Token validation failure in auth.py"},
            "scope": ["backend/auth.py"],
            "reason": "Code check of auth.py",
            "expected_output": "Evidence and findings",
            "completion_criteria": "Inspection complete",
            "status": "pending",
        },
        {
            "task_id": "TASK-RUNTIME-001",
            "assigned_agent": "runtime_error_investigator",
            "objective": "Inspect backend auth runtime failure",
            "instructions": "Inspect backend/auth.py token validation failure logs",
            "input": {"problem": "Token validation failure in auth.py"},
            "scope": ["backend/auth.py"],
            "reason": "Runtime check of auth.py",
            "expected_output": "Evidence and findings",
            "completion_criteria": "Inspection complete",
            "status": "pending",
        },
    ]

    res = orch.execute_plan_single_pass(
        plan=plan,
        state={"problem": "Token validation failure in auth.py", "workspace_path": str(tmp_path)},
    )

    # Consolidated down to 1 hypothesis entry
    assert len(res["aggregated_hypotheses"]) == 1
    consolidated = res["aggregated_hypotheses"][0]

    # Both specialists are credited in contributing_specialists
    contributors = consolidated["contributing_specialists"]
    assert "Code Investigator" in contributors
    assert "Runtime / Error Investigator" in contributors

    # Both evidence IDs are retained
    supp_ids = consolidated["supporting_evidence_ids"]
    assert "EV-CODE-001" in supp_ids
    assert "EV-RUNTIME-001" in supp_ids

    # Unanswered questions from both specialists are merged
    assert len(consolidated["unanswered_questions"]) == 2

    # In task_results, individual specialist outputs remain distinct
    assert len(res["task_results"]["TASK-CODE-001"]["hypotheses"]) == 1
    assert len(res["task_results"]["TASK-RUNTIME-001"]["hypotheses"]) == 1


# ---------------------------------------------------------------------------
# Criterion 9: An investigator returning no hypotheses is handled explicitly
# ---------------------------------------------------------------------------

def test_investigator_no_hypotheses_handled_explicitly(tmp_path: Path):
    """When a specialist cannot find defect indicators, it explicitly returns an
    empty hypothesis list without inventing findings."""
    clean_file = tmp_path / "clean.py"
    clean_file.write_text("def add(a, b):\n    return a + b\n")

    inv = CodeInvestigator()
    result = inv.investigate(
        workspace_path=str(tmp_path),
        investigation_task="Check calculation in clean.py",
        shared_context={"target_files": ["clean.py"]},
    )

    assert result["hypotheses"] == []
    assert len(result["unanswered_questions"]) > 0
    assert any("defect mechanism" in q.lower() or "source-code" in q.lower() for q in result["unanswered_questions"])


# ---------------------------------------------------------------------------
# Criterion 10: Missing evidence is not fabricated
# ---------------------------------------------------------------------------

def test_missing_evidence_not_fabricated(tmp_path: Path):
    """When evidence is unavailable, fields remain empty/absent rather than being fabricated."""
    inv = CodeInvestigator()
    result = inv.investigate(
        workspace_path=str(tmp_path),
        investigation_task="Investigate missing module nonexistent.py",
        shared_context={"target_files": ["nonexistent.py"]},
    )

    assert result["hypotheses"] == []
    assert len(result["evidence"]) == 0
    assert len(result["missing_evidence"]) > 0
    # No made-up lines or imaginary file paths
    for item in result["evidence"]:
        assert "nonexistent.py" != item["source"]


# ---------------------------------------------------------------------------
# Criterion 11: Tool failures are not treated as evidence of an application defect
# ---------------------------------------------------------------------------

def test_tool_failures_not_treated_as_application_defects(tmp_path: Path):
    """Tool execution failures (e.g. search tool exception) must be reported in
    tool_errors and never converted into defect hypotheses."""
    def failing_search(workspace, query, max_results=10):
        raise ConnectionError("Search service unavailable")

    inv = CodeInvestigator(search_code_tool=failing_search)
    result = inv.investigate(
        workspace_path=str(tmp_path),
        investigation_task="Search for auth errors",
    )

    assert len(result["tool_errors"]) > 0
    assert any("ConnectionError" in err["error"] for err in result["tool_errors"])
    # Tool failure must NOT produce a defect hypothesis
    assert result["hypotheses"] == []


# ---------------------------------------------------------------------------
# Criterion 12: Existing Phase 1-6 behavior remains intact
# ---------------------------------------------------------------------------

def test_backward_compatibility_intact():
    """Verify that HypothesisRecord satisfies backward compatibility requirements:
    category, statement, task_id, agent_name, verification_status."""
    hyp = create_hypothesis(
        hypothesis_id="HYP-001",
        hypothesis_title="Test Title",
        produced_by="Code Investigator",
        possible_cause="Possible test cause",
        evidence="test.py:1",
        reasoning="Test reasoning",
        task_id="TASK-001",
        agent_name="code_investigator",
    )

    assert hyp["category"] == "Possible Cause"
    assert hyp["statement"] == "Test Title: Possible test cause"
    assert hyp["task_id"] == "TASK-001"
    assert hyp["agent_name"] == "code_investigator"
    assert hyp["verification_status"] == "unverified"
    assert hyp["status"] == "needs_validation"
