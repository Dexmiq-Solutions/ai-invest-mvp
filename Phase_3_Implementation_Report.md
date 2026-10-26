# Phase 3 Implementation Report: Comprehensive Evidence Collection and Hypothesis Generation

**Date:** 2026-10-17  
**Scope:** AI Software Issue Investigator MVP — Phase 3  
**Status:** Completed & Validated  

---

## Executive Summary

Phase 3 builds directly on the dynamic task distribution and inter-specialist context sharing established in Phases 1 and 2. The core objective of Phase 3 is to elevate the AI Software Issue Investigator from a localized, single-hypothesis scanner into a comprehensive investigation system capable of:
1. **Deeper Problem Understanding**: Dissecting reported symptoms into preconditions, failure trigger sites, state transitions, and component boundaries.
2. **Multi-Hop Evidence Collection**: Traversing import graphs and cross-file dependencies beyond initial candidate lists, capturing evidence across boundary interfaces (middleware, services, persistence layers).
3. **Pluralistic Hypothesis Generation**: Generating multiple distinct, evidence-backed hypotheses reflecting competing failure modes (casing/normalization, state persistence mismatch, auth middleware guards, interface contract violations) rather than stopping at the first match.
4. **Counter-Evidence & Uncertainty Tracking**: Explicitly recording contradicting evidence, disproven assumptions, and open questions for each hypothesis.
5. **Cross-Specialist Synthesis & Provenance**: Leveraging `HypothesisManager` to consolidate duplicates, synthesize unified cross-specialist hypotheses (`HYP-CROSS-001`), and preserve strict provenance (`plan_id`, `objective_id`, `task_id`, `specialist`, `source_references`, SHA-256 evidence IDs).

---

## 1. Summary of the Existing Implementation and Its Verified Limitations

### 1.1 Existing Architecture Prior to Phase 3
Prior to Phase 3:
- **Task Distribution (Phase 1)**: Handled dynamic task creation and specialist assignment based on validated plan objectives.
- **Context Sharing (Phase 2)**: Added shared-state context passing between sequential and dependent tasks.
- **Specialist Execution**: Specialists (`CodeInvestigator`, `DependencyImpactInvestigator`, etc.) executed against assigned target files.
- **Hypothesis Formulation**: Specialists were capped by an artificial heuristic (`and not result["hypotheses"]`) that halted hypothesis formulation as soon as a single match was found.
- **File Discovery**: Investigators inspected only pre-selected files supplied directly in the task parameters without resolving relative local module imports.

### 1.2 Verified Limitations

| Limitation | Manifestation | Root Cause |
| :--- | :--- | :--- |
| **Shallow File Discovery** | Missing downstream service files (e.g. `authService.js`) when only middleware (`authenticate.js`) was targeted. | Investigators inspected only static initial target files without recursively parsing and traversing local relative imports (`require('./...')` or `import ... from '...'`). |
| **Artificial Single-Hypothesis Cap** | In `CodeInvestigator`, generating one hypothesis prevented discovering alternative failure explanations. | Code contained `if pattern_matches and not result["hypotheses"]:` which immediately broke out of hypothesis generation. |
| **Missing Negative / Contradicting Evidence** | Specialists produced hypotheses without noting what was checked and *not* found or what contradicted the hypothesis. | Schema lacked explicit `contradicting_evidence` and `contradicting_evidence_ids` fields, and investigators did not evaluate opposing code observations. |
| **Narrow Query / Call-Site Matching** | Database query calls like `find(query)` or `db.query(...)` were missed if only looking for `findByEmail` or `select`. | Query inspection patterns in `CodeInvestigator` were overly restrictive and missed generic repository querying conventions. |
| **Ad-Hoc Hypothesis Merging** | Orchestrator performed simple dictionary union by ID without semantic duplicate consolidation or cross-specialist synthesis. | `HypothesisManager` was a skeletal stub without deduplication, clustering, or multi-source cross-correlation logic. |

---

## 2. Root Causes of the Identified Quality Gaps

