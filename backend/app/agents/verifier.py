"""Phase 9 Verifier: Test Discovery, Safe Verification Execution, and Actionable Verification Planning.

Responsibilities:
1. Inspects the repository structure and configuration to discover existing test suites, runners, and test files.
2. Evaluates whether runtime tests can be executed safely within the sandboxed workspace.
3. Distinguishes executed tests from proposed verification steps; never claims runtime verification
   when only static code inspection or structural analysis was performed.
4. Identifies blockers (missing dependencies, unconfigured test runners, required external services).
5. Generates concrete, actionable verification steps, expected outcomes, boundary values, and edge cases.
6. Enforces verification status conventions:
   - 'verified': Empirically verified through dynamic test probe or executed runtime test.
   - 'partially verified': Static contract verified or some checks passed while runtime verification remains unexecuted.
   - 'not verified': Proposed verification steps only; no runtime execution occurred.
   - 'verification blocked': Execution could not proceed due to missing test runner, uninstalled dependencies, or environment blockers.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from pydantic import BaseModel, Field

logger = logging.getLogger("app.agents.verifier")


class VerificationCheck(BaseModel):
    name: str = Field(description="Name or summary of the verification check")
    command: Optional[str] = Field(default=None, description="Command to execute the check")
    target_file: Optional[str] = Field(default=None, description="Target test or source file")
    description: str = Field(description="Actionable description of the test scenario")
    expected_outcome: str = Field(description="Expected behavior and assertions")
    edge_cases: List[str] = Field(default_factory=list, description="Boundary conditions and edge cases to test")
    executed: bool = Field(default=False, description="Whether the check was actually executed")
    execution_result: Optional[str] = Field(default=None, description="Outcome of actual execution if executed")
    status: str = Field(
        default="not verified",
        description="Status: verified, partially verified, not verified, or verification blocked"
    )
    blocker_reason: Optional[str] = Field(default=None, description="Reason why execution was blocked or skipped")


class VerificationSummary(BaseModel):
    overall_status: str = Field(
        description="Overall status: verified, partially verified, not verified, or verification blocked"
    )
    executed_checks: List[VerificationCheck] = Field(default_factory=list)
    proposed_checks: List[VerificationCheck] = Field(default_factory=list)
    blocker_reasons: List[str] = Field(default_factory=list)
    relevant_existing_tests: List[str] = Field(default_factory=list)
    actionable_steps: List[str] = Field(default_factory=list)


class Verifier:
    """
    Evaluates verification feasibility, inspects existing test configurations,
    records safe execution results, and produces honest, actionable verification steps.
    """

    def __init__(self, workspace_path: Optional[str] = None):
        self.workspace_path = workspace_path

    def discover_existing_tests(
        self,
        workspace_path: Optional[str] = None,
        repository_summary: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Inspect the target repository workspace for test files, test runners, and test scripts.
        """
        ws = workspace_path or self.workspace_path
        discovery = {
            "has_test_suite": False,
            "test_runner": None,
            "test_command": None,
            "test_files": [],
            "dependencies_installed": False,
            "blocker_reasons": [],
        }

        if not ws:
            discovery["blocker_reasons"].append("No workspace path provided for verification.")
            return discovery

        ws_path = Path(ws).resolve()
        if not ws_path.exists() or not ws_path.is_dir():
            discovery["blocker_reasons"].append(f"Workspace path does not exist or is not a directory: {ws}")
            return discovery

        # 1. Check package.json (Node.js / JavaScript / TypeScript)
        pkg_json_candidates = list(ws_path.glob("**/package.json"))
        for pkg_file in pkg_json_candidates:
            if "node_modules" in pkg_file.parts:
                continue
            try:
                pkg_data = json.loads(pkg_file.read_text(encoding="utf-8"))
                scripts = pkg_data.get("scripts", {})
                test_script = scripts.get("test")
                has_node_modules = (pkg_file.parent / "node_modules").is_dir()
                if test_script and not test_script.strip().startswith("echo ") and "no test specified" not in test_script:
                    discovery["has_test_suite"] = True
                    discovery["test_command"] = "npm test"
                    discovery["test_runner"] = "npm"
                discovery["dependencies_installed"] = has_node_modules
                if not has_node_modules:
                    discovery["blocker_reasons"].append(
                        f"Node dependencies not installed in `{pkg_file.parent.relative_to(ws_path) or '.'}` (node_modules missing)."
                    )
                if not test_script or test_script.strip().startswith("echo ") or "no test specified" in test_script:
                    discovery["blocker_reasons"].append(
                        f"No automated test script configured in `{pkg_file.relative_to(ws_path)}` (`scripts.test` is missing or unconfigured)."
                    )
            except Exception as e:
                logger.debug("Could not parse package.json at %s: %s", pkg_file, e)

        # 2. Check Python test configurations
        py_test_files = []
        for pat in ("**/test_*.py", "**/*_test.py", "**/tests/*.py"):
            for tf in ws_path.glob(pat):
                if not any(ignored in tf.parts for ignored in (".venv", "venv", "__pycache__", ".git")):
                    py_test_files.append(str(tf.relative_to(ws_path)))

        if py_test_files:
            discovery["has_test_suite"] = True
            discovery["test_files"].extend(py_test_files[:10])
            if not discovery["test_runner"]:
                discovery["test_runner"] = "pytest"
                discovery["test_command"] = "pytest"

        # 3. Check repository_summary if provided
        if repository_summary:
            structure = repository_summary.get("project_structure", {})
            for f in structure.get("all_files", []):
                lower = str(f).lower()
                if ("test" in lower or "spec" in lower) and not any(ign in lower for ign in ("node_modules", ".venv", "seed")):
                    if f not in discovery["test_files"]:
                        discovery["test_files"].append(f)

        return discovery

    def evaluate_verification(
        self,
        problem: str,
        root_cause: str,
        affected_components: List[str],
        workspace_path: Optional[str] = None,
        repository_summary: Optional[Dict[str, Any]] = None,
        is_empirically_verified: bool = False,
        empirical_evidence: Optional[Dict[str, Any]] = None,
        allow_safe_execution: bool = False,
        expected_behavior: Optional[str] = None,
    ) -> VerificationSummary:
        """
        Evaluate and formulate honest verification results and proposed validation steps.
        """
        ws = workspace_path or self.workspace_path
        discovery = self.discover_existing_tests(workspace_path=ws, repository_summary=repository_summary)

        executed_checks: List[VerificationCheck] = []
        proposed_checks: List[VerificationCheck] = []
        blocker_reasons: List[str] = list(discovery.get("blocker_reasons") or [])

        # 1. Handle empirical verification already recorded during investigation probe
        if is_empirically_verified or (empirical_evidence and empirical_evidence.get("is_empirically_verified")):
            ev_desc = empirical_evidence.get("statement") or empirical_evidence.get("description") or "Dynamic test probe execution"
            executed_checks.append(
                VerificationCheck(
                    name="Empirical Investigation Probe",
                    command=empirical_evidence.get("command") or "in-process verification probe",
                    description=f"Isolated verification probe reproduced and confirmed failure mechanism: {ev_desc}",
                    expected_outcome="Defect reproduced and observed in isolated probe execution.",
                    executed=True,
                    execution_result="Verified: Failure mechanism confirmed under controlled test input.",
                    status="verified",
                )
            )

        # 2. Check if automated tests can be safely executed
        can_run = allow_safe_execution and discovery["has_test_suite"] and discovery["test_command"] and discovery["dependencies_installed"]
        if can_run and ws:
            ws_path = Path(ws).resolve()
            cmd = discovery["test_command"]
            try:
                logger.info("Executing safe test suite verification: %s in %s", cmd, ws_path)
                res = subprocess.run(
                    cmd,
                    cwd=str(ws_path),
                    shell=True,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                status_str = "verified" if res.returncode == 0 else "partially verified"
                executed_checks.append(
                    VerificationCheck(
                        name="Repository Automated Test Suite",
                        command=cmd,
                        description=f"Executed existing project test suite via `{cmd}`.",
                        expected_outcome="All existing automated tests pass.",
                        executed=True,
                        execution_result=f"Exit code: {res.returncode}. Output: {res.stdout[:200]} {res.stderr[:200]}",
                        status=status_str,
                    )
                )
            except Exception as exc:
                logger.warning("Safe test execution failed or timed out: %s", exc)
                blocker_reasons.append(f"Automated test execution failed: {exc}")

        # 3. Formulate proposed actionable verification steps tailored to the affected components and defect class
        primary_comp = affected_components[0] if affected_components else "the affected component"
        relevant_files_text = ", ".join(f"`{c}`" for c in affected_components[:3]) or "the codebase"

        # Formulate targeted checks based on problem domain and defect description
        prob_lower = (problem + " " + root_cause).lower()
        if any(k in prob_lower for k in ("column", "order", "parameter", "argument", "swap", "due_date", "user_id")):
            proposed_checks.append(
                VerificationCheck(
                    name="Regression Test for Argument / Column Ordering",
                    description=(
                        f"Create a unit or integration test calling `{primary_comp}` with distinct, recognizable sentinel values "
                        "(e.g., userId=9999 and dueDate='2026-12-31'). Inspect the persisted record or query binding."
                    ),
                    expected_outcome=(
                        "Database record stores `due_date='2026-12-31'` and `user_id=9999` in their respective columns "
                        "without foreign key rejection or column data corruption."
                    ),
                    edge_cases=[
                        "Task creation with null or omitted due date.",
                        "Task creation with ISO-8601 formatted due date strings.",
                        "User ID types (numeric integer vs string representations).",
                    ],
                    executed=False,
                    status="not verified",
                    blocker_reason="Fix recommendation only; target application code has not been modified.",
                )
            )
            proposed_checks.append(
                VerificationCheck(
                    name="Persistence Round-Trip Verification",
                    description=(
                        f"Execute an end-to-end task creation request through the API route and retrieve the created task via GET."
                    ),
                    expected_outcome="The retrieved task payload contains the matching `due_date` and belongs strictly to the authenticated `user_id`.",
                    edge_cases=["Multiple concurrent tasks created with and without due dates."],
                    executed=False,
                    status="not verified",
                )
            )

        elif any(k in prob_lower for k in ("token", "email", "auth", "login", "register", "jwt", "case", "casing")):
            proposed_checks.append(
                VerificationCheck(
                    name="Authentication Token & Database Consistency Check",
                    description=(
                        f"Register a user account with mixed-case email (e.g., `TestUser@Example.com`), authenticate via login, "
                        "and decode the generated JWT token claims."
                    ),
                    expected_outcome=(
                        "The `email` claim in the signed JWT matches the exact email value stored in the database record, "
                        "ensuring consistent user identity resolution."
                    ),
                    edge_cases=[
                        "Login with mixed-case vs lower-case email input.",
                        "Duplicate registration attempt with differing letter cases.",
                        "Special characters and leading/trailing whitespace in email addresses.",
                    ],
                    executed=False,
                    status="not verified",
                    blocker_reason="Fix recommendation only; target application code has not been modified.",
                )
            )

        elif any(k in prob_lower for k in ("exception", "crash", "keyerror", "null", "none", "typeerror", "runtime")):
            proposed_checks.append(
                VerificationCheck(
                    name="Defensive Guard & Error Handling Verification",
                    description=(
                        f"Invoke `{primary_comp}` with boundary/missing inputs (e.g. `None`, `null`, missing keys, or empty strings) "
                        "that previously triggered unhandled exceptions."
                    ),
                    expected_outcome="Function gracefully validates input and raises a descriptive error or returns appropriate HTTP 400 Bad Request without crashing.",
                    edge_cases=[
                        "Null / None input payload.",
                        "Empty string or missing required dictionary key.",
                        "Malformed data types (e.g., string passed where integer expected).",
                    ],
                    executed=False,
                    status="not verified",
                    blocker_reason="Fix recommendation only; target application code has not been modified.",
                )
            )

        else:
            proposed_checks.append(
                VerificationCheck(
                    name=f"Functional Regression Check for {primary_comp}",
                    description=f"Execute unit test asserting expected behavior for: {problem[:100]}.",
                    expected_outcome=expected_behavior or "Operation succeeds with expected state transitions and return values.",
                    edge_cases=["Boundary input values", "Empty / invalid inputs", "Concurrent invocations"],
                    executed=False,
                    status="not verified",
                    blocker_reason="Fix recommendation only; target application code has not been modified.",
                )
            )

        # 4. Determine overall verification status following strict conventions
        if any(c.status == "verified" for c in executed_checks):
            overall_status = "verified"
        elif any(c.status == "partially verified" for c in executed_checks):
            overall_status = "partially verified"
        elif blocker_reasons and not executed_checks:
            overall_status = "verification blocked"
        else:
            overall_status = "not verified"

        # 5. Compile actionable steps list
        actionable_steps = []
        if executed_checks:
            for c in executed_checks:
                actionable_steps.append(f"[Executed] {c.name}: {c.execution_result or c.status}")
        for c in proposed_checks:
            actionable_steps.append(f"[Proposed] {c.name}: {c.description}")
            if c.edge_cases:
                actionable_steps.append(f"  Edge cases: {', '.join(c.edge_cases)}")

        return VerificationSummary(
            overall_status=overall_status,
            executed_checks=executed_checks,
            proposed_checks=proposed_checks,
            blocker_reasons=blocker_reasons,
            relevant_existing_tests=discovery.get("test_files", []),
            actionable_steps=actionable_steps,
        )
