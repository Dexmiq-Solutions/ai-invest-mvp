"""Phase 5 Tests: Specialized Investigators.

Tests Code Investigator, Dependency/Impact Investigator, and Runtime/Error Investigator
for common interface, structured outputs, evidence grounding, and error handling.
"""

from pathlib import Path
import pytest

from app.agents.investigator_contract import (
    InvestigatorInput,
    InvestigatorResult,
    Finding,
    EvidenceRecord,
    resolve_workspace,
    safe_search,
    safe_read,
)
from app.agents.code_investigator import (
    CodeInvestigator,
    run_code_investigator,
)
from app.agents.dependency_impact_investigator import (
    DependencyImpactInvestigator,
    run_dependency_impact_investigator,
)
from app.agents.runtime_error_investigator import (
    RuntimeErrorInvestigator,
    run_runtime_error_investigator,
)
from app.tools.investigation_tools import list_files, search_code, read_file


@pytest.fixture
def sample_project(tmp_path: Path) -> Path:
    """Create a multi-file sample workspace for investigator testing."""
    root = tmp_path / "sample_app"
    root.mkdir(parents=True)

    # Directories
    (root / "api").mkdir()
    (root / "services").mkdir()
    (root / "models").mkdir()

    # Files
    (root / "api" / "auth.py").write_text(
        "from services.user_service import UserService\n\n"
        "def login_handler(request):\n"
        "    email = request.get('email')\n"
        "    if not email:\n"
        "        return {'status': 400, 'error': 'Email required'}\n"
        "    user = UserService().find_by_email(email)\n"
        "    if not user:\n"
        "        return {'status': 401, 'error': 'Invalid credentials'}\n"
        "    return {'status': 200, 'token': 'jwt_token_123'}\n",
        encoding="utf-8",
    )

    (root / "services" / "user_service.py").write_text(
        "from models.user import User\n\n"
        "class UserService:\n"
        "    def find_by_email(self, email):\n"
        "        # Potential bug: case-sensitive comparison\n"
        "        return User.get_by_email(email)\n",
        encoding="utf-8",
    )

    (root / "models" / "user.py").write_text(
        "class User:\n"
        "    users = {'alice@example.com': {'id': 1, 'name': 'Alice'}}\n\n"
        "    @classmethod\n"
        "    def get_by_email(cls, email):\n"
        "        return cls.users.get(email)\n",
        encoding="utf-8",
    )

    return root


# ============================================================
# 1. Investigator Contract & Common Interface Tests
# ============================================================

def test_investigator_contract_models():
    """Verify standard Pydantic models for inputs and outputs."""
    inp = InvestigatorInput(
        workspace_path="/path/to/ws",
        investigation_task="Inspect login failure",
        shared_context={"problem": "401 error"},
    )
    assert inp.workspace_path == "/path/to/ws"
    assert inp.investigation_task == "Inspect login failure"
    assert inp.shared_context["problem"] == "401 error"

    finding = Finding(
        category="Possible Cause",
        description="Case sensitivity issue in email comparison",
        file_path="services/user_service.py",
        line_number=6,
        snippet="return User.get_by_email(email)",
    )
    assert finding.category == "Possible Cause"
    assert finding.file_path == "services/user_service.py"

    evidence = EvidenceRecord(
        source="services/user_service.py",
        line=6,
        matched_text="return User.get_by_email(email)",
        kind="code_match",
        verification_status="unverified",
    )
    assert evidence.verification_status == "unverified"

    res = InvestigatorResult(
        investigator_name="code_investigator",
        findings=[finding],
        evidence=[evidence],
        status="completed",
        summary="Found potential case mismatch",
    )
    assert res.status == "completed"
    assert len(res.findings) == 1
    assert len(res.evidence) == 1


def test_resolve_workspace_helper():
    """Verify workspace resolution from state or context."""
    state = {"workspace_path": "/workspace/dir"}
    context = {"workspace": "/context/dir"}

    assert resolve_workspace(state, None) == "/workspace/dir"
    assert resolve_workspace(None, context) == "/context/dir"
    assert resolve_workspace(None, None) == ""


# ============================================================
# 2. Code Investigator Tests
# ============================================================

def test_code_investigator_finds_relevant_logic(sample_project: Path):
    """Code investigator should search and identify relevant functions."""
    investigator = CodeInvestigator(
        list_files_tool=list_files,
        search_code_tool=search_code,
        read_file_tool=read_file,
    )

    result = investigator.investigate(
        workspace_path=str(sample_project),
        investigation_task="Investigate login authentication failure and user lookup",
        shared_context={
            "relevant_files": ["api/auth.py", "services/user_service.py"],
        },
    )

    assert "code" in result["investigator_name"].lower()
    assert result["status"] in ("completed", "partial")
    assert len(result["evidence"]) > 0

    sources = [e.get("source") for e in result["evidence"]]
    assert any("auth.py" in s for s in sources if s)


