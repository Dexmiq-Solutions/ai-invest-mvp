import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.graph.context_gathering import context_gathering_node
from app.repository.analyzer import analyze_repository
from app.tools.investigation_tools import list_files, search_code, read_file


# These fields belong to later investigation/evidence stages, not Phase 1
# Context Gathering. Keep this list aligned with the agreed phase contract.
PROHIBITED_CONTEXT_KEYS = {
    "evidence_candidates",
    "trace_flows",
    "producer_consumer_pairs",
    "write_read_pairs",
    "rejected_producer_candidates",
    "unresolved_questions",
    "evidence_gaps",
    "hypotheses",
    "findings",
    "root_cause",
    "diagnosis",
}


def find_keys_recursively(value, prohibited_keys, path="result"):
    """Return paths of prohibited keys found anywhere in a nested structure."""
    found = []

    if isinstance(value, dict):
        for key, nested_value in value.items():
            current_path = f"{path}.{key}"
            if key in prohibited_keys:
                found.append(current_path)
            found.extend(
                find_keys_recursively(nested_value, prohibited_keys, current_path)
            )
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            found.extend(
                find_keys_recursively(
                    item, prohibited_keys, f"{path}[{index}]"
                )
            )

    return found


def resolve_real_workspace():
    """
    Find an already extracted TaskFlow workspace.

    This test intentionally does not create a fake fixture: it should fail
    clearly if the real repository workspace is not available.
    """
    candidates = [
        PROJECT_ROOT / "workspaces",
        PROJECT_ROOT.parent / "workspaces",
    ]

    for root in candidates:
        if not root.exists():
            continue

        for candidate in root.glob("**/*"):
            if not candidate.is_dir():
                continue

            has_project_marker = any(
                (candidate / marker).exists()
                for marker in (
                    "package.json",
                    "requirements.txt",
                    "pyproject.toml",
                    "manage.py",
                )
            )
            if has_project_marker and (
                "taskflow" in candidate.name.lower()
                or any(part.lower() == "taskflow" for part in candidate.parts)
            ):
                return candidate

    pytest.fail(
        "Real TaskFlow workspace was not found. Run the investigation/workspace "
        "setup first, or update resolve_real_workspace() to point to the actual "
        "extracted TaskFlow directory. This test will not silently use a fixture."
    )


def test_context_gathering_real_taskflow_workspace():
    workspace_path = resolve_real_workspace()

    assert workspace_path.is_dir(), f"Workspace does not exist: {workspace_path}"

    # Analyze the actual extracted repository first.
    repository_summary = analyze_repository(str(workspace_path))
    assert isinstance(repository_summary, dict), (
        "analyze_repository() should return a dictionary"
    )

    # Match the field names used by the workflow's problem context.
    # If context_gathering_node expects different names, align this with its
    # actual implementation rather than silently changing production code.
    state = {
        "problem": (
            "The email in the login token does not match the email stored for "
            "the user. Investigate token generation, authentication, user "
            "records, and database access."
        ),
        "original_problem": (
            "The email in the login token does not match the email stored for "
            "the user. Investigate token generation, authentication, user "
            "records, and database access."
        ),
        "validated_problem_context": {
            "problem_summary": (
                "The email in the login token does not match the email stored "
                "for the user."
            ),
            "observed_behavior": (
                "The reported token email differs from the stored user email; "
                "runtime reproduction has not been verified."
            ),
            "expected_behavior": (
                "The token identity should correspond to the same user's "
                "stored record."
            ),
            "target_areas": ["authentication", "users", "database"],
        },
        "context_status": "sufficient",
        "repository_summary": repository_summary,
        "workspace_path": str(workspace_path),
    }

    result = context_gathering_node(
        {
            **state,
            "workspace": str(workspace_path),
        },
        list_files_tool=list_files,
        search_code_tool=search_code,
        read_file_tool=read_file,
    )

    print("\n========================================")
    print("CONTEXT GATHERING RESULT")
    print("========================================")
    print(f"Workspace: {workspace_path}")
    print(f"Current step: {result.get('current_step')}")
    print(f"Context status: {result.get('context_status')}")

    gathered_context = result.get("gathered_context")
    assert isinstance(gathered_context, dict), (
        "Result must contain a gathered_context dictionary"
    )

    for key in (
        "problem_understanding",
        "investigation_concepts",
        "relevant_files",
        "technical_terms",
        "related_components",
        "file_contents",
        "frameworks_detected",
    ):
        print(f"\n{key}:")
        print(gathered_context.get(key, "<not present>"))

    print("\nContext summary:")
    print(gathered_context.get("context_summary", "<not present>"))

    # Completion and completeness contract.
    assert result.get("current_step") == "context_gathered", (
        f"Unexpected current_step: {result.get('current_step')!r}"
    )
    assert gathered_context, "Gathered context must not be empty"
    assert gathered_context.get("relevant_files"), (
        "Context gathering should identify relevant files in the real repository"
    )
    assert gathered_context.get("investigation_concepts"), (
        "Context gathering should identify investigation concepts"
    )

    assert result.get("context_status") == "sufficient", (
        f"Expected sufficient context, got {result.get('context_status')!r}"
    )
    completeness = result.get("context_completeness", {})
    assert completeness.get("is_sufficient") is True, (
        f"Context completeness gate did not pass: {completeness!r}"
    )

    # Ensure later-stage investigation outputs are not generated in this phase.
    violations = find_keys_recursively(result, PROHIBITED_CONTEXT_KEYS)
    assert not violations, (
        "Context Gathering produced prohibited later-stage outputs: "
        + ", ".join(violations)
    )
