# Phase 2 Implementation Report: Root-Cause Proof Policy & Reviewer Guidance Alignment

## Executive Summary

Phase 2 of the AI Software Issue Investigator MVP has been successfully implemented and validated in accordance with the approved implementation plan and the Phase 1 baseline.

This phase resolves the core proof policy conflict and reviewer evaluation defect:
1. **Approved Proof Policy (Option B) Enacted:** Deterministic structural code evidence now supports a conclusive diagnosis at `medium` confidence with `status_label = "Likely root cause"` when the causal defect mechanism is deterministically established.
2. **Reviewer Guidance for Defect Classes:** Reviewer prompt guidance and heuristic evaluation now distinguish between **crash/exception defects** (which expect runtime error traces) and **silent semantic/data-flow defects** (such as parameter ordering, column-swapping, and contract mismatches), eliminating erroneous demands for runtime error logs or execution stack traces when static code analysis deterministically demonstrates the failure mechanism.
3. **Preservation of Empirical Proof Standards:** High confidence (`confidence = "high"`) and the `"Verified root cause"` status label remain strictly reserved for empirically verified findings (e.g., dynamic test probe failures or runtime reproductions). Structural code proof cannot inflate to high confidence.
4. **Strict Defect Guardrails:** Incomplete, ambiguous, or weakly supported hypotheses (e.g., missing call-site invocation or declaration, or unrelated domain issues) are strictly rejected from reaching conclusiveness.

---

## 1. Summary of Changes Made

### 1.1 `backend/app/agents/reviewer.py`
- **Structural Contract Completeness (`is_structural_contract_evidence_complete`):**
  - Added deterministic contract validation for silent data-flow and parameter-mismatch defects.
  - Requires evidence capturing both the declaration contract (e.g., `INSERT INTO tasks (title, description, status, due_date, user_id)` or column mapping) and the invocation call site (e.g., `insertWithDue.run(..., task.userId, task.dueDate)`), or verified column order in finding statements.
  - Rejects hypotheses lacking either side of the contract or exhibiting cross-domain mismatches.
- **Reviewer Prompt Defect Classification (`_review_with_llm`):**
  - Updated LLM prompt with explicit **Defect Classification** and **Option B Proof Policy**:
    - Silent semantic/data-flow defects evaluated on structural contract proof without demanding runtime crash logs.
    - Crash/exception defects evaluated on call-stack and runtime exception evidence.
  - Added strict confidence rules: structural code proof is capped at `medium` confidence; only empirical execution verification qualifies for `high` confidence.
- **LLM Output Safeguards:**
  - Added post-processing validation in `_review_with_llm` to check structural contract completeness and enforce confidence capping (`confidence = "medium"`, `status_label = "Likely root cause"`) unless `is_empirically_verified` is true.
- **Heuristic Reviewer Engine (`_review_heuristically`):**
  - Enforced `is_structural_contract_evidence_complete` checks.
  - Formulates structured causal reasoning that explicitly explains how the code contract mismatch produces the functional symptom and persistence corruption.
  - Assigns `medium` confidence and `"Likely root cause"` status label for fully established structural code defects.
  - Preserves `high` confidence and `"Verified root cause"` for empirical verification.
  - Emits actionable feedback and missing evidence items when contract evidence is partial or incomplete.

### 1.2 `backend/app/graph/graph.py`
- **`root_cause_result_node`:**
  - Aligned status labels with Option B policy:
    - `is_empirical == True` and `confidence == "high"` -> `status_label = "Verified root cause"`
    - Deterministic structural code proof (`confidence == "medium"`) -> `status_label = "Likely root cause"`
  - Added `status_label` to `diagnosis` dictionary in both `root_cause_result_node` and `inconclusive_result_node` to ensure uniform schema downstream.
- **`inconclusive_result_node`:**
  - Preserved Phase 1 supporting evidence selection while ensuring `status_label = "Inconclusive"` and low confidence.

### 1.3 `backend/app/agents/investigation_orchestrator.py`
- Updated `InvestigationReviewer._review_with_llm` prompt template to mirror the defect classification guidance and Option B confidence rules.

---

## 2. Validation & Test Execution Results

### 2.1 Real-World TaskFlow Investigation (Live Execution)
Executed `python tests/run_taskflow_task_creation_live.py` against the running FastAPI application with the actual `TaskFlow.zip` project repository.

