"""Regression tests for Investigation Quality Improvements.

Verifies:
1. Code investigator ignores non-application seed scripts and lockfiles.
2. Code investigator detects casing/normalization discrepancies without hallucinating on nulls.
3. Dependency impact investigator does not blame boilerplate error handlers or imports.
4. Runtime error investigator accurately reports missing server traces for HTTP 4xx failures.
5. Orchestrator generalizes across distinct problem domains (Auth 401, Calculation, Runtime Exception).
"""
import pytest
from app.agents.code_investigator import CodeInvestigator, _is_non_application_path
from app.agents.dependency_impact_investigator import (
    DependencyImpactInvestigator,
    _is_boilerplate_error_handler,
)
from app.agents.runtime_error_investigator import RuntimeErrorInvestigator
from app.agents.investigation_orchestrator import InvestigationPlanner, InvestigationOrchestrator
from app.agents.reviewer import Reviewer
from app.agents.diagnostician import Diagnostician
from app.graph.graph import inconclusive_result_node


def test_is_non_application_path_filters_correctly():
    assert _is_non_application_path("taskflow/backend/scripts/seed.js") is True
    assert _is_non_application_path("package-lock.json") is True
    assert _is_non_application_path("fixtures/users.json") is True
    assert _is_non_application_path("taskflow/backend/src/models/userModel.js") is False
    assert _is_non_application_path("taskflow/backend/src/services/authService.js") is False


def test_is_boilerplate_error_handler_filters_correctly():
    assert _is_boilerplate_error_handler("const errorHandler = (err, req, res, next) => {") is True
    assert _is_boilerplate_error_handler("app.use(errorHandler);") is True
    assert _is_boilerplate_error_handler("const auth = require('./middleware/authenticate');") is False


def test_code_investigator_normalization_discrepancy():
    """Verify code investigator detects normalization mismatches without citing seed.js."""
    def mock_list_files(workspace):
        return [
            "src/services/authService.js",
            "src/models/userModel.js",
            "scripts/seed.js",
        ], []

    def mock_read_file(workspace, path):
        if "authService" in path:
            return (
                "export function register({ email }) {\n"
                "  const cleanEmail = String(email).trim();\n"
                "  return userModel.create({ email: cleanEmail });\n"
                "}\n"
                "export function login({ email }) {\n"
                "  const normalizedEmail = String(email).trim().toLowerCase();\n"
                "  return userModel.findByLogin(normalizedEmail);\n"
                "}\n"
            ), None
        if "userModel" in path:
            return (
                "const byEmail = db.prepare('SELECT * FROM users WHERE email = ?');\n"
                "const byLogin = db.prepare('SELECT * FROM users WHERE LOWER(email) = ?');\n"
                "export function findByEmail(email) { return byEmail.get(email); }\n"
            ), None
        if "seed.js" in path:
            return "const seedUsers = [{ email: 'admin@example.com', bio: null }];", None
        return "", None

    def mock_search_code(workspace, query, *args, **kwargs):
        hits = []
        if query in ("email", "login", "user", "findbyemail"):
            hits.append({"path": "src/services/authService.js", "line": 2, "line_text": "cleanEmail = String(email).trim();"})
            hits.append({"path": "src/services/authService.js", "line": 5, "line_text": "normalizedEmail = String(email).trim().toLowerCase();"})
            hits.append({"path": "src/models/userModel.js", "line": 1, "line_text": "const byEmail = db.prepare('SELECT * FROM users WHERE email = ?');"})
            hits.append({"path": "scripts/seed.js", "line": 1, "line_text": "bio: null"})
        return hits, []

    inv = CodeInvestigator(
        list_files_tool=mock_list_files,
        read_file_tool=mock_read_file,
        search_code_tool=mock_search_code,
    )
    task = {
        "task_id": "TASK-CODE-001",
        "instructions": "Inspect email normalization and user lookup queries",
        "scope": ["src/services/authService.js", "src/models/userModel.js", "scripts/seed.js"],
    }
    result = inv.investigate(
        workspace_path="/dummy",
        investigation_task=task,
        shared_context={"problem": "Login succeeds, but opening profile page returns 401 Unauthorized."},
    )

    assert result["status"] == "completed"
    # Must NOT generate hypothesis blaming seed.js null
    for h in result["hypotheses"]:
        assert "seed.js" not in h["possible_cause"]
        assert "seed.js" not in h.get("evidence", "")

    # Must detect casing / normalization discrepancy
    assert len(result["hypotheses"]) > 0
    hyp = result["hypotheses"][0]
    assert "normalization" in hyp["hypothesis_title"].lower() or "casing" in hyp["hypothesis_title"].lower()


