import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


from app.graph.state import InvestigationState
from app.graph.context_gathering import context_gathering_node
from app.repository.analyzer import analyze_repository
from app.tools.investigation_tools import list_files, search_code, read_file


def test_context_gathering_real_workspace():

    workspace_path = PROJECT_ROOT / "workspaces" / "ae57faaa-2210-4e6d-8efa-6849068ff906"

    # Analyze the actual repository first
    repository_summary = analyze_repository(workspace_path)

    state: InvestigationState = {
        "validated_problem_context": {
            "problem_description": "My profile page returns 401 after login.",
            "observed_behavior": "Profile API returns HTTP 401.",
            "expected_behavior": "Profile data should be returned after login.",
            "target_areas": [
                "profile",
                "authentication",
            ],
        },

        # Use the actual repository analysis
        "repository_summary": repository_summary,

        "workspace_path": workspace_path,
    }

    result = context_gathering_node(
        {
            **state,
            "workspace": workspace_path,
        },
        list_files_tool=list_files,
        search_code_tool=search_code,
        read_file_tool=read_file,
    )

    print("\n========================================")
    print("CONTEXT GATHERING RESULT")
    print("========================================")

    gathered_context = result["gathered_context"]

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

    print("\nImplementation Context:")
    for item in gathered_context["implementation_context"]:
        print(item)

    print("\nEvidence Candidates:")
    for evidence in gathered_context["evidence_candidates"]:
        print(evidence)

    print("\nSearch History:")
    for search in gathered_context["search_history"]:
        print(search)

    print("\nContext Summary:")
    print(gathered_context["context_summary"])

    print("\nCurrent Step:")
    print(result["current_step"])

    assert result["current_step"] == "context_gathered"
    assert result["gathered_context"]