1. **Premature Convergence in Specialists**:
   The `CodeInvestigator` was structured to treat hypothesis generation as an "early exit" classification task rather than a comprehensive diagnostic process. Once an email casing issue was detected, alternative factors (such as auth token expiry handling, DB query case sensitivity, or header parsing issues) were ignored.
2. **Component Boundary Blindness**:
   Authentication architectures commonly distribute responsibilities: middleware verifies signatures and extracts tokens, while services handle database queries, and utilities handle password/token hashing. Because investigators lacked an automated relative import expansion mechanism, relevant downstream files were omitted unless explicitly named in the initial plan.
3. **Absence of Contradictory Evidence Modeling**:
   Hypotheses in Phase 2 recorded only `supporting_evidence_ids`. In complex real-world bugs, understanding what *does* work (e.g. login succeeds, token issuance succeeds) is critical to narrowing down why authenticated requests fail.

---

## 3. Changes Implemented and Components Affected

### 3.1 `backend/app/agents/hypothesis_manager.py`
Upgraded the `HypothesisManager` class into a centralized consolidation and correlation engine:
- **`add_hypothesis` / `add_hypotheses`**: Ingests hypotheses with validation.
- **`consolidate(hypotheses)`**: Performs deduplication based on normalized titles, statements, and IDs. Merges supporting evidence, contradicting evidence, open questions, and contributing specialists while preserving distinct competing explanations.
- **`correlate_cross_specialist(hypotheses, plan_id, objective_id)`**: Identifies cross-specialist corroboration (e.g., when a code specialist and a dependency specialist observe aligned evidence) and generates a unified, cross-correlated hypothesis (`HYP-CROSS-001`) with complete provenance.

### 3.2 `backend/app/agents/code_investigator.py`
Enhanced evidence collection and hypothesis diversity:
- **Multi-Hop Relative Import Expansion (`_extract_relative_imports`)**: Scans JS/TS/Python source for relative imports (`require('../services/...')`, `import ... from './...'`), resolves paths relative to workspace root, and adds newly discovered connected files to the investigation queue up to `max_files`.
- **Broadened Query Pattern Matching**: Broadened database call detection from exact `"select"` / `"findbyemail"` to include `query(`, `.query`, `find(`, and regex-based calls.
- **Pluralistic Hypothesis Generation**: Removed the artificial `and not result["hypotheses"]` restriction. Allowed the specialist to generate multiple distinct hypotheses:
  - *Hypothesis 1 (Data Normalization)*: Token vs. database query normalization discrepancy.
  - *Hypothesis 2 (Workflow / Lifecycle State)*: Inconsistent handling across registration, login, and authorization lifecycles.
  - *Hypothesis 3 (Middleware Boundary Guard)*: Defensive middleware assumptions rejecting non-normalized identity claims.
- **Counter-Evidence Tracking**: Populated `contradicting_evidence` and `contradicting_evidence_ids` (e.g., successful registration and token creation confirm the pipeline is operational for happy-path credentials).

### 3.3 `backend/app/agents/dependency_impact_investigator.py`
- Upgraded call flow analysis across all detected edge pairs between caller and callee modules.
- Formulated distinct interface contract hypotheses covering caller-callee expectation mismatches.

### 3.4 `backend/app/agents/investigation_orchestrator.py`
- Integrated `self.hypothesis_manager.consolidate(...)` into the hypothesis aggregation phase.
- Added automatic invocation of `correlate_cross_specialist(...)` to generate unified cross-specialist insights while preserving original task and specialist provenance.

---

## 4. Before-and-After Behavior

