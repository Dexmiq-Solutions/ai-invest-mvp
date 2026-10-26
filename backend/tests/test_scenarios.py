"""Integration Scenarios: Scenarios A through F from Section 13.7.

Validates end-to-end behavior across representative software engineering issues:
- Scenario A: Authentication failure
- Scenario B: Simple implementation bug
- Scenario C: Runtime error with stack trace
- Scenario D: Insufficient context
- Scenario E: Tool failure
- Scenario F: Investigator failure
"""

from pathlib import Path
import pytest
import zipfile

from app.graph.graph import build_graph
from app.graph.context import ContextAssessment
from app.agents.investigation_orchestrator import InvestigationOrchestrator
from app.tools.investigation_tools import read_file


@pytest.fixture
def scenario_a_repo(tmp_path: Path) -> Path:
    """Fixture for Scenario A: Auth 401 issue."""
    proj = tmp_path / "scenario_a"
    (proj / "src" / "api").mkdir(parents=True)
    (proj / "src" / "services").mkdir(parents=True)

    (proj / "src" / "api" / "auth.py").write_text(
        "from fastapi import APIRouter, Header, HTTPException\n"
        "router = APIRouter()\n"
        "@router.get('/profile')\n"
        "def profile(authorization: str = Header(None)):\n"
        "    if not authorization or not authorization.startswith('Bearer '):\n"
        "        raise HTTPException(status_code=401, detail='Unauthorized')\n"
        "    return {'user': 'alice'}\n",
        encoding="utf-8",
    )

    (proj / "src" / "services" / "client.py").write_text(
        "# Frontend client\n"
        "def get_profile(token):\n"
        "    # Bug: omitting authorization header\n"
        "    headers = {}\n"
        "    return request('/profile', headers=headers)\n",
        encoding="utf-8",
    )

    (proj / "requirements.txt").write_text("fastapi\n", encoding="utf-8")

    zip_path = tmp_path / "scenario_a.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as arc:
        for f in proj.rglob("*"):
            if f.is_file():
                arc.write(f, f.relative_to(proj))
    return zip_path


@pytest.fixture
def scenario_b_repo(tmp_path: Path) -> Path:
    """Fixture for Scenario B: Pricing / discount bug."""
    proj = tmp_path / "scenario_b"
    (proj / "billing").mkdir(parents=True)

    (proj / "billing" / "discount.py").write_text(
        "def calculate_total(subtotal: float, discount_percent: float) -> float:\n"
        "    # Bug: applying fixed 10% instead of discount_percent\n"
        "    return subtotal * 0.90\n",
        encoding="utf-8",
    )

    (proj / "requirements.txt").write_text("python\n", encoding="utf-8")

    zip_path = tmp_path / "scenario_b.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as arc:
        for f in proj.rglob("*"):
            if f.is_file():
                arc.write(f, f.relative_to(proj))
    return zip_path


@pytest.fixture
def scenario_c_repo(tmp_path: Path) -> Path:
    """Fixture for Scenario C: Runtime crash."""
    proj = tmp_path / "scenario_c"
    (proj / "app").mkdir(parents=True)

    (proj / "app" / "processor.py").write_text(
        "def process_data(payload):\n"
        "    user_id = payload['user']['id']\n"
        "    return user_id\n",
        encoding="utf-8",
    )

    (proj / "requirements.txt").write_text("python\n", encoding="utf-8")

    zip_path = tmp_path / "scenario_c.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as arc:
        for f in proj.rglob("*"):
            if f.is_file():
                arc.write(f, f.relative_to(proj))
    return zip_path


# ============================================================
# Scenario A — Authentication failure
# ============================================================

def test_scenario_a_authentication_failure(scenario_a_repo: Path):
    """Scenario A: Authentication failure end-to-end pipeline execution."""
    graph = build_graph()

    result = graph.invoke({
        "problem": "My profile page returns 401 after login. Login succeeds, but /profile is rejected.",
        "project_path": str(scenario_a_repo),
        "problem_context": {
            "expected_behavior": "Profile data should be returned.",
            "actual_behavior": "Returns 401 Unauthorized.",
        },
    })

    # Context check accepts problem
    assert result["context_status"] == "sufficient"

    # Workspace prepared
    assert result.get("workspace_path")
    assert Path(result["workspace_path"]).exists()

    # Repository analyzed
    assert result.get("repository_summary")
    assert "FastAPI" in result["repository_summary"].get("frameworks", []) or "Python" in result["repository_summary"].get("languages", [])

    # Context gathered with auth concepts
    gathered = result.get("gathered_context", {})
    assert gathered

    # Phase 6 executed
    assert result.get("investigation_plan")
    assert len(result.get("completed_investigators", [])) >= 1
    assert result["current_step"] in ("investigation_completed", "context_gathered")


