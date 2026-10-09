import pytest

from app.graph.context import check_context


def make_state(problem):
    return {
        "problem": problem,
        "project_path": r"C:\dummy\project",
        "repository_url": "",
        "validation_errors": [],
    }


@pytest.mark.parametrize(
    "problem",
    [
        "Users can't log in.",
        "The dashboard isn't loading.",
        "File upload fails.",
        "The API returns 500 when creating a user.",
        "The profile page shows the wrong user data.",
        "The application crashes when I upload a PDF.",
        "The payment request times out.",
        "My users can't reset their password.",
    ],
)
def test_sufficient_problems(problem):
    result = check_context(make_state(problem))

    print("\nPROBLEM:", problem)
    print("STATUS:", result["context_status"])
    print("ANALYSIS:", result.get("context_analysis"))

    assert result["context_status"] == "sufficient"


@pytest.mark.parametrize(
    "problem",
    [
        "Login issue.",
        "Problem with the dashboard.",
        "Something is wrong with payments.",
        "There is an issue with the profile.",
    ],
)
def test_clarification_problems(problem):
    result = check_context(make_state(problem))

    print("\nPROBLEM:", problem)
    print("STATUS:", result["context_status"])
    print("QUESTION:", result.get("context_questions"))

    assert result["context_status"] == "insufficient"


@pytest.mark.parametrize(
    "problem",
    [
        "My application is not working.",
        "Something is wrong.",
        "There is a problem.",
        "The app has an issue.",
        "Please fix my project.",
    ],
)
def test_insufficient_problems(problem):
    result = check_context(make_state(problem))

    print("\nPROBLEM:", problem)
    print("STATUS:", result["context_status"])
    print("QUESTION:", result.get("context_questions"))

    assert result["context_status"] == "insufficient"