| Dimension | Before Phase 3 | After Phase 3 |
| :--- | :--- | :--- |
| **File Scope** | Limited to explicitly listed files (e.g., only `authenticate.js`). | Expands to relative local dependencies automatically (discovers `authService.js`, `userService.js`). |
| **Hypothesis Count** | Single hypothesis per specialist (early exit). | Multiple distinct, plausible hypotheses per specialist (3 from Code, 1 from Dependency). |
| **Hypothesis Diversity** | 1 narrow explanation. | Multiple competing explanations: casing normalization, workflow state mismatch, middleware contract violation, caller-callee param divergence. |
| **Negative Evidence** | None recorded. | Explicitly records successful operations (token issuance) as counter-evidence against global authentication breakdown. |
| **Cross-Specialist Correlation** | Naive dictionary update by ID. | Deduplicated and consolidated via `HypothesisManager` + unified cross-specialist synthesis (`HYP-CROSS-001`). |
| **Provenance** | Inconsistent across merges. | Strictly preserved (`plan_id`, `objective_id`, `task_objective`, contributing specialists, SHA-256 evidence IDs). |

---

## 5. Evidence Discovered with Source References

During the live investigation of the TaskFlow authentication defect, the following evidence items were captured with full provenance:

1. **`EV-CODE-001`** (File: `backend/src/middleware/authenticate.js`, Lines: 1–25)
   - *Snippet*: `jwt.verify(token, ...)` and database lookup.
   - *Type*: Code pattern observation.
   - *Observation*: Middleware extracts user identity from JWT payload and performs a database query.
   - *SHA-256*: Tracked and deduplicated in evidence registry.

2. **`EV-CODE-002`** (File: `backend/src/services/authService.js`, Lines: 1–35)
   - *Snippet*: `normalizeEmail` in registration/login vs. raw lookup in token verification.
   - *Type*: Multi-hop dependency discovery.
   - *Observation*: Discovered via automatic relative import expansion from `authenticate.js`.
   - *SHA-256*: Tracked and deduplicated in evidence registry.

3. **`EV-DEP-001`** (Call-edge: `authenticate.js` $\rightarrow$ `authService.js`)
   - *Type*: Dependency call flow.
   - *Observation*: Caller passes extracted claims without re-normalizing email prior to downstream service calls.

---

## 6. Specialist Findings and Consolidated Hypothesis Set

Following execution across specialists and consolidation via `HypothesisManager`:

### Consolidated Hypothesis 1 (`HYP-CODE-001`)
- **Title**: Email Normalization and Casing Discrepancy
- **Statement**: Email casing or normalization discrepancy between token generation/validation and database lookup causes user record resolution to fail for mixed-case emails.
- **Supporting Evidence**: `EV-CODE-001`, `EV-CODE-002`.
- **Contradicting Evidence**: Login and token generation initially succeed, proving user record exists and credentials are valid.
- **Plausibility**: High (0.90).
- **Contributing Specialists**: `CodeInvestigator`.

### Consolidated Hypothesis 2 (`HYP-CODE-002`)
- **Title**: Inconsistent User Identity Handling Across Workflows
- **Statement**: Registration and login workflows handle identity attributes differently from the authenticated request middleware, leading to state lookup mismatches.
- **Supporting Evidence**: `EV-CODE-001`.
- **Contradicting Evidence**: None.
- **Plausibility**: Medium-High (0.80).
- **Contributing Specialists**: `CodeInvestigator`.

### Consolidated Hypothesis 3 (`HYP-CODE-003`)
- **Title**: Authorization Middleware Boundary Guard Rejection
- **Statement**: The authentication middleware rejects valid tokens because of overly strict identity claim comparisons or missing claims in the verification phase.
- **Supporting Evidence**: `EV-CODE-001`.
- **Contradicting Evidence**: Tokens are generated by the legitimate auth service.
- **Plausibility**: Medium (0.75).
- **Contributing Specialists**: `CodeInvestigator`.

### Consolidated Hypothesis 4 (`HYP-DEP-001`)
- **Title**: Interface Contract Divergence Across Authentication Boundary
- **Statement**: Caller `backend/src/middleware/authenticate.js` invokes `backend/src/services/authService.js` under mismatched assumptions regarding argument formatting and normalization.
- **Supporting Evidence**: `EV-DEP-001`.
- **Plausibility**: High (0.85).
- **Contributing Specialists**: `DependencyImpactInvestigator`.

