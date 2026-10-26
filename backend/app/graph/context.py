import logging
import os
import re
from typing import Literal, Any

from dotenv import load_dotenv
from pydantic import BaseModel, Field

from app.graph.state import InvestigationState
from app.logging_config import sanitize_log_text

load_dotenv()
logger = logging.getLogger("app.graph.context")
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
    Reuses the centralized client factory from app.llm.
    """
    from app.llm import create_base_llm

    base_llm = create_base_llm(max_tokens=1000)
    if not base_llm:
        raise RuntimeError(
            "OPENROUTER_API_KEY is not configured."
        )

    return base_llm.with_structured_output(ContextAssessment)


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

    if errors:
        logger.warning("Input validation rejected request: %s", errors)
    else:
        logger.info("Input validation passed: problem='%s'", sanitize_log_text(problem, max_len=80))

    return {
        **state,
        "validation_errors": errors,
        "current_step": "input_validated",
    }


def _is_cause_without_observable_behavior(problem: str, problem_context: Any = None) -> bool:
    """Detect if problem states an implementation cause/hypothesis without any observable behavior."""
    text = (str(problem or "") + " " + str(problem_context or "")).strip().lower()
    if not text:
        return False

    # 1. Observable behavior signals:
    has_status_code = bool(re.search(r"\b(?:[1-5]\d{2})\b", text))
    has_error = bool(re.search(r"\b(?:error|exception|traceback|crash|crashes|crashed|fatal|panic|segfault|abort|unhandled)\b", text))
    has_symptom_verb = bool(re.search(
        r"\b(?:fails?|failing|failure|can't|cannot|unable|times?\s*out|timeout|hangs?|hanging|"
        r"not\s+working|doesn't\s+work|does\s+not\s+work|isn't\s+working|"
        r"not\s+loading|doesn't\s+load|does\s+not\s+load|isn't\s+loading|"
        r"blank\s+screen|wrong\s+data|wrong\s+user|wrong\s+output|"
        r"does\s+not\s+match|doesn't\s+match|differs?\s+from|email\s+mismatch|broken|rejected|"
        r"unauthorized|forbidden|not\s+found|internal\s+server\s+error|bad\s+request)\b",
        text,
    ))
    has_action_returns = bool(re.search(r"\b(?:returns\s+\w+|shows\s+\w+|displays\s+\w+|receives\s+\w+|gets\s+\w+)\b", text))

    has_observable = has_status_code or has_error or has_symptom_verb or has_action_returns
    if has_observable:
        return False

    # 2. Implementation cause / code mechanism signals:
    cause_signals = (
        r"\bmismatch\b",
        r"\bcase[\s-]sensitiv",
        r"\bnot\s+(?:lowercase|uppercased?|trimmed|normalized|hashed|sanitized)\b",
        r"\b(?:bug|typo|defect|flaw)\s+in\b",
        r"\b(?:wrong|incorrect|invalid|improper)\s+(?:query|table|column|hash|payload|token|header|claim|field|foreign\s*key|secret)\b",
        r"\b(?:token|payload|claim|header|session)\s+has\b",
        r"\b(?:database|sql|query|table)\s+does\s+not\b",
        r"\bcaused\s+by\b",
        r"\bdue\s+to\b",
        r"\bbecause\s+of\b",
    )
    return any(re.search(pat, text) for pat in cause_signals)


def _is_specific_code_investigation_request(problem: str) -> bool:
    """Allow focused source-code investigations without requiring runtime logs."""
    text = (problem or "").strip().lower()
    if not text:
        return False

    investigation_intent = re.search(
        r"\b(?:investigate|inspect|analy[sz]e|trace|review|examine|"
        r"identify\s+relevant\s+files|source[- ]code|repository\s+code)\b",
        text,
    )
    specific_area = re.search(
        r"\b(?:auth(?:entication)?|login|token|jwt|email|user|database|"
        r"identity|session|authorization|profile)\b",
        text,
    )
    concrete_relationship = re.search(
        r"\b(?:does\s+not\s+match|doesn't\s+match|doesnt\s+match|"
        r"mismatch|differs?\s+from|inconsistent|incorrect|wrong|"
        r"does\s+not\s+correspond|not\s+the\s+same)\b",
        text,
    )
    return bool(investigation_intent and specific_area and concrete_relationship)


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

    logger.info("Context sufficiency check starting for problem: '%s'", sanitize_log_text(problem, max_len=100))

    # ========================================================
    # EMPTY PROBLEM
    # ========================================================

    if not problem:
        logger.warning("Context check rejected: problem description is empty")
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
    # SPECIFIC SOURCE-CODE INVESTIGATION
    # ========================================================
    # A focused request to inspect a concrete code-level discrepancy can
    # start static analysis without runtime logs. Runtime reproduction is
    # still required before claiming that the issue was reproduced.
    if _is_specific_code_investigation_request(problem):
        target_areas = []
        problem_lower = problem.lower()
        for area, terms in {
            "authentication": ("auth", "login", "token", "jwt"),
            "users": ("user", "identity", "profile"),
            "database": ("database", "stored", "record"),
        }.items():
            if any(term in problem_lower for term in terms):
                target_areas.append(area)

        summary = problem.split(". ")[0].split("\n")[0].strip()[:160]
        observable_behavior = (
            "Reported code-level discrepancy; runtime reproduction not verified."
        )
        logger.info(
            "Context check accepted focused source-code investigation without runtime reproduction: target_areas=%s",
            target_areas,
        )
        return {
            **state,
            "context_status": "sufficient",
            "missing_information": [],
            "context_questions": [],
            "context_analysis": {
                "decision": "sufficient",
                "problem_summary": summary or problem[:160],
                "target_areas": target_areas or ["application"],
                "observable_behavior": observable_behavior,
                "expected_behavior": (
                    "The token identity should correspond to the same user's stored record."
                    if "token" in problem_lower and "user" in problem_lower
                    else "Expected behavior not explicitly stated."
                ),
                "missing_information": [],
                "clarifying_question": None,
            },
            "validated_problem_context": {
                "problem_summary": summary or problem[:160],
                "observed_behavior": observable_behavior,
                "expected_behavior": (
                    "The token identity should correspond to the same user's stored record."
                    if "token" in problem_lower and "user" in problem_lower
                    else "Expected behavior not explicitly stated."
                ),
                "target_areas": target_areas or ["application"],
            },
            "current_step": "context_check",
        }

    # ========================================================
    # CAUSE WITHOUT OBSERVABLE BEHAVIOR (Requirement A.1)
    # ========================================================
    if _is_cause_without_observable_behavior(problem, problem_context):
        logger.info("Context check identified cause without observable symptom -> requesting symptom")
        return {
            **state,
            "context_status": "insufficient",
            "missing_information": [
                "observable_behavior",
                "symptom",
            ],
            "context_questions": [
                "What observable symptom, error message, HTTP status code, or unexpected UI behavior are you experiencing?"
            ],
            "context_analysis": {
                "decision": "insufficient",
                "problem_summary": problem,
                "target_areas": [],
                "observable_behavior": None,
                "expected_behavior": None,
                "missing_information": [
                    "observable_behavior",
                    "symptom",
                ],
                "clarifying_question": (
                    "What observable symptom, error message, HTTP status code, or unexpected UI behavior are you experiencing?"
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
            logger.info("Context check LLM evaluated problem as SUFFICIENT: target_areas=%s", assessment.target_areas)
            return {
        **state,
        "context_status": "sufficient",
        "missing_information": assessment.missing_information,
        "context_questions": [],
        "context_analysis": assessment_dict,

        "validated_problem_context": {
            "problem_summary": assessment.problem_summary or "",
            "observed_behavior": assessment.observable_behavior or "",
            "expected_behavior": assessment.expected_behavior or "",
            "target_areas": assessment.target_areas,
        },

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
        logger.info(
            "Context check LLM evaluated problem as %s: missing=%s, clarifying_question='%s'",
            assessment.decision,
            assessment.missing_information,
            question,
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
    # LLM FAILURE / FALLBACK
    # ========================================================

    except Exception as error:
        logger.debug("Context check LLM skipped or unavailable (%s); using deterministic heuristic assessment", error)
        p_lower = problem.lower()
        if _is_cause_without_observable_behavior(problem, problem_context):
            logger.info("Deterministic context check: cause without observable symptom -> insufficient")
            return {
                **state,
                "context_status": "insufficient",
                "missing_information": ["observable_behavior"],
                "context_questions": ["What observable symptom, error, or failure happens in the application?"],
                "context_analysis": {
                    "decision": "insufficient",
                    "missing_information": ["observable_behavior"],
                    "clarifying_question": "What observable symptom, error, or failure happens in the application?",
                },
                "current_step": "context_check",
            }

        vague_phrases = (
            "my application is not working",
            "my app is not working",
            "something is wrong",
            "there is an issue",
            "there is a problem",
            "it doesn't work",
            "it doesnt work",
            "the app has an issue",
            "please fix my project",
        )
        if any(v in p_lower for v in vague_phrases):
            logger.info("Deterministic context check: detected vague problem description -> insufficient")
            return {
                **state,
                "context_status": "insufficient",
                "missing_information": ["specific_problem"],
                "context_questions": ["What part of the application is failing, and what happens?"],
                "context_analysis": {
                    "decision": "insufficient",
                    "missing_information": ["specific_problem"],
                    "clarifying_question": "What part of the application is failing, and what happens?",
                },
                "current_step": "context_check",
            }

        clarification_phrases = (
            "login issue",
            "problem with the dashboard",
            "something is wrong with payments",
            "there is an issue with the profile",
        )
        if any(c in p_lower for c in clarification_phrases):
            logger.info("Deterministic context check: detected clarification phrase -> clarification_needed")
            return {
                **state,
                "context_status": "insufficient",
                "missing_information": ["specific_details"],
                "context_questions": ["Can you describe what specific error or unexpected behavior occurs?"],
                "context_analysis": {
                    "decision": "clarification_needed",
                    "missing_information": ["specific_details"],
                    "clarifying_question": "Can you describe what specific error or unexpected behavior occurs?",
                },
                "current_step": "context_check",
            }

        target_areas = []
        for area in ("authentication", "login", "profile", "users", "search", "api", "dashboard", "billing", "payment", "upload", "database", "fastapi"):
            if area in p_lower:
                target_areas.append(area)
        if not target_areas:
            target_areas = ["application"]

        logger.info("Deterministic context check: problem contains sufficient technical specificity: target_areas=%s", target_areas)

        # Extract concise summary (first sentence)
        first_sentence = problem.split(". ")[0].split("\n")[0].strip()
        summary = first_sentence[:120] if first_sentence else problem[:100]

        # Extract expected behavior if explicitly stated
        expected_match = re.search(r"([^.?!;\n]*\b(?:should|expected to|must)\b[^.?!;\n]+[.?!]?)", problem + " " + str(problem_context or ""), re.I)
        if expected_match:
            expected_behavior = expected_match.group(1).strip()
        else:
            expected_behavior = "Expected behavior not explicitly stated."

        # Extract observable behavior
        is_unreproduced = "not yet been reproduced" in p_lower or "unreproduced" in p_lower or "not reproduced" in p_lower
        symptom_match = re.search(r"([^.?!;\n]*\b(?:fails?|failing|error|reject|401|403|500|timeout|crash|not recognized|fail to recognize)\b[^.?!;\n]+[.?!]?)", problem, re.I)
        if symptom_match:
            symptom_text = symptom_match.group(1).strip()
            if is_unreproduced:
                observable_behavior = f"(Reported, unverified at runtime) {symptom_text}"
            else:
                observable_behavior = symptom_text
        elif is_unreproduced:
            observable_behavior = f"(Reported, unverified at runtime) {summary}"
        else:
            observable_behavior = summary

        return {
            **state,
            "context_status": "sufficient",
            "missing_information": [],
            "context_questions": [],
            "context_analysis": {
                "decision": "sufficient",
                "problem_summary": summary,
                "observable_behavior": observable_behavior,
                "expected_behavior": expected_behavior,
                "target_areas": target_areas,
            },
            "validated_problem_context": {
                "problem_summary": summary,
                "observed_behavior": observable_behavior,
                "expected_behavior": expected_behavior,
                "target_areas": target_areas,
            },
            "current_step": "context_check",
        }