def test_dependency_investigator_ignores_boilerplate_error_handlers():
    """Verify dependency investigator does not falsely blame errorHandler.js or server.js imports."""
    def mock_list_files(workspace):
        return [
            "src/server.js",
            "src/middleware/errorHandler.js",
            "src/middleware/authenticate.js",
        ], []

    def mock_read_file(workspace, path):
        if "server" in path:
            return "const errorHandler = require('./middleware/errorHandler');\napp.use(errorHandler);", None
        if "errorHandler" in path:
            return "const errorHandler = (err, req, res, next) => { res.status(500).json({ error: err.message }); };", None
        if "authenticate" in path:
            return "export function authenticate(req, res, next) { next(); }", None
        return "", None

    def mock_search_code(workspace, query, *args, **kwargs):
        return [
            {"path": "src/server.js", "line": 1, "line_text": "const errorHandler = require('./middleware/errorHandler');"},
            {"path": "src/middleware/errorHandler.js", "line": 1, "line_text": "const errorHandler = (err, req, res, next) => {"},
        ], []

    inv = DependencyImpactInvestigator(
        list_files_tool=mock_list_files,
        read_file_tool=mock_read_file,
        search_code_tool=mock_search_code,
    )
    task = {
        "task_id": "TASK-DEP-001",
        "instructions": "Trace authentication middleware and callers",
        "scope": ["src/server.js", "src/middleware/authenticate.js"],
    }
    result = inv.investigate(
        workspace_path="/dummy",
        investigation_task=task,
        shared_context={"problem": "Login succeeds, but opening profile page returns 401 Unauthorized."},
    )

    # Must NOT produce false dependency discrepancy hypotheses for errorHandler.js or server.js
    for h in result["hypotheses"]:
        assert "errorHandler" not in h["possible_cause"]
        assert "server.js" not in h["possible_cause"]
    assert len(result["hypotheses"]) == 0


def test_runtime_error_investigator_http_401_reports_missing_traces():
    """Verify runtime investigator accurately flags missing server logs when only HTTP 401 is available."""
    inv = RuntimeErrorInvestigator(
        list_files_tool=lambda w: (["src/server.js"], []),
        read_file_tool=lambda w, p: ("", None),
    )
    task = {
        "task_id": "TASK-RUNTIME-001",
        "instructions": "Inspect HTTP 401 error condition and execution failures",
    }
    result = inv.investigate(
        workspace_path="/dummy",
        investigation_task=task,
        shared_context={"problem": "Login succeeds, but opening the profile page returns 401 Unauthorized."},
    )

    # Must accurately report missing runtime server logs
    assert any("server-side exception" in m or "No runtime logs" in m for m in result["missing_evidence"])
    # Must capture confirmed HTTP 401 finding
    assert any("401" in f["statement"] for f in result["findings"])


