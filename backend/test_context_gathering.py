from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.graph.context_gathering import context_gathering_node
from app.repository.analyzer import analyze_repository
from app.tools.investigation_tools import list_files, search_code, read_file


def _resolve_test_workspace() -> Path:
    ws_root = PROJECT_ROOT / "workspaces"
    if ws_root.exists():
        for candidate in ws_root.glob("**/taskflow"):
            if candidate.is_dir() and (candidate / "package.json").exists():
                return candidate
        for candidate in ws_root.glob("*/*"):
            if candidate.is_dir() and ((candidate / "package.json").exists() or (candidate / "requirements.txt").exists()):
                return candidate
    return (PROJECT_ROOT / "workspaces").resolve()


WORKSPACE_PATH = _resolve_test_workspace()


def test_context_gathering_real_workspace():
    repository_summary = analyze_repository(WORKSPACE_PATH)

    state = {
        "validated_problem_context": {
            "problem_description": "My profile page returns 401 after login.",
            "observed_behavior": "Profile API returns HTTP 401.",
            "expected_behavior": "Profile data should be returned after login.",
            "target_areas": ["profile", "authentication"],
        },
        "repository_summary": repository_summary,
        "workspace": WORKSPACE_PATH,
    }

    result = context_gathering_node(
        state,
        list_files_tool=list_files,
        search_code_tool=search_code,
        read_file_tool=read_file,
    )

    gathered_context = result["gathered_context"]

    print("\n===== CONTEXT GATHERING RESULT =====")
    print("\nCurrent Step:")
    print(result["current_step"])

    print("\nProblem Understanding:")
    print(gathered_context["problem_understanding"])

    print("\nInvestigation Concepts:")
    print(gathered_context["investigation_concepts"])

    print("\nRelevant Files:")
    for file in gathered_context["relevant_files"]:
        print(file)

    print("\nTechnical Terms:")
    print(gathered_context["technical_terms"])

    print("\nRelated Components:")
    print(gathered_context["related_components"])

    print("\nRelationships:")
    for relationship in gathered_context["relationships"]:
        print(relationship)

    print("\nEvidence Candidates:")
    for evidence in gathered_context["evidence_candidates"]:
        print(evidence)

    print("\nContext Summary:")
    print(gathered_context["context_summary"])

    assert result["current_step"] == "context_gathered"
    assert gathered_context
