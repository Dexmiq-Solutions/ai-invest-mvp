import os
from typing import Literal

from pydantic import BaseModel, Field
from langchain_openrouter import ChatOpenRouter

from app.graph.state import InvestigationState

import os

from dotenv import load_dotenv
from pydantic import BaseModel, Field
from langchain_openrouter import ChatOpenRouter

from app.graph.state import InvestigationState


load_dotenv()
# ============================================================
# LLM STRUCTURED OUTPUT
# ============================================================

class ContextAssessment(BaseModel):
    """
    Structured result returned by the LLM Context Checker.
    """

    decision: Literal[
        "sufficient",
        "clarification_needed",
        "insufficient",
    ] = Field(
        description=(
            "Whether the problem contains enough information "
            "to begin repository investigation."
        )
    )

    problem_summary: str | None = Field(
        default=None,
        description=(
            "A short summary of the engineering problem "
            "reported by the developer."
        ),
    )

    target_areas: list[str] = Field(
        default_factory=list,
        description=(
            "Application areas that appear relevant, such as "
            "authentication, login, dashboard, API, database, "
            "file upload, payment, frontend, backend, etc."
        ),
    )

    observable_behavior: str | None = Field(
        default=None,
        description=(
            "What is actually happening according to the developer."
        ),
    )

    expected_behavior: str | None = Field(
        default=None,
        description=(
            "What the developer expected to happen, if provided."
        ),
    )

    missing_information: list[str] = Field(
        default_factory=list,
        description=(
            "Important information that is missing before "
            "investigation can begin."
        ),
    )

    clarifying_question: str | None = Field(
        default=None,
        description=(
            "A concise question asking the developer for "
            "the missing information."
        ),
    )


# ============================================================
# LLM
# ============================================================

def _get_context_llm():
    """
    Create the LLM used for Context Check.

    This project uses OpenRouter.

    Required environment variable:

        OPENROUTER_API_KEY

    Optional:

        OPENROUTER_MODEL
    """

    api_key = os.getenv("OPENROUTER_API_KEY")

    if not api_key:
        raise RuntimeError(
            "OPENROUTER_API_KEY is not configured."
        )

    model_name = os.getenv(
        "OPENROUTER_MODEL",
        "openai/gpt-4o-mini",
    )

    llm = ChatOpenRouter(
        model=model_name,
        temperature=0,
        api_key=api_key,
    )

    return llm.with_structured_output(ContextAssessment)


# ============================================================
# BASIC INPUT VALIDATION
# ============================================================

def validate_input(
    state: InvestigationState,
) -> InvestigationState:
    """
    Validate the minimum information required to start
    an investigation.

    This validation is deterministic.

    The LLM is only responsible for understanding whether
    the problem description contains enough context.
    """

    errors = []

    problem = state.get("problem", "").strip()
    project_path = state.get("project_path", "").strip()
    repository_url = state.get("repository_url", "").strip()

    # --------------------------------------------------------
    # Problem validation
    # --------------------------------------------------------

    if not problem:
        errors.append(
            "Problem description is required."
        )

    # --------------------------------------------------------
    # Project validation
    # --------------------------------------------------------

    if not project_path and not repository_url:
        errors.append(
            "A project path or repository URL is required."
        )

    # --------------------------------------------------------
    # Source conflict
    # --------------------------------------------------------

    if project_path and repository_url:
        errors.append(
            "Provide either a project path or repository URL, "
            "not both."
        )

    return {
        **state,
        "validation_errors": errors,
        "current_step": "input_validated",
    }


# ============================================================
# LLM CONTEXT CHECK
# ============================================================