def test_orchestrator_multi_bug_generalization():
    """Verify orchestrator dynamically plans distinct tasks for distinct bug domains."""
    planner = InvestigationPlanner()

    # Case A: Authorization / Access control failure (403 Forbidden)
    plan_auth_403 = planner.plan(
        problem="Access to sensitive resource returns 403 Forbidden after permission check.",
        repository_summary={},
        gathered_context={"relevant_files": ["src/routes/admin.js", "src/models/role.js"]},
        current_state={},
    )
    agents_auth_403 = {t["assigned_agent"] for t in plan_auth_403}
    assert "dependency_impact_investigator" in agents_auth_403
    assert "runtime_error_investigator" in agents_auth_403

    # Case A2: Authentication failure (401 Unauthorized)
    plan_auth = planner.plan(
        problem="Login succeeds, but opening profile page returns 401 Unauthorized.",
        repository_summary={},
        gathered_context={"relevant_files": ["src/routes/auth.js", "src/models/user.js"]},
        current_state={},
    )
    agents_auth = {t["assigned_agent"] for t in plan_auth}
    assert "dependency_impact_investigator" in agents_auth
    assert "runtime_error_investigator" in agents_auth

    # Case B: Calculation logic bug (generic tax calculation without discount keyword)
    plan_tax = planner.plan(
        problem="Tax calculation typo applies fixed rate instead of progressive bracket formula",
        repository_summary={},
        gathered_context={"relevant_files": ["src/finance/tax.py"]},
        current_state={},
    )
    assert len(plan_tax) == 1
    assert plan_tax[0]["assigned_agent"] == "code_investigator"
    assert "arithmetic" in plan_tax[0]["objective"].lower() or "calculation" in plan_tax[0]["instructions"].lower()

    # Case B2: Calculation logic bug (discount formula)
    plan_calc = planner.plan(
        problem="Discount calculation typo applies 10% instead of discount_rate formula",
        repository_summary={},
        gathered_context={"relevant_files": ["src/billing/calc.py"]},
        current_state={},
    )
    assert len(plan_calc) == 1
    assert plan_calc[0]["assigned_agent"] == "code_investigator"
    assert "arithmetic" in plan_calc[0]["objective"].lower() or "calculation" in plan_calc[0]["instructions"].lower()

    # Case C: Runtime exception crash
    plan_crash = planner.plan(
        problem="Application crashes with TypeError: Cannot read property id of undefined",
        repository_summary={},
        gathered_context={"relevant_files": ["src/service.py"]},
        current_state={"stack_trace": 'File "src/service.py", line 42, in get_user\n    return user.id'},
    )
    agents_crash = {t["assigned_agent"] for t in plan_crash}
    assert "runtime_error_investigator" in agents_crash


def test_reviewer_evaluates_usefulness_without_rejecting_unverified_candidate():
    """Verify Reviewer assesses hypothesis usefulness and does not dismiss useful hypotheses when unconfirmed."""
    reviewer = Reviewer()
    problem = "Payment service fails when charging cards with currency conversions."
    evidence = [
        {
            "evidence_id": "EV-001",
            "source": "backend/payment_service.py",
            "line": 45,
            "snippet": "amount_converted = amount * get_rate(currency)",
            "is_defect_evidence": True,
        }
    ]
    hypotheses = [
        {
            "hypothesis_id": "HYP-001",
            "statement": "Missing currency conversion scale factor leads to zero-amount charge",
            "supporting_evidence_ids": ["EV-001"],
            "status": "unverified",
        }
    ]
    result = reviewer.review(
        problem=problem,
        evidence=evidence,
        findings=[],
        hypotheses=hypotheses,
        iteration=1,
        max_iterations=2,
    )
    # Hypothesis is relevant and useful even though exact runtime verification is pending
    assert len(result["hypothesis_evaluations"]) == 1
    eval_item = result["hypothesis_evaluations"][0]
    assert eval_item["is_useful"] is True
    assert "useful" in eval_item["usefulness_reason"].lower()
    assert len(result["useful_hypotheses"]) == 1
    assert result["most_likely_hypothesis"]["hypothesis_id"] == "HYP-001"