def test_code_investigator_handles_absent_optional_context(sample_project: Path):
    """Code investigator should succeed even with empty shared_context."""
    investigator = CodeInvestigator(
        list_files_tool=list_files,
        search_code_tool=search_code,
        read_file_tool=read_file,
    )

    result = investigator.investigate(
        workspace_path=str(sample_project),
        investigation_task="Check user login",
        shared_context=None,
    )

    assert "code" in result["investigator_name"].lower()
    assert result["status"] in ("completed", "partial")


def test_code_investigator_run_entry_point(sample_project: Path):
    """Verify backward-compatible run_code_investigator entry point."""
    result = run_code_investigator(
        investigation_task="Check user login error handling",
        relevant_context={"workspace_path": str(sample_project)},
        current_state={"workspace_path": str(sample_project), "problem": "Login issue"},
    )

    assert isinstance(result, dict)
    assert "code" in result["investigator_name"].lower()
    assert "findings" in result
    assert "evidence" in result


# ============================================================
# 3. Dependency / Impact Investigator Tests
# ============================================================

def test_dependency_impact_investigator_traces_imports(sample_project: Path):
    """Dependency investigator should discover import relationships across modules."""
    investigator = DependencyImpactInvestigator(
        list_files_tool=list_files,
        search_code_tool=search_code,
        read_file_tool=read_file,
    )

    result = investigator.investigate(
        workspace_path=str(sample_project),
        investigation_task="Trace dependencies of login handler and user service",
        shared_context={
            "relevant_files": ["api/auth.py", "services/user_service.py", "models/user.py"],
        },
    )

    assert "dependency" in result["investigator_name"].lower()
    assert result["status"] in ("completed", "partial")

    # Should discover relationships/evidence between modules
    assert len(result["evidence"]) > 0
    sources = [e.get("source") for e in result["evidence"]]
    assert any("auth.py" in s or "user_service.py" in s for s in sources if s)


def test_dependency_impact_investigator_run_entry_point(sample_project: Path):
    """Verify run_dependency_impact_investigator entry point."""
    result = run_dependency_impact_investigator(
        investigation_task="Trace callers of user lookup",
        relevant_context={"workspace_path": str(sample_project)},
        current_state={"workspace_path": str(sample_project)},
    )

    assert isinstance(result, dict)
    assert "dependency" in result["investigator_name"].lower()


# ============================================================
# 4. Runtime / Error Investigator Tests
# ============================================================

def test_runtime_error_investigator_with_status_code(sample_project: Path):
    """Runtime investigator parses HTTP status codes and maps to code sites."""
    investigator = RuntimeErrorInvestigator(
        list_files_tool=list_files,
        read_file_tool=read_file,
    )

    result = investigator.investigate(
        workspace_path=str(sample_project),
        investigation_task="The request fails with 401 Unauthorized",
        shared_context={
            "errors": ["HTTP 401 Unauthorized"],
            "relevant_files": ["api/auth.py"],
        },
    )

    assert "runtime" in result["investigator_name"].lower()
    assert result["status"] in ("completed", "partial")

    # Should identify 401
    assert 401 in result.get("http_status_codes", [])


def test_runtime_error_investigator_with_stack_trace(sample_project: Path):
    """Runtime investigator parses stack trace frames and reads source lines."""
    stack = (
        'File "api/auth.py", line 6, in login_handler\n'
        '    user = UserService().find_by_email(email)\n'
        'File "services/user_service.py", line 5, in find_by_email\n'
        '    return User.get_by_email(email)\n'
    )

    investigator = RuntimeErrorInvestigator(
        list_files_tool=list_files,
        read_file_tool=read_file,
    )

    result = investigator.investigate(
        workspace_path=str(sample_project),
        investigation_task="Application error during login execution",
        shared_context={
            "stack_trace": stack,
        },
    )

    assert "runtime" in result["investigator_name"].lower()
    assert len(result.get("stack_frames", [])) >= 2
    frame_files = [f.get("path") or f.get("file") for f in result.get("stack_frames", [])]
    assert any("auth.py" in f for f in frame_files if f)


def test_runtime_error_investigator_run_entry_point(sample_project: Path):
    """Verify run_runtime_error_investigator entry point."""
    result = run_runtime_error_investigator(
        investigation_task="Check 401 status handling",
        relevant_context={"workspace_path": str(sample_project), "error": "401"},
        current_state={"workspace_path": str(sample_project)},
    )

    assert isinstance(result, dict)
    assert "runtime" in result["investigator_name"].lower()


# ============================================================
# 5. Error Handling and Path Safety across Investigators
# ============================================================

def test_investigators_handle_missing_workspace(tmp_path: Path):
    """All investigators fail gracefully when workspace does not exist."""
    missing_ws = str(tmp_path / "nonexistent_dir")

    for runner in (
        run_code_investigator,
        run_dependency_impact_investigator,
        run_runtime_error_investigator,
    ):
        result = runner(
            investigation_task="Task",
            relevant_context={"workspace_path": missing_ws},
            current_state={"workspace_path": missing_ws},
        )
        assert result["status"] in ("failed", "partial")
        assert len(result.get("tool_errors", [])) > 0 or result.get("findings")