def check_context(
    state: InvestigationState,
) -> InvestigationState:
    """
    Determine whether the developer has provided enough
    information to START repository investigation.

    This function does NOT diagnose the root cause.

    It determines:

    - What the reported problem appears to be
    - Which application areas may be relevant
    - Whether investigation can begin
    - What information is missing
    """

    problem = state.get("problem", "").strip()

    problem_context = (
        state.get("problem_context") or {}
    )

    # ========================================================
    # EMPTY PROBLEM
    # ========================================================

    if not problem:
        return {
            **state,
            "context_status": "insufficient",
            "missing_information": [
                "problem",
            ],
            "context_questions": [
                "What part of the application is not working?"
            ],
            "context_analysis": {
                "decision": "insufficient",
                "problem_summary": None,
                "target_areas": [],
                "observable_behavior": None,
                "expected_behavior": None,
                "missing_information": [
                    "problem",
                ],
                "clarifying_question": (
                    "What part of the application is not working?"
                ),
            },
            "current_step": "context_check",
        }

    # ========================================================
    # ADDITIONAL CONTEXT
    # ========================================================

    context_text = ""

    if problem_context:
        context_text = (
            "\n\nAdditional developer-provided context:\n"
            f"{problem_context}"
        )

    # ========================================================
    # SYSTEM PROMPT
    # ========================================================

    system_prompt = """
You are the Context Check component of an AI Software
Investigation System.

Your responsibility is ONLY to determine whether the
developer has provided enough information to START
investigating an existing software project.

You are NOT the root-cause investigator.

Do NOT diagnose the bug.

Do NOT invent technical details.

Do NOT guess missing information.

Do NOT require the developer to know:
- the affected file
- the function
- the API endpoint
- the database table
- the framework
- the technical root cause
- technical terminology

The repository will be investigated later to discover
technical details.

------------------------------------------------------------
DECISION RULES
------------------------------------------------------------

Return "sufficient" when the problem describes a concrete
behavior, failure, affected feature, or application area
that gives an investigator a meaningful starting point.

Examples:

"Users can't log in."
→ sufficient

"The dashboard isn't loading."
→ sufficient

"File upload fails."
→ sufficient

"The API returns 500 when creating a user."
→ sufficient

"The profile page shows the wrong user data."
→ sufficient

"The application crashes when I upload a PDF."
→ sufficient

"The payment request times out."
→ sufficient

Notice that these statements do NOT provide the root cause.
That is okay.

------------------------------------------------------------

Return "clarification_needed" when the developer identifies
a specific area or feature but the actual problem is too vague
to understand what behavior should be investigated.

Examples:

"Login issue."
→ clarification_needed

"Problem with the dashboard."
→ clarification_needed

"Something is wrong with payments."
→ clarification_needed

"There is an issue with the profile."
→ clarification_needed

In these cases, ask a concise question about what is actually
happening.

------------------------------------------------------------

Return "insufficient" when the statement is completely generic
and does not identify a meaningful application area or behavior.

Examples:

"My application is not working."
→ insufficient

"Something is wrong."
→ insufficient

"There is a problem."
→ insufficient

"The app has an issue."
→ insufficient

"Please fix my project."
→ insufficient

------------------------------------------------------------
IMPORTANT
------------------------------------------------------------

The existence of a project repository does NOT automatically
make a vague problem sufficient.

For example:

Project: uploaded successfully
Problem: "My application is not working."

The result must still be "insufficient".

The project will be analyzed AFTER Context Check succeeds.

------------------------------------------------------------

A simple natural-language problem is enough if it provides
a meaningful investigation starting point.

For example:

"My users can't reset their password."

This is sufficient even though the developer does not know
whether the problem is caused by frontend code, backend code,
email delivery, authentication, database logic, or anything else.

The investigator will discover those technical details later.

------------------------------------------------------------

For "sufficient":

- provide a concise problem_summary
- identify likely target_areas
- describe observable_behavior when possible
- include expected_behavior only if stated or clearly provided
- missing_information may be empty
- clarifying_question should normally be null

For "clarification_needed" or "insufficient":

- identify what information is missing
- provide ONE concise clarifying question
- do not invent the missing information

Return only the structured assessment.
"""

    human_prompt = f"""
Developer's problem:

{problem}
{context_text}
"""

    # ========================================================
    # CALL LLM
    # ========================================================

    try:
        llm = _get_context_llm()

        assessment = llm.invoke(
            [
                (
                    "system",
                    system_prompt,
                ),
                (
                    "human",
                    human_prompt,
                ),
            ]
        )

        assessment_dict = assessment.model_dump()

        # ====================================================
        # SUFFICIENT
        # ====================================================

        if assessment.decision == "sufficient":
            return {
                **state,
                "context_status": "sufficient",
                "missing_information": (
                    assessment.missing_information
                ),
                "context_questions": [],
                "context_analysis": assessment_dict,
                "current_step": "context_check",
            }

        # ====================================================
        # CLARIFICATION / INSUFFICIENT
        # ====================================================

        question = (
            assessment.clarifying_question
            or (
                "Please provide more information about "
                "what is happening."
            )
        )

        return {
            **state,
            "context_status": "insufficient",
            "missing_information": (
                assessment.missing_information
            ),
            "context_questions": [
                question,
            ],
            "context_analysis": assessment_dict,
            "current_step": "context_check",
        }

    # ========================================================
    # LLM FAILURE
    # ========================================================

    except Exception as error:
        return {
            **state,
            "context_status": "context_check_failed",
            "missing_information": [
                "context_check",
            ],
            "context_questions": [
                "The problem could not be analyzed automatically. "
                "Please try again or provide more details about "
                "what is happening."
            ],
            "context_analysis": {
                "decision": "context_check_failed",
                "problem_summary": None,
                "target_areas": [],
                "observable_behavior": None,
                "expected_behavior": None,
                "missing_information": [
                    "context_check",
                ],
                "clarifying_question": (
                    "The problem could not be analyzed automatically."
                ),
                "error": str(error),
            },
            "validation_errors": [
                *state.get("validation_errors", []),
                f"Context check failed: {error}",
            ],
            "current_step": "context_check_failed",
        }