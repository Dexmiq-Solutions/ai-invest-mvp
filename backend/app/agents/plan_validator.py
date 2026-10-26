"""Phase 3: Context-Aware Plan Validation Engine.

Enforces structural and semantic plan validation immediately before
specialist task dispatch across all execution pathways.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Set
from pydantic import BaseModel, Field

from app.logging_config import sanitize_log_text
from app.agents.investigator_contract import STOP_WORDS, extract_terms

logger = logging.getLogger(__name__)

DEFAULT_AVAILABLE_AGENTS = {
    "code_investigator",
    "dependency_impact_investigator",
    "runtime_error_investigator",
}

AVAILABLE_INVESTIGATOR_AGENTS = DEFAULT_AVAILABLE_AGENTS

# 10 mandatory fields required for each task in an investigation plan
MANDATORY_TASK_FIELDS = (
    "task_id",
    "objective",
    "assigned_agent",
    "reason",
    "input",
    "scope",
    "instructions",
    "expected_output",
    "completion_criteria",
    "status",
)

# Common manifest and config file names for dependency inspection
MANIFEST_FILES = {
    "package.json",
    "package-lock.json",
    "requirements.txt",
    "requirements-dev.txt",
    "pyproject.toml",
    "pipfile",
    "pipfile.lock",
    "poetry.lock",
    "setup.py",
    "setup.cfg",
    "pom.xml",
    "build.gradle",
    "gemfile",
    "cargo.toml",
    "go.mod",
    "composer.json",
}


class PlanValidationResult(BaseModel):
    """Structured result returned by validate_investigation_plan or validate_plan_strategy."""

    is_valid: bool = Field(description="Whether the investigation plan passed all validation checks")
    errors: List[str] = Field(default_factory=list, description="List of fatal validation errors")
    warnings: List[str] = Field(default_factory=list, description="List of non-fatal validation warnings")
    validated_task_count: int = Field(default=0, description="Count of validated tasks in the plan")
    validated_objective_count: int = Field(default=0, description="Count of validated objectives in the plan")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "is_valid": self.is_valid,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "validated_task_count": self.validated_task_count,
            "validated_objective_count": self.validated_objective_count or self.validated_task_count,
        }

    def __getitem__(self, item: str) -> Any:
        return getattr(self, item)

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default)

    def __contains__(self, item: str) -> bool:
        return hasattr(self, item)


class ProblemUnderstanding(BaseModel):
    """First-class representation of problem understanding in the investigation plan."""

    original_problem: str = Field(default="", description="Original problem statement reported by user")
    problem_summary: str = Field(default="", description="Concise summary of the problem")
    observed_symptoms: List[str] = Field(default_factory=list, description="Observed symptoms and error signals")
    expected_behavior: str = Field(default="", description="Expected system behavior, separating explicit from inferred expectations")
    uncertainties_and_unknowns: List[str] = Field(default_factory=list, description="Important unknowns, diagnostic gaps, and ambiguities")
    scope_and_constraints: List[str] = Field(default_factory=list, description="Scope boundaries and investigation constraints")

    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump()


class InvestigationObjective(BaseModel):
    """Specific inquiry objective defining what needs to be examined and why."""

    objective_id: str = Field(description="Unique stable objective identifier, e.g. OBJ-001")
    title: str = Field(description="What needs to be investigated")
    relevance_to_symptoms: str = Field(description="Why this matters to the reported symptoms")
    possible_explanations: List[str] = Field(default_factory=list, description="Possible explanations or failure areas to examine")
    discriminating_evidence: str = Field(default="", description="Information that would help confirm or eliminate possibilities")
    completion_outcome: str = Field(default="", description="Outcome that marks the objective as completed")

    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump()


class InvestigationApproach(BaseModel):
    """Logical execution strategy and dependencies for the investigation."""

    execution_paths_to_probe: List[str] = Field(default_factory=list, description="Components or execution paths to examine first")
    data_flows_and_interfaces: List[str] = Field(default_factory=list, description="Data flows, function interactions, interfaces, or transformations")
    dependencies_and_ordering: List[str] = Field(default_factory=list, description="Ordering and question dependencies")
    contingency_plans: str = Field(default="", description="How to proceed if expected files or behavior cannot be located")

    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump()


class SpecialistStrategy(BaseModel):
    """Specialist subagents selected and their investigation responsibilities."""

    selected_specialists: List[str] = Field(default_factory=list, description="Selected specialist agents")
    specialist_responsibilities: Dict[str, str] = Field(default_factory=dict, description="Responsibilities assigned to each specialist")

    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump()


class InvestigationPlan(BaseModel):
    """First-class investigation plan artifact representing strategy before task decomposition."""

    plan_id: str = Field(description="Stable unique plan identifier, e.g. PLAN-001")
    session_id: str = Field(default="unspecified", description="Associated session or investigation identifier")
    problem_reference: str = Field(default="", description="Reference to the original problem")
    problem_understanding: ProblemUnderstanding = Field(default_factory=ProblemUnderstanding)
    objectives: List[InvestigationObjective] = Field(default_factory=list)
    approach: InvestigationApproach = Field(default_factory=InvestigationApproach)
    specialist_strategy: SpecialistStrategy = Field(default_factory=SpecialistStrategy)
    completion_criteria: List[str] = Field(default_factory=list, description="Requirements before investigation can be considered complete")
    lifecycle_status: str = Field(default="draft", description="Lifecycle status: draft, validated, execution-ready")
    validation_status: str = Field(default="pending", description="Validation status: pending, validated, invalid")
    validation_warnings: List[str] = Field(default_factory=list)
    validation_errors: List[str] = Field(default_factory=list)
    created_at: float = Field(default=0.0)
    updated_at: float = Field(default=0.0)

    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump()


def _extract_tasks(plan: Any) -> Optional[List[Dict[str, Any]]]:
    """Extract a list of task dictionaries from various plan container shapes."""
    if plan is None:
        return None

    if isinstance(plan, list):
        return plan

    if isinstance(plan, dict):
        for key in ("tasks", "steps", "plan", "investigation_plan"):
            candidate = plan.get(key)
            if isinstance(candidate, list):
                return candidate
        return None

    if hasattr(plan, "steps") and isinstance(plan.steps, list):
        return [s.model_dump() if hasattr(s, "model_dump") else dict(s) for s in plan.steps]

    if hasattr(plan, "tasks") and isinstance(plan.tasks, list):
        return [t.model_dump() if hasattr(t, "model_dump") else dict(t) for t in plan.tasks]

    return None


def _is_broad_discovery_scope(scope_items: List[str]) -> bool:
    """Check if scope denotes broad repository exploration."""
    for item in scope_items:
        s = str(item).strip().lower()
        if s in ("*", ".*", "all", "all_files", "repository", "root", ".", "./", "/"):
            return True
        if "*" in s or "?" in s:
            return True
        # Directory-level broad discovery
        if s in ("src", "app", "backend", "frontend", "server", "lib", "packages"):
            return True
    return False


def _is_dependency_task(agent: str, scope_items: List[str], text_corpus: str) -> bool:
    """Check if a task is legitimate dependency/contract/manifest analysis."""
    if agent == "dependency_impact_investigator":
        return True

    for item in scope_items:
        clean = str(item).replace("\\", "/").lower().rsplit("/", 1)[-1]
        if clean in MANIFEST_FILES:
            return True

    dep_keywords = {"dependency", "dependencies", "import", "imports", "caller", "callers", "manifest", "package", "module"}
    return any(kw in text_corpus for kw in dep_keywords)


def _is_runtime_task(agent: str, scope_items: List[str], text_corpus: str) -> bool:
    """Check if a task is legitimate runtime log/error/exception inspection."""
    if agent == "runtime_error_investigator":
        return True

    for item in scope_items:
        clean = str(item).replace("\\", "/").lower()
        if clean.endswith(".log") or "log" in clean or "error" in clean or "trace" in clean:
            return True

    runtime_keywords = {"runtime", "exception", "error", "traceback", "stack", "status code", "crash", "500", "401", "404", "log"}
    return any(kw in text_corpus for kw in runtime_keywords)


# Boundary assertion patterns: unproven root causes stated as settled facts
BOUNDARY_ASSERTION_PATTERNS = [
    re.compile(r"\b(?:the\s+)?root\s+cause\s+is\b", re.IGNORECASE),
    re.compile(r"\b(?:proven|confirmed)\s+that\b", re.IGNORECASE),
    re.compile(r"\bthe\s+bug\s+is\s+caused\s+by\b", re.IGNORECASE),
    re.compile(r"^fix\s+(?:the\s+bug\s+in\s+|the\s+defect\s+in\s+|the\s+issue\s+in\s+)", re.IGNORECASE),
    re.compile(r"\b(?:confirm|verify)\s+this\s+(?:root\s+cause|bug|defect)\b", re.IGNORECASE),
]

# Evidence claim patterns: claims of evidence already discovered
EVIDENCE_CLAIM_PATTERNS = [
    re.compile(r"\b(?:we\s+have\s+already\s+found|evidence\s+already\s+(?:collected|discovered))\b", re.IGNORECASE),
    re.compile(r"\b(?:finding\s+already\s+verified|already\s+confirmed\s+at\s+line)\b", re.IGNORECASE),
]

# Placeholder tokens for task fields
PLACEHOLDER_TOKENS = {"n/a", "none", "todo", "tbd", "placeholder", "tba", "null", "nil"}


def _has_runtime_signals(
    problem: str,
    gathered_context: Dict[str, Any],
    task_input: Dict[str, Any],
    task_corpus: str,
) -> bool:
    """Check if runtime diagnostic signals, errors, exceptions, or status codes exist."""
    runtime_terms = {
        "exception", "traceback", "stack", "trace", "crash",
        "500", "401", "403", "404", "502", "503", "504", "400",
        "status code", "status_code", "timeout", "unhandled", "panic",
        "segfault", "nullpointer", "typeerror", "runtime error", "runtimeerror",
        "referenceerror", "log", "logs", "stderr", "abort", "fatal",
        "undefined is not a function", "null pointer", "cannot read property",
    }
    prob_lower = (problem or "").lower()
    if any(term in prob_lower for term in runtime_terms):
        return True
    if "error" in prob_lower and not any(k in prob_lower for k in ("calculation error", "arithmetic error", "rounding error", "typo error", "formula error")):
        return True

    # In task corpus, look for specific runtime diagnostics (excluding calculation/formula error)
    task_lower = (task_corpus or "").lower()
    task_has_calc = any(k in task_lower for k in ("calculation", "arithmetic", "formula", "bounding box", "geometry"))
    if any(term in task_lower for term in runtime_terms):
        if not task_has_calc or any(t in task_lower for t in ("crash", "stack", "traceback", "exception", "nullpointer", "null pointer", "segfault", "panic")):
            return True

    if isinstance(gathered_context, dict):
        if (
            gathered_context.get("stack_trace")
            or gathered_context.get("error_logs")
            or gathered_context.get("diagnostic_signals")
            or gathered_context.get("stack_frames")
        ):
            return True
        for rf in gathered_context.get("relevant_files") or []:
            p = rf.get("path") if isinstance(rf, dict) else str(rf)
            if p and (str(p).endswith(".log") or "trace" in str(p).lower()):
                return True

    if isinstance(task_input, dict):
        if (
            task_input.get("stack_trace")
            or task_input.get("logs")
            or task_input.get("status_code")
        ):
            return True

    return False


def _build_context_term_set(problem: str, gathered_context: Dict[str, Any]) -> Set[str]:
    """Collect significant words, symbols, and concepts from problem and context."""
    terms = set()

    # Extract terms from problem text
    if problem:
        for t in extract_terms(problem, limit=30):
            terms.add(t.lower())
        for word in re.findall(r"[A-Za-z0-9_]{3,}", problem):
            w = word.lower()
            if w not in STOP_WORDS:
                terms.add(w)

    # Extract from gathered context fields
    if isinstance(gathered_context, dict):
        for concept in gathered_context.get("investigation_concepts") or []:
            if isinstance(concept, str):
                terms.add(concept.lower())
                for sub in re.findall(r"[A-Za-z0-9_]{3,}", concept):
                    if sub.lower() not in STOP_WORDS:
                        terms.add(sub.lower())

        for term in gathered_context.get("technical_terms") or []:
            if isinstance(term, str):
                terms.add(term.lower())

        for comp in gathered_context.get("related_components") or []:
            if isinstance(comp, str):
                terms.add(comp.lower())

        for fw in gathered_context.get("frameworks_detected") or []:
            if isinstance(fw, str):
                terms.add(fw.lower())

        prob_understanding = gathered_context.get("problem_understanding")
        if isinstance(prob_understanding, dict):
            for val in prob_understanding.values():
                if isinstance(val, str):
                    for sub in re.findall(r"[A-Za-z0-9_]{3,}", val):
                        if sub.lower() not in STOP_WORDS:
                            terms.add(sub.lower())

        # Include file basenames from relevant_files
        for rf in gathered_context.get("relevant_files") or []:
            p = rf.get("path") if isinstance(rf, dict) else str(rf)
            if p:
                clean_p = str(p).replace("\\", "/").lower()
                basename = clean_p.rsplit("/", 1)[-1].split(".")[0]
                if len(basename) >= 3 and basename not in STOP_WORDS:
                    terms.add(basename)

    return terms


def validate_investigation_plan(
    plan: Any,
    available_agents: Optional[Set[str] | List[str] | Dict[str, Any]] = None,
    problem: str = "",
    gathered_context: Optional[Dict[str, Any]] = None,
    session_id: Optional[str] = None,
) -> PlanValidationResult:
    """
    Context-aware pre-dispatch plan validation engine (Phase 3).

    Validates:
    1. Plan structure and non-empty list of tasks.
    2. Unique, non-empty task IDs.
    3. All 10 mandatory task fields.
    4. Valid and available specialist agent assignments.
    5. Non-empty objectives, reasons, instructions, expected outputs, completion criteria.
    6. Non-empty scope and input dictionary.
    7. No duplicate tasks (identical agent, scope, instructions).
    8. Context-aware semantic task relevance to the problem and gathered context.
    9. Legitimate broad-discovery scopes when initial search is sparse.
    10. Legitimate dependency-inspection and runtime-analysis tasks.

    Does NOT depend on or require prohibited context-gathering structures
    (evidence_candidates, trace_flows, producer_consumer_pairs, hypotheses).
    """
    sid = session_id or "unspecified"
    context = gathered_context or {}
    problem_str = str(problem or "").strip()

    if available_agents is None:
        agent_set = set(DEFAULT_AVAILABLE_AGENTS)
    elif isinstance(available_agents, (set, list, tuple)):
        agent_set = set(available_agents)
    elif isinstance(available_agents, dict):
        agent_set = set(available_agents.keys())
    else:
        agent_set = set(DEFAULT_AVAILABLE_AGENTS)

    raw_tasks = _extract_tasks(plan)

    logger.info(
        "[PlanValidator] plan_validation_start: session_id=%s, task_count=%d",
        sid,
        len(raw_tasks) if raw_tasks is not None else 0,
    )

    errors: List[str] = []
    warnings: List[str] = []

    # Rule 1 & 2: Plan container & non-empty task list
    if raw_tasks is None or not isinstance(raw_tasks, list):
        errors.append("Investigation plan must be a list of tasks.")
        logger.error("[PlanValidator] plan_validation_failed: session_id=%s, errors=%s", sid, errors)
        logger.warning(
            "[PlanValidator] plan_validation_dispatch_blocked: zero specialists will be dispatched due to plan validation failure for session_id=%s",
            sid,
        )
        logger.info(
            "[PlanValidator] plan_validation_complete: session_id=%s, is_valid=False, validated_task_count=0, errors_count=%d, warnings_count=%d",
            sid,
            len(errors),
            len(warnings),
        )
        return PlanValidationResult(is_valid=False, errors=errors, warnings=warnings, validated_task_count=0)

    if len(raw_tasks) == 0:
        errors.append("Investigation plan is empty. At least one task is required.")
        logger.error("[PlanValidator] plan_validation_failed: session_id=%s, errors=%s", sid, errors)
        logger.warning(
            "[PlanValidator] plan_validation_dispatch_blocked: zero specialists will be dispatched due to plan validation failure for session_id=%s",
            sid,
        )
        logger.info(
            "[PlanValidator] plan_validation_complete: session_id=%s, is_valid=False, validated_task_count=0, errors_count=%d, warnings_count=%d",
            sid,
            len(errors),
            len(warnings),
        )
        return PlanValidationResult(is_valid=False, errors=errors, warnings=warnings, validated_task_count=0)

    # Build context term set for semantic relevance checking
    context_terms = _build_context_term_set(problem_str, context)
    relevant_files_in_context = set()
    for rf in context.get("relevant_files") or []:
        p = rf.get("path") if isinstance(rf, dict) else str(rf)
        if p:
            clean = str(p).replace("\\", "/").lower()
            relevant_files_in_context.add(clean)
            relevant_files_in_context.add(clean.rsplit("/", 1)[-1])

    seen_task_ids: Set[str] = set()
    seen_task_signatures: Set[tuple] = set()
    valid_task_count = 0

    for idx, raw_task in enumerate(raw_tasks):
        if hasattr(raw_task, "model_dump"):
            task = raw_task.model_dump()
        elif isinstance(raw_task, dict):
            task = dict(raw_task)
        else:
            errors.append(f"Task at index {idx} must be a dictionary or task model, got {type(raw_task).__name__}.")
            continue

        # Extract normalized values resolving backward-compatible aliases
        task_id = str(task.get("task_id") or task.get("step_id") or "").strip()
        objective = str(task.get("objective") or task.get("task") or "").strip()
        assigned_agent = str(
            task.get("assigned_agent")
            or task.get("assigned_sub_agent")
            or task.get("investigator")
            or ""
        ).strip()
        reason = str(task.get("reason") or task.get("rationale") or "").strip()
        instructions = str(task.get("instructions") or task.get("task") or "").strip()
        expected_output = str(task.get("expected_output") or task.get("expected_evidence") or "").strip()
        completion_criteria = str(task.get("completion_criteria") or "").strip()
        status = str(task.get("status") or "pending").strip()

        task_label = f"Task '{task_id}'" if task_id else f"Task at index {idx}"
        task_has_errors = False

        # Rule 3: Unique, non-empty task IDs
        if not task_id:
            errors.append(f"Task at index {idx} has an empty or missing task_id; task_id must be a non-empty string.")
            task_has_errors = True
        elif task_id in seen_task_ids:
            errors.append(f"Duplicate task ID '{task_id}' found in investigation plan.")
            task_has_errors = True
        else:
            seen_task_ids.add(task_id)

        # Rule 4: Check missing mandatory fields (checking primary names and aliases)
        missing_fields = []
        if not (task.get("task_id") or task.get("step_id")):
            missing_fields.append("task_id")
        if not (task.get("objective") or task.get("task")):
            missing_fields.append("objective")
        if not (task.get("assigned_agent") or task.get("assigned_sub_agent") or task.get("investigator")):
            missing_fields.append("assigned_agent")
        if not (task.get("reason") or task.get("rationale")):
            missing_fields.append("reason")
        if "input" not in task:
            missing_fields.append("input")
        if "scope" not in task and "target_files" not in task:
            missing_fields.append("scope")
        if not (task.get("instructions") or task.get("task")):
            missing_fields.append("instructions")
        if not (task.get("expected_output") or task.get("expected_evidence")):
            missing_fields.append("expected_output")
        if not task.get("completion_criteria"):
            missing_fields.append("completion_criteria")
        if "status" not in task:
            missing_fields.append("status")

        if missing_fields:
            errors.append(f"{task_label} is missing mandatory field(s): {', '.join(missing_fields)}.")
            task_has_errors = True

        # Rule 5: Valid and available agent assignments
        if not assigned_agent:
            errors.append(f"{task_label} has unknown or unassigned specialist agent.")
            task_has_errors = True
        elif assigned_agent not in agent_set:
            errors.append(
                f"{task_label} has unknown or unassigned specialist agent '{assigned_agent}'. Available agents: {sorted(agent_set)}."
            )
            task_has_errors = True

        # Rule 6: Non-empty objectives, reasons, instructions, expected outputs, and completion criteria
        if not objective:
            errors.append(f"{task_label} has an empty objective; objective must be a non-empty string.")
            task_has_errors = True
        if not reason:
            errors.append(f"{task_label} has an empty reason; reason must be a non-empty string.")
            task_has_errors = True
        if not instructions:
            errors.append(f"{task_label} has empty instructions; instructions must be a non-empty string.")
            task_has_errors = True
        if not expected_output:
            errors.append(f"{task_label} has an empty expected_output; expected_output must be a non-empty string.")
            task_has_errors = True
        if not completion_criteria:
            errors.append(f"{task_label} has an empty completion_criteria; completion_criteria must be a non-empty string.")
            task_has_errors = True

        # Rule 6b: Placeholder values and actionability
        if objective.strip().lower() in PLACEHOLDER_TOKENS or objective.strip().lower() in {"investigate", "fix", "test"}:
            errors.append(f"{task_label} has placeholder objective '{objective}'; must specify a concrete investigation goal.")
            task_has_errors = True
        if instructions.strip().lower() in PLACEHOLDER_TOKENS or instructions.strip().lower() in {"investigate", "check", "fix", "test", "inspect"}:
            errors.append(f"{task_label} has placeholder instructions '{instructions}'; must specify actionable inspection steps.")
            task_has_errors = True
        if completion_criteria.strip().lower() in PLACEHOLDER_TOKENS:
            errors.append(f"{task_label} has placeholder completion_criteria '{completion_criteria}'; must specify concrete verifiable criteria.")
            task_has_errors = True
        elif completion_criteria.strip().lower() in {"done", "ok", "completed"}:
            warnings.append(f"{task_label} completion criteria '{completion_criteria}' is generic; specific verifiable conditions are recommended.")

        # Rule 6c: Strict planning/investigation boundary enforcement
        for b_pat in BOUNDARY_ASSERTION_PATTERNS:
            if b_pat.search(objective) or b_pat.search(instructions):
                errors.append(
                    f"{task_label} violates the planning/investigation boundary by asserting an unverified conclusion as fact."
                )
                task_has_errors = True
                break

        for ec_pat in EVIDENCE_CLAIM_PATTERNS:
            if ec_pat.search(expected_output) or ec_pat.search(instructions):
                errors.append(
                    f"{task_label} violates the planning/investigation boundary by claiming evidence already discovered instead of specifying what to collect."
                )
                task_has_errors = True
                break

        # Type validations for input and scope
        task_input = task.get("input")
        if task_input is not None and not isinstance(task_input, dict):
            errors.append(f"{task_label} input must be a dictionary, got {type(task_input).__name__}.")
            task_has_errors = True

        raw_scope = task.get("scope") if "scope" in task else task.get("target_files")
        if raw_scope is None:
            scope_items = []
            errors.append(f"{task_label} scope must be a list, got None.")
            task_has_errors = True
        elif isinstance(raw_scope, list):
            scope_items = [str(s).strip() for s in raw_scope if str(s).strip()]
            if len(scope_items) == 0:
                errors.append(f"{task_label} has an empty scope list. Specify target files or ['*'] for broad discovery.")
                task_has_errors = True
        elif isinstance(raw_scope, str) and raw_scope.strip():
            scope_items = [raw_scope.strip()]
            warnings.append(f"{task_label} scope was provided as a string instead of a list.")
        else:
            scope_items = []
            errors.append(f"{task_label} scope must be a list, got {type(raw_scope).__name__}.")
            task_has_errors = True

        # Status validation
        if status and status.lower() not in ("pending", "not_started", "ready", "completed", "in_progress", "partial", "unresolved", "failed", "skipped"):
            warnings.append(f"{task_label} status is '{status}', expected 'pending'.")

        # Duplicate task detection (identical agent, scope, and instructions)
        if assigned_agent and scope_items and instructions:
            task_sig = (assigned_agent, tuple(sorted(s.lower() for s in scope_items)), instructions.lower())
            if task_sig in seen_task_signatures:
                warnings.append(f"{task_label} is a duplicate task with identical agent, scope, and instructions.")
            else:
                seen_task_signatures.add(task_sig)

        # Rules 7, 8, 9: Semantic Task Relevance & Context Awareness
        task_text_corpus = f"{objective} {reason} {instructions} {expected_output}".lower()
        is_broad = _is_broad_discovery_scope(scope_items)
        is_dep = _is_dependency_task(assigned_agent, scope_items, task_text_corpus)
        is_runtime = _is_runtime_task(assigned_agent, scope_items, task_text_corpus)

        # Check if scope matches any relevant file identified during context gathering
        matches_relevant_file = False
        for s in scope_items:
            clean_s = s.replace("\\", "/").lower()
            if clean_s in relevant_files_in_context or clean_s.rsplit("/", 1)[-1] in relevant_files_in_context:
                matches_relevant_file = True
                break

        # Check if task text matches problem or gathered context vocabulary
        task_words = set(re.findall(r"[A-Za-z0-9_]{3,}", task_text_corpus))
        has_context_overlap = bool(task_words & context_terms)

        is_relevant = (
            matches_relevant_file
            or is_broad
            or is_dep
            or is_runtime
            or has_context_overlap
        )

        if not is_relevant and problem_str:
            errors.append(
                f"{task_label} lacks relevance and is not relevant to the problem description or gathered context."
            )
            task_has_errors = True

        # Rule 11: Specialist Alignment
        if assigned_agent == "runtime_error_investigator" and problem_str:
            if not _has_runtime_signals(problem_str, context, task_input or {}, task_text_corpus):
                errors.append(
                    f"{task_label} has inappropriate specialist assignment: 'runtime_error_investigator' assigned without any reported runtime errors, crashes, exceptions, status codes, or stack traces in problem or context."
                )
                task_has_errors = True
        elif assigned_agent == "dependency_impact_investigator" and problem_str:
            is_manifest = any(
                str(item).replace("\\", "/").lower().rsplit("/", 1)[-1] in MANIFEST_FILES
                for item in scope_items
            )
            is_dep_scope = (
                _is_broad_discovery_scope(scope_items)
                or is_manifest
                or len(scope_items) > 1
                or any(kw in task_text_corpus for kw in ("dependency", "import", "caller", "callee", "middleware", "cross-component", "contract", "interface", "flow", "boundary", "package"))
            )
            if not is_dep_scope:
                warnings.append(
                    f"{task_label} assigns 'dependency_impact_investigator' to a single file without dependency, caller, or import analysis scope."
                )

        # Rule 12: Scope hygiene
        if assigned_agent == "code_investigator" and scope_items:
            lockfile_names = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "cargo.lock", "gemfile.lock"}
            if all(str(item).replace("\\", "/").lower().rsplit("/", 1)[-1] in lockfile_names for item in scope_items):
                warnings.append(
                    f"{task_label} assigns code investigation solely to package lockfiles; verify if application source files should be targeted."
                )

        if not task_has_errors:
            valid_task_count += 1

    is_valid = len(errors) == 0
    validated_count = valid_task_count if is_valid else 0

    logger.info(
        "[PlanValidator] plan_validation_complete: session_id=%s, is_valid=%s, validated_task_count=%d, errors_count=%d, warnings_count=%d",
        sid,
        is_valid,
        validated_count,
        len(errors),
        len(warnings),
    )

    if is_valid:
        logger.info(
            "[PlanValidator] plan_validation_success: session_id=%s, validated_task_count=%d, warnings_count=%d",
            sid,
            validated_count,
            len(warnings),
        )
    else:
        logger.error(
            "[PlanValidator] plan_validation_failed: session_id=%s, error_count=%d, errors=%s, warnings=%s",
            sid,
            len(errors),
            errors,
            warnings,
        )
        logger.warning(
            "[PlanValidator] plan_validation_dispatch_blocked: zero specialists will be dispatched due to plan validation failure for session_id=%s",
            sid,
        )

    return PlanValidationResult(
        is_valid=is_valid,
        errors=errors,
        warnings=warnings,
        validated_task_count=validated_count,
    )


def validate_plan_strategy(
    plan: Any,
    problem: str = "",
    gathered_context: Optional[Dict[str, Any]] = None,
    session_id: Optional[str] = None,
) -> PlanValidationResult:
    """
    Validates a high-level strategic InvestigationPlan artifact BEFORE task decomposition.

    Evaluates:
    1. Relevance to the original problem.
    2. Coverage of reported symptoms.
    3. Logical ordering, paths, and dependencies in approach.
    4. Completeness and concreteness of investigation objectives.
    5. Appropriate specialist selection (e.g., runtime investigator not selected without runtime signals).
    6. Boundary enforcement: no premature assertion of confirmed root cause or fabricated evidence.
    7. Completion criteria defined for future execution.
    """
    sid = session_id or "unspecified"
    context = gathered_context or {}
    prob_str = str(problem or "").strip()

    logger.info("[PlanValidator] validate_plan_strategy_start: session_id=%s", sid)

    errors: List[str] = []
    warnings: List[str] = []

    if plan is None:
        errors.append("Investigation plan artifact is missing (None).")
        return PlanValidationResult(is_valid=False, errors=errors, warnings=warnings, validated_task_count=0)

    # Convert to dict or object access
    if isinstance(plan, InvestigationPlan):
        plan_dict = plan.model_dump()
        plan_obj = plan
    elif isinstance(plan, dict):
        plan_dict = plan
        plan_obj = None
    elif hasattr(plan, "model_dump"):
        plan_dict = plan.model_dump()
        plan_obj = plan
    else:
        errors.append(f"Invalid plan artifact type: {type(plan).__name__}. Expected InvestigationPlan or dict.")
        return PlanValidationResult(is_valid=False, errors=errors, warnings=warnings, validated_task_count=0)

    # 1. Problem Understanding
    prob_understanding = plan_dict.get("problem_understanding") or {}
    if not isinstance(prob_understanding, dict):
        errors.append("Plan lacks a valid 'problem_understanding' section.")
    else:
        orig = str(prob_understanding.get("original_problem") or "").strip()
        summary = str(prob_understanding.get("problem_summary") or "").strip()
        if not orig and not summary and not prob_str:
            errors.append("Problem understanding is missing both original_problem and problem_summary.")

        symptoms = prob_understanding.get("observed_symptoms") or []
        if not isinstance(symptoms, list) or len(symptoms) == 0:
            errors.append("Problem understanding lacks documented observed symptoms.")

    # 2. Investigation Objectives
    raw_objectives = plan_dict.get("objectives") or []
    if not isinstance(raw_objectives, list) or len(raw_objectives) == 0:
        errors.append("Investigation plan contains no objectives. At least one objective is required.")
        valid_objs_count = 0
    else:
        valid_objs_count = 0
        context_terms = _build_context_term_set(prob_str or prob_understanding.get("original_problem", ""), context)
        generic_patterns = {
            "investigate", "inspect", "debug", "look at files", "fix bug", "test code",
            "run tests", "check everything", "find error", "investigate issue"
        }

        premature_assertions = (
            "root cause is confirmed",
            "the root cause is proven",
            "root cause is found",
            "defect is confirmed in",
            "proven that",
            "bug is verified as",
            "confirmed that",
        )

        for idx, obj in enumerate(raw_objectives):
            obj_dict = obj if isinstance(obj, dict) else (obj.model_dump() if hasattr(obj, "model_dump") else {})
            obj_id = str(obj_dict.get("objective_id") or f"OBJ-{idx + 1:03d}")
            title = str(obj_dict.get("title") or "").strip()
            relevance = str(obj_dict.get("relevance_to_symptoms") or "").strip()
            explanations = obj_dict.get("possible_explanations") or []

            if not title or len(title) < 5:
                errors.append(f"Objective {obj_id} has an empty or inadequate title.")
                continue

            if title.lower() in generic_patterns:
                errors.append(f"Objective {obj_id} has a generic placeholder title ('{title}').")

            if not relevance:
                errors.append(f"Objective {obj_id} lacks an explanation of its relevance to reported symptoms.")

            if not isinstance(explanations, list) or len(explanations) == 0:
                warnings.append(f"Objective {obj_id} does not list possible explanations or failure directions to examine.")

            # Boundary enforcement: Planning must not assert confirmed findings as fact
            full_obj_text = f"{title} {relevance} {' '.join(str(e) for e in explanations)}".lower()
            if any(pa in full_obj_text for pa in premature_assertions):
                errors.append(
                    f"Objective {obj_id} violates planning boundary by asserting a confirmed root cause before investigation execution."
                )

            # Relevance to problem context
            if context_terms and len(context_terms) >= 3:
                obj_terms = set(extract_terms(full_obj_text))
                overlap = obj_terms.intersection(context_terms)
                if not overlap and len(obj_terms) > 0:
                    warnings.append(
                        f"Objective {obj_id} has minimal keyword overlap with reported problem or context."
                    )

            valid_objs_count += 1

    # 3. Specialist Strategy
    spec_strategy = plan_dict.get("specialist_strategy") or {}
    if not isinstance(spec_strategy, dict):
        errors.append("Plan lacks a valid 'specialist_strategy' section.")
    else:
        selected = spec_strategy.get("selected_specialists") or []
        if not isinstance(selected, list) or len(selected) == 0:
            errors.append("Specialist strategy must select at least one specialist investigator.")
        else:
            invalid_agents = [s for s in selected if s not in AVAILABLE_INVESTIGATOR_AGENTS]
            if invalid_agents:
                errors.append(f"Specialist strategy selects invalid investigator(s): {invalid_agents}")

            # Check runtime investigator justification
            if "runtime_error_investigator" in selected:
                combined_prob = (
                    prob_str
                    + " "
                    + str(prob_understanding.get("original_problem") or "")
                    + " "
                    + str(prob_understanding.get("observed_symptoms") or "")
                    + " "
                    + str(context.get("errors") or "")
                    + " "
                    + str(context.get("stack_trace") or "")
                ).lower()
                has_runtime_signal = any(
                    sig in combined_prob
                    for sig in (
                        "exception", "traceback", "stack trace", "crash", "crashed",
                        "status 5", "500 internal", "http 500", "502", "503", "504",
                        "http 401", "401", "403", "unauthorized", "forbidden",
                        "nullpointer", "typeerror", "keyerror", "valueerror", "zerodivision",
                        "indexerror", "attributeerror", "unhandled", "runtime error", "panic", "segfault"
                    )
                ) or bool(context.get("stack_trace") or (isinstance(context.get("errors"), list) and len(context.get("errors")) > 0 and any("error" in str(e).lower() or "exception" in str(e).lower() for e in context.get("errors"))))
                if not has_runtime_signal:
                    errors.append(
                        "Specialist 'runtime_error_investigator' was selected without runtime errors, exceptions, stack traces, or crash signals in the reported problem."
                    )

            # Responsibilities
            responsibilities = spec_strategy.get("specialist_responsibilities") or {}
            if isinstance(responsibilities, dict):
                for s in selected:
                    if not responsibilities.get(s):
                        warnings.append(f"No specific responsibilities documented for selected specialist '{s}'.")

    # 4. Investigation Approach
    approach = plan_dict.get("approach") or {}
    if not isinstance(approach, dict):
        errors.append("Plan lacks a valid 'approach' section.")
    else:
        paths = approach.get("execution_paths_to_probe") or []
        ordering = approach.get("dependencies_and_ordering") or []
        if not paths and not ordering:
            warnings.append("Investigation approach does not define execution paths to probe or ordering dependencies.")

    # 5. Completion Criteria
    criteria = plan_dict.get("completion_criteria") or []
    if not isinstance(criteria, list) or len(criteria) == 0:
        errors.append("Investigation plan must define completion criteria before task execution.")

    is_valid = len(errors) == 0

    # Update status on plan if accessible
    status_str = "validated" if is_valid else "invalid"
    lifecycle_str = "validated" if is_valid else "invalid"

    if plan_obj is not None:
        try:
            plan_obj.validation_status = status_str
            plan_obj.validation_errors = list(errors)
            plan_obj.validation_warnings = list(warnings)
            plan_obj.lifecycle_status = lifecycle_str
        except Exception:
            pass

    if isinstance(plan, dict):
        plan["validation_status"] = status_str
        plan["validation_errors"] = list(errors)
        plan["validation_warnings"] = list(warnings)
        plan["lifecycle_status"] = lifecycle_str

    logger.info(
        "[PlanValidator] validate_plan_strategy_complete: session_id=%s, is_valid=%s, valid_objectives=%d, errors=%d, warnings=%d",
        sid,
        is_valid,
        valid_objs_count,
        len(errors),
        len(warnings),
    )

    return PlanValidationResult(
        is_valid=is_valid,
        errors=errors,
        warnings=warnings,
        validated_task_count=valid_objs_count,
        validated_objective_count=valid_objs_count,
    )
