from app.graph.state import InvestigationState


def validate_input(state: InvestigationState) -> InvestigationState:
    """
    Validate the minimum information required to start an investigation.
    """

    errors = []

    problem = state.get("problem", "").strip()
    project_path = state.get("project_path", "").strip()
    repository_url = state.get("repository_url", "").strip()

    if not problem:
        errors.append("Problem description is required.")

    if not project_path and not repository_url:
        errors.append(
            "A project path or repository URL is required."
        )

    if project_path and repository_url:
        errors.append(
            "Provide either a project path or repository URL, not both."
        )

    return {
        **state,
        "validation_errors": errors,
        "current_step": "input_validated",
    }

def check_context(state: InvestigationState) -> InvestigationState:
    """
    Determine whether the provided engineering problem contains
    enough information to proceed with investigation.
    """

    problem = state.get("problem", "").strip()

    if not problem:
        return {
            **state,
            "context_status": "insufficient",
            "missing_information": ["problem"],
            "context_questions": [
                "What part of the application is not working?"
            ],
            "current_step": "context_check",
        }

    normalized_problem = problem.lower().strip().rstrip(".!?")

    vague_patterns = (
        "not working",
        "doesn't work",
        "doesnt work",
        "something is wrong",
        "there is an issue",
        "there is a problem",
    )

    is_vague = (
    normalized_problem in vague_patterns
    or normalized_problem.endswith("application is not working")
    or normalized_problem.endswith("app is not working")
    or normalized_problem.endswith("doesn't work")
    or normalized_problem.endswith("doesnt work")
)

    if is_vague:
        return {
            **state,
            "context_status": "insufficient",
            "missing_information": ["specific_problem"],
            "context_questions": [
                "Which part of the application is not working? "
                "If possible, provide the expected behavior, actual behavior, "
                "or any error message."
            ],
            "current_step": "context_check",
        }

    return {
        **state,
        "context_status": "sufficient",
        "missing_information": [],
        "context_questions": [],
        "current_step": "context_check",
    }
    """
    Determine whether the provided engineering problem contains
    enough information to proceed with investigation.
    """

    problem = state.get("problem", "").strip()
    problem_context = state.get("problem_context", {}) or {}

    missing_information = []
    context_questions = []

    if not problem:
        return {
            **state,
            "context_status": "insufficient",
            "missing_information": ["problem"],
            "context_questions": [
                "What part of the application is not working?"
            ],
            "current_step": "context_check",
        }

    # Very broad descriptions do not provide enough information
    vague_problems = {
        "not working",
        "doesn't work",
        "doesnt work",
        "application is not working",
        "app is not working",
        "something is wrong",
        "there is an issue",
        "there is a problem",
    }

    normalized_problem = problem.lower().strip().rstrip(".!?")

    if normalized_problem in vague_problems:
        missing_information.append("specific_problem")
        context_questions.append(
            "Which part of the application is not working? "
            "If possible, provide the expected behavior, actual behavior, "
            "or any error message."
        )

    # Check whether additional context was explicitly supplied.
    # We don't require it because the problem description itself
    # may already be sufficient.
    if not problem_context:
        pass

    if missing_information:
        return {
            **state,
            "context_status": "insufficient",
            "missing_information": missing_information,
            "context_questions": context_questions,
            "current_step": "context_check",
        }

    return {
        **state,
        "context_status": "sufficient",
        "missing_information": [],
        "context_questions": [],
        "current_step": "context_check",
    }