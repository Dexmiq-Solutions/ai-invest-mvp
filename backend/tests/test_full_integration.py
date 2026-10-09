from pathlib import Path
import json

from app.graph.graph import build_graph


# ============================================================
# CONFIGURATION
# ============================================================

# Change this to the actual TaskFlow ZIP/project path.
# Example:
# PROJECT_PATH = r"C:\Users\Dellj'\Downloads\taskflow.zip"

PROJECT_PATH = r"C:\Users\Dellj'\Downloads\TaskFlow.zip"


# Real TaskFlow bug symptom.
# IMPORTANT:
# Do NOT include the official answer/root cause here.
PROBLEM = (
    "Some users can log in successfully, but every authenticated request "
    "returns HTTP 401. The issue occurs for users whose registered email "
    "contains uppercase letters."
)

PROBLEM_CONTEXT = {
    "observed_behavior": (
        "Login succeeds and returns a token, but subsequent authenticated "
        "requests return HTTP 401 for affected accounts."
    ),
    "expected_behavior": (
        "After successful login, authenticated requests should succeed."
    ),
    "error": "401 Unauthorized",
    "target_areas": [
        "authentication",
        "login",
        "authenticated requests",
    ],
}


# ============================================================
# INPUT
# ============================================================

initial_state = {
    "problem": PROBLEM,
    "project_path": PROJECT_PATH,
    "problem_context": PROBLEM_CONTEXT,
}


# ============================================================
# HELPERS
# ============================================================

def print_section(title):
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)


def print_json(title, data):
    print_section(title)

    if data is None:
        print("None")
        return

    try:
        print(json.dumps(data, indent=2, default=str))
    except TypeError:
        print(data)


# ============================================================
# RUN PIPELINE
# ============================================================

def main():
    project_path = Path(PROJECT_PATH)

    if not project_path.exists():
        print("ERROR: Project path does not exist:")
        print(project_path)
        return

    print_section("TASKFLOW END-TO-END PIPELINE TEST")

    print("Project:")
    print(project_path)

    print("\nProblem:")
    print(PROBLEM)

    print("\nStarting graph...")

    graph = build_graph()

    try:
        result = graph.invoke(initial_state)
    except Exception as error:
        print_section("PIPELINE FAILED")
        print(f"{type(error).__name__}: {error}")
        raise

    # ========================================================
    # FINAL STATE
    # ========================================================

    print_section("PIPELINE RESULT")

    print("Current step:")
    print(result.get("current_step"))

    print("\nWorkspace ID:")
    print(result.get("workspace_id"))

    print("\nWorkspace path:")
    print(result.get("workspace_path"))

    print("\nWorkspace source type:")
    print(result.get("workspace_source_type"))

    # ========================================================
    # PHASE RESULTS
    # ========================================================

    print_json(
        "VALIDATED PROBLEM CONTEXT",
        result.get("validated_problem_context"),
    )

    print_json(
        "CONTEXT STATUS",
        result.get("context_status"),
    )

    print_json(
        "REPOSITORY SUMMARY",
        result.get("repository_summary"),
    )

    print_json(
        "GATHERED CONTEXT",
        result.get("gathered_context"),
    )

    # ========================================================
    # ERRORS
    # ========================================================

    print_json(
        "VALIDATION / PIPELINE ERRORS",
        result.get("validation_errors", []),
    )

    # ========================================================
    # IMPORTANT CHECKS
    # ========================================================

    print_section("PIPELINE CHECKS")

    checks = {
        "Input accepted": bool(result.get("problem")),
        "Validation passed": not bool(result.get("validation_errors")),
        "Context status available": bool(result.get("context_status")),
        "Workspace created": bool(result.get("workspace_path")),
        "Repository analyzed": bool(result.get("repository_summary")),
        "Context gathered": bool(result.get("gathered_context")),
        "Relevant files found": bool(
            result.get("gathered_context", {}).get("relevant_files")
        ),
        "Investigation concepts found": bool(
            result.get("gathered_context", {}).get("investigation_concepts")
        ),
        "Technical terms found": bool(
            result.get("gathered_context", {}).get("technical_terms")
        ),
        "Evidence candidates found": bool(
            result.get("gathered_context", {}).get("evidence_candidates")
        ),
    }

    passed = 0

    for name, status in checks.items():
        symbol = "PASS" if status else "FAIL"

        print(f"[{symbol}] {name}")

        if status:
            passed += 1

    print("\nResult:")
    print(f"{passed}/{len(checks)} checks passed")

    # ========================================================
    # SHOW IMPORTANT GATHERED CONTEXT
    # ========================================================

    gathered = result.get("gathered_context", {})

    print_json(
        "RELEVANT FILES",
        gathered.get("relevant_files", []),
    )

    print_json(
        "INVESTIGATION CONCEPTS",
        gathered.get("investigation_concepts", []),
    )

    print_json(
        "TECHNICAL TERMS",
        gathered.get("technical_terms", []),
    )

    print_json(
        "RELATED COMPONENTS",
        gathered.get("related_components", []),
    )

    print_json(
        "RELATIONSHIPS",
        gathered.get("relationships", []),
    )

    print_json(
        "EVIDENCE CANDIDATES",
        gathered.get("evidence_candidates", []),
    )

    print_json(
        "CONTEXT SUMMARY",
        gathered.get("context_summary"),
    )

def test_full_integration():
    project_path = Path(PROJECT_PATH)

    assert project_path.exists(), (
        f"TaskFlow project not found: {project_path}"
    )

    graph = build_graph()

    result = graph.invoke(initial_state)

    print_section("FINAL PIPELINE STATE")

    print_json("FINAL STATE", result)

    assert result.get("problem")
    assert result.get("workspace_path")
    assert result.get("repository_summary")
    assert result.get("gathered_context")

    assert result.get("current_step") == "context_gathered"