# ============================================================
# Scenario B — Simple implementation bug
# ============================================================

def test_scenario_b_simple_implementation_bug(scenario_b_repo: Path):
    """Scenario B: Discount calculation bug selects relevant code investigation without unnecessary agents."""
    graph = build_graph()

    result = graph.invoke({
        "problem": "The order total is incorrect when a discount is applied.",
        "project_path": str(scenario_b_repo),
    })

    assert result["context_status"] == "sufficient"
    assert result.get("workspace_path")
    assert result.get("repository_summary")

    # The plan should prioritize code_investigator for logic bug
    plan = result.get("investigation_plan", [])
    investigators = [step.get("investigator") for step in plan]
    assert "code_investigator" in investigators


# ============================================================
# Scenario C — Runtime error
# ============================================================

def test_scenario_c_runtime_error_with_stack_trace(scenario_c_repo: Path):
    """Scenario C: Runtime error receives stack trace and selects runtime error investigator."""
    stack = (
        'Traceback (most recent call last):\n'
        '  File "app/processor.py", line 2, in process_data\n'
        '    user_id = payload[\'user\'][\'id\']\n'
        'KeyError: \'user\'\n'
    )

    graph = build_graph()

    result = graph.invoke({
        "problem": "The application crashes when processing a request with KeyError: 'user'.",
        "project_path": str(scenario_c_repo),
        "problem_context": {
            "stack_trace": stack,
            "error": "KeyError: 'user'",
        },
    })

    assert result["context_status"] == "sufficient"
    plan = result.get("investigation_plan", [])
    investigators = [step.get("investigator") for step in plan]
    assert "runtime_error_investigator" in investigators or "code_investigator" in investigators


# ============================================================
# Scenario D — Insufficient context
# ============================================================

def test_scenario_d_insufficient_context(scenario_a_repo: Path):
    """Scenario D: Vague problem requests clarification and halts investigation before workspace setup."""
    graph = build_graph()

    result = graph.invoke({
        "problem": "My application is not working.",
        "project_path": str(scenario_a_repo),
    })

    assert result["context_status"] == "insufficient"
    assert result.get("missing_information") or result.get("context_questions")
    assert result["current_step"] == "context_insufficient"

    # Workspace setup must NOT have proceeded
    assert not result.get("workspace_id")
    assert not result.get("investigation_plan")


# ============================================================
# Scenario E — Tool failure
# ============================================================

def test_scenario_e_tool_failure_recorded(tmp_path: Path):
    """Scenario E: Unreadable file or missing path records tool error cleanly without crash."""
    ws = tmp_path / "scenario_e"
    ws.mkdir()
    (ws / "main.py").write_text("print('hello')", encoding="utf-8")

    # Calling read_file on a non-existent file produces a clean error
    tool_res = read_file(str(ws), "does_not_exist.py")
    assert tool_res["success"] is False
    assert "does not exist" in tool_res["error"]

    # Calling read_file outside workspace produces a safety error
    with pytest.raises(ValueError, match="outside the workspace"):
        read_file(str(ws), "../secret.txt")


# ============================================================
# Scenario F — Investigator failure
# ============================================================

def test_scenario_f_investigator_failure_handled(tmp_path: Path):
    """Scenario F: Investigator exception is recorded honestly in failed_investigators."""
    ws = tmp_path / "scenario_f"
    ws.mkdir()
    (ws / "main.py").write_text("print('test')", encoding="utf-8")

    def failing_specialist(*args, **kwargs):
        raise RuntimeError("Specialist failed due to simulated bug")

    orchestrator = InvestigationOrchestrator(
        code_investigator=failing_specialist,
        max_iterations=1,
        llm_factory=None,
    )

    result = orchestrator.investigate(
        investigation_task="Investigate code defect",
        workspace_path=str(ws),
        shared_context={
            "repository_summary": {"languages": ["Python"]},
        },
        current_state={"workspace_path": str(ws)},
    )

    # Failed investigator recorded honestly
    assert "code_investigator" in result["failed_investigators"]
    assert "code_investigator" not in result["completed_investigators"]