- **Defect Scenario:** Task creation due-date / userId parameter mismatch in `backend/src/models/taskModel.js`.
- **Declaration Evidence Captured:**
  - `EV-CODE-001` (`taskflow/backend/src/models/taskModel.js:3`):
    `const insertWithDue = db.prepare('INSERT INTO tasks (title, description, status, due_date, user_id) VALUES (?, ?, ?, ?, ?)');`
- **Invocation Evidence Captured:**
  - `EV-CODE-006` (`taskflow/backend/src/models/taskModel.js:13`):
    `return insertWithDue.run(task.title, task.description, task.status, task.userId, task.dueDate);`
- **Reviewer Decision:** `decision = "sufficient"`, `is_sufficient = True`
- **Reviewer Confidence:** `confidence = "medium"`
- **Conclusiveness & Status:** `is_conclusive = True`, `status_label = "Likely root cause"`
- **Reviewer Causal Reasoning:**
  > "The hypothesis regarding the parameter order mismatch in task creation is supported by multiple pieces of evidence. The code snippets show that the parameters for the SQL insert statement in the createTask function are indeed being passed in the wrong order, which leads to incorrect data being inserted into the database. Specifically, the evidence IDs EV-CODE-001, EV-CODE-006, and others confirm that the parameters are not aligned with the expected order in the SQL statement. This mismatch directly correlates with the reported problem of task creation failing when due dates are involved, as the incorrect parameter order can lead to runtime errors or silent failures in data integrity. Therefore, the evidence establishes a clear causal relationship between the defect mechanism and the observed symptom."
- **Diagnosis Output:**
  - **Root Cause:** "Parameter Order Mismatch in Task Creation: The parameters for the SQL insert statement in the createTask function are being passed in the wrong order, leading to incorrect data being inserted into the database."
  - **Confidence:** `medium`
  - **Status Label:** `Likely root cause`
  - **Supporting Evidence Count:** 10 items
- **Session Output Artifact:** Saved to `backend/session-output-phase2.json`.
- **Live Script Status:** All 11 programmatic assertions passed (`Exit code 0`).

### 2.2 Phase 2 Policy Unit & Integration Tests
File: `backend/tests/test_phase2_reviewer_policy.py`
- `test_contract_evidence_complete_with_declaration_and_invocation`: PASSED
- `test_contract_evidence_incomplete_missing_invocation`: PASSED
- `test_contract_evidence_incomplete_missing_declaration`: PASSED
- `test_reviewer_sufficient_on_complete_structural_code_evidence`: PASSED
- `test_reviewer_medium_confidence_for_structural_defect`: PASSED
- `test_incomplete_structural_evidence_rejected`: PASSED
- `test_unrelated_hypothesis_domain_mismatch_rejected`: PASSED
- `test_empirical_verification_preserves_high_confidence`: PASSED
- `test_root_cause_node_labels_medium_confidence_as_likely_root_cause`: PASSED
- `test_root_cause_node_labels_empirical_as_verified_root_cause`: PASSED
- `test_inconclusive_node_sets_low_confidence_and_inconclusive_label`: PASSED
**Result:** **11 passed in 1.63s**

### 2.3 Phase 1 Fixes Regression Test Suite
File: `backend/tests/test_phase1_fixes.py`
- `test_multiline_evidence_captures_complete_statement`: PASSED
- `test_single_line_evidence_preserved`: PASSED
- `test_deduplication_and_provenance_preserved`: PASSED
- `test_inconclusive_evidence_selection_from_candidate_hypothesis`: PASSED
- `test_inconclusive_evidence_fallback_when_no_candidate`: PASSED
- `test_task_budget_enforcement_allows_valid_iterations`: PASSED
- `test_replanner_generates_new_tasks_within_budget`: PASSED
**Result:** **7 passed in 1.69s**

### 2.4 Workflow & Graph Regression Test Suite
Files: `backend/tests/test_wf_workflow_issues_regression.py`, `backend/tests/test_graph.py`
**Result:** **11 passed in 2.18s**

### 2.5 Phase 8 Reviewer & Re-planner Comprehensive Integration Suite
File: `backend/tests/test_phase8_reviewer_replanner.py`
- 19 integration tests covering reviewer decisions, heuristic fallback, re-planning budget loops, evidence aggregation, and graph transitions.
**Result:** **19 passed in 18.88s**

---

## 3. Preservation of Prior Work
- The three Phase 1 fixes (multi-line statement capture, candidate hypothesis evidence resolution in inconclusive diagnoses, and task budget decoupling) were completely preserved with zero regression or unnecessary refactoring.
- The 10-field task schema, SHA-256 evidence provenance deduplication, and single-pass execution contracts remain intact.