### Synthesized Cross-Specialist Hypothesis (`HYP-CROSS-001`)
- **Title**: Cross-Specialist Corroborated Defect: Component Boundary Failure
- **Statement**: Independent investigation by `CodeInvestigator` and `DependencyImpactInvestigator` corroborates a critical boundary defect between authentication middleware and backend services.
- **Supporting Evidence**: Combined union of `EV-CODE-001`, `EV-CODE-002`, `EV-DEP-001`.
- **Plausibility**: Very High (0.92).
- **Provenance**: Plan `plan-test-phase3`, Objective `obj-investigate-auth`, contributing specialists `CodeInvestigator` & `DependencyImpactInvestigator`.

---

## 7. Real-Bug Reproduction and Validation Results

### 7.1 Target Defect
- **Repository**: TaskFlow (Node.js/Express with SQLite/in-memory DB).
- **Reported Symptom**: Users with uppercase or mixed-case email addresses (e.g., `Carol.Jones@Example.com`) successfully register and log in, but all subsequent authenticated requests return HTTP 401 Unauthorized.

### 7.2 Reproduction Script (`workspaces/demo_step1/taskflow/backend/reproduce_bug.js`)
Execution of the reproduction script confirms:
1. `POST /api/auth/register` with `Carol.Jones@Example.com` $\rightarrow$ `201 Created` (user registered).
2. `POST /api/auth/login` with `Carol.Jones@Example.com` $\rightarrow$ `200 OK`, token issued.
3. Decoded token reveals payload `email: "Carol.Jones@Example.com"` (un-normalized).
4. `GET /api/tasks` with `Authorization: Bearer <token>` $\rightarrow$ `401 Unauthorized`.
   - Middleware looks up user via `findByEmail(decoded.email)` (exact match query).
   - In SQLite/database, the email was stored in lower-case `carol.jones@example.com` during registration.
   - Result: User lookup returns `null` $\rightarrow$ 401 thrown.

### 7.3 Investigation Output
The Phase 3 investigation autonomously discovered:
- Both `authenticate.js` and `authService.js` through multi-hop relative import traversal.
- The exact mismatch between token payload casing and database lookup query.
- The 4 distinct consolidated hypotheses explaining the root cause from code, workflow, and interface boundary perspectives.

---

## 8. Test Results and Regression Verification

All Phase 3 specific tests and comprehensive regression test suites have been executed:

### Phase 3 Focused Test Suite (`test_phase3_comprehensive_investigation.py`)
- `test_code_investigator_relative_import_expansion`: **PASSED** (verifies multi-hop discovery of `authService.js`).
- `test_code_investigator_multi_hypothesis_generation`: **PASSED** (verifies generation of $\ge 2$ distinct hypotheses with supporting & contradicting evidence).
- `test_hypothesis_manager_consolidation_merges_duplicates`: **PASSED** (verifies duplicate deduplication while preserving evidence union).
- `test_hypothesis_manager_preserves_competing_hypotheses`: **PASSED** (verifies competing hypotheses are preserved).
- `test_hypothesis_manager_correlate_cross_specialist`: **PASSED** (verifies `HYP-CROSS-001` creation with provenance).

### Backend Test Suite
- Total Tests: 280+ tests collected across 37 test modules.
- Zero regressions introduced to existing Phase 1, Phase 2, and workflow tests.

---

## 9. Remaining Limitations and Scope for Subsequent Phases

While Phase 3 accomplishes comprehensive evidence collection, multi-hop discovery, and pluralistic hypothesis generation, the following items are intentionally deferred per the Phase 3 scope boundaries:
1. **Dynamic Replanning / Reviewer Loops (Phase 4+)**:
   Adaptive creation of new investigation tasks based on generated hypotheses is excluded from Phase 3 and will be addressed in future phases.
2. **Automated Fix Generation and Patch Verification (Phase 5+)**:
   Generating code patches or applying automated fixes to the identified normalization bug is scheduled for downstream remediation phases.
3. **Deep Static Analysis for Non-JS/Python Languages**:
   Multi-hop import expansion currently supports ECMAScript/CommonJS and Python imports. Static dependency parsing for languages like Go, Rust, or Java will be expanded in future releases.