def test_deduplication_and_explanation_of_findings(tmp_path):
    """Verify finding aggregation deduplicates identical statement/location entries across passes."""
    orchestrator = InvestigationOrchestrator()
    orchestrator.code_agent = lambda *a, **kw: {
        "status": "completed",
        "findings": [
            {
                "statement": "Parameter mismatch in taskModel.js at line 13",
                "file_path": "backend/taskModel.js",
                "line_number": 13,
            },
            {
                "statement": "Unique finding in userModel.js at line 5",
                "file_path": "backend/userModel.js",
                "line_number": 5,
            },
        ],
        "evidence": [],
        "hypotheses": [],
    }
    curr_state = {
        "problem": "Parameter ordering defect in taskModel.js causes parameter mismatch",
        "workspace_path": str(tmp_path),
        "gathered_context": {"relevant_files": ["backend/taskModel.js"]},
        "aggregated_findings": [
            {
                "statement": "Parameter mismatch in taskModel.js at line 13",
                "file_path": "backend/taskModel.js",
                "line_number": 13,
                "supports_or_contradicts": "supports candidate root cause HYP-001",
            }
        ],
        "completed_tasks": [],
    }
    plan = [
        {
            "task_id": "TASK-001",
            "assigned_agent": "code_investigator",
            "objective": "Inspect parameter ordering in taskModel",
            "reason": "Verify column order mismatch",
            "instructions": "Inspect parameter ordering in taskModel",
            "scope": ["backend/taskModel.js"],
            "input": {"problem": "Sample issue"},
            "expected_output": "Evidence of parameter ordering",
            "completion_criteria": "Code findings produced",
        }
    ]
    res = orchestrator.execute_plan_single_pass(plan=plan, state=curr_state)
    findings = res["aggregated_findings"]
    # Duplicate from task_results must be deduplicated
    assert len(findings) == 2
    statements = [f["statement"] for f in findings]
    assert statements.count("Parameter mismatch in taskModel.js at line 13") == 1
    assert "Unique finding in userModel.js at line 5" in statements


def test_inconclusive_diagnosis_provides_candidate_cause_and_actionable_debugging_steps():
    """Verify inconclusive diagnosis provides likely candidate root cause and prioritized debugging steps."""
    state = {
        "problem": "Occasional crash during export processing",
        "evidence_pool": [
            {
                "evidence_id": "EV-001",
                "source": "backend/export.py",
                "line": 88,
                "snippet": "with open(export_path, 'w') as f:",
                "is_defect_evidence": False,
            }
        ],
        "aggregated_hypotheses": [
            {
                "hypothesis_id": "HYP-001",
                "statement": "File handle exhaustion or unhandled IOException on concurrent export jobs",
                "supporting_evidence_ids": ["EV-001"],
                "status": "unverified",
            }
        ],
        "review_result": {
            "decision": "inconclusive",
            "is_sufficient": False,
            "hypothesis_evaluations": [
                {
                    "hypothesis_id": "HYP-001",
                    "statement": "File handle exhaustion or unhandled IOException on concurrent export jobs",
                    "is_relevant_to_problem": True,
                    "status": "unverified",
                }
            ],
            "unanswered_questions": ["What is the concurrent file descriptor limit?"],
            "missing_evidence": ["OS file descriptor limit configuration and reproduction trace"],
        },
        "investigation_iterations": 3,
        "max_iterations": 3,
    }
    result = inconclusive_result_node(state)
    assert result["is_conclusive"] is False
    assert result["status_label"] == "Inconclusive"
    # Should identify the candidate cause
    assert "unverified" in result["likely_root_cause"].lower() or "inconclusive" in result["likely_root_cause"].lower()
    # Diagnosis contains prioritized files to inspect and actionable debugging steps
    diag = result["diagnosis"]
    assert "prioritized_files_to_inspect" in diag
    assert "backend/export.py" in diag["prioritized_files_to_inspect"]
    assert len(diag["actionable_debugging_steps"]) >= 2

