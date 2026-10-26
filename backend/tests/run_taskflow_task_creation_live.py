"""Run live TaskFlow task-creation investigation against running FastAPI server and validate Phase 2 policy."""

import json
import sys
import requests
from pathlib import Path

BASE_URL = "http://127.0.0.1:8000"

# Find TaskFlow.zip
zip_candidates = [
    Path(r"..\TaskFlow.zip"),
    Path(r"C:\Users\Dellj'\Downloads\TaskFlow.zip"),
    Path("TaskFlow.zip"),
]
ZIP_PATH = None
for cand in zip_candidates:
    if cand.resolve().exists():
        ZIP_PATH = cand.resolve()
        break

if not ZIP_PATH:
    print("Error: TaskFlow.zip not found", file=sys.stderr)
    sys.exit(1)

print(f"Using TaskFlow archive: {ZIP_PATH}")

problem = "Task creation fails when creating tasks with due dates in taskModel.js. Parameters are passed out of order."
developer_context = (
    "In taskModel.js, insertWithDue takes (title, description, categoryId, dueDate, userId). "
    "Trace column ordering in SQL INSERT."
)

print("=" * 70)
print("RUNNING REAL TASKFLOW TASK-CREATION INVESTIGATION (PHASE 2 VALIDATION)")
print("=" * 70)

# Step 1: Start investigation
print("\n[Step 1] POST /api/investigation/start ...")
with open(ZIP_PATH, "rb") as f:
    resp = requests.post(
        f"{BASE_URL}/api/investigation/start",
        data={"problem": problem},
        files={"project_zip": ("TaskFlow.zip", f, "application/zip")},
        timeout=120,
    )
resp.raise_for_status()
start_data = resp.json()
session_id = start_data["session_id"]
print(f"  Session ID: {session_id}")
print(f"  Current step: {start_data.get('current_step')}")

# Step 2: Add Developer Context
print(f"\n[Step 2] POST /api/investigation/{session_id}/context ...")
resp = requests.post(
    f"{BASE_URL}/api/investigation/{session_id}/context",
    json={"additional_context": developer_context},
    timeout=60,
)
resp.raise_for_status()
ctx_data = resp.json()
print(f"  Context status: {ctx_data.get('context_status')}")

# Step 3: Workspace creation
print(f"\n[Step 3] POST /api/investigation/{session_id}/workspace ...")
resp = requests.post(
    f"{BASE_URL}/api/investigation/{session_id}/workspace",
    timeout=60,
)
resp.raise_for_status()
ws_data = resp.json()
print(f"  Workspace ID: {ws_data.get('workspace_id')}")
print(f"  Workspace Path: {ws_data.get('workspace_path')}")

# Step 4: Repository Analysis
print(f"\n[Step 4] POST /api/investigation/{session_id}/repository-analysis ...")
resp = requests.post(
    f"{BASE_URL}/api/investigation/{session_id}/repository-analysis",
    timeout=60,
)
resp.raise_for_status()
repo_data = resp.json()
print(f"  Status: {repo_data.get('status')}")

# Step 5: Context Gathering
print(f"\n[Step 5] POST /api/investigation/{session_id}/context-gathering ...")
resp = requests.post(
    f"{BASE_URL}/api/investigation/{session_id}/context-gathering",
    timeout=120,
)
resp.raise_for_status()
gather_data = resp.json()
print(f"  Status: {gather_data.get('status')}")

# Step 6: Investigation Plan
print(f"\n[Step 6] POST /api/investigation/{session_id}/plan ...")
resp = requests.post(
    f"{BASE_URL}/api/investigation/{session_id}/plan",
    timeout=60,
)
resp.raise_for_status()
plan_data = resp.json()
plan_steps = plan_data.get("plan", [])
print(f"  Plan raw output type: {type(plan_steps)}")
if isinstance(plan_steps, list):
    print(f"  Planned steps count: {len(plan_steps)}")
    for s in plan_steps:
        if isinstance(s, dict):
            print(f"    - [{s.get('step_id') or s.get('task_id')}] {s.get('investigator') or s.get('assigned_agent')}: {s.get('task') or s.get('objective')}")
        else:
            print(f"    - {s}")
else:
    print(f"  Plan representation: {str(plan_steps)[:200]}...")

# Step 7: Investigation Execution
print(f"\n[Step 7] POST /api/investigation/{session_id}/execute ...")
resp = requests.post(
    f"{BASE_URL}/api/investigation/{session_id}/execute",
    timeout=300,
)
resp.raise_for_status()
exec_data = resp.json()
print(f"  Execution status: {exec_data.get('execution_status')}")
print(f"  Findings count: {len(exec_data.get('findings', []))}")
print(f"  Evidence count: {len(exec_data.get('evidence', []))}")

# Step 8: Trigger Reviewer Evaluation
print(f"\n[Step 8] POST /api/investigation/{session_id}/review ...")
resp = requests.post(
    f"{BASE_URL}/api/investigation/{session_id}/review",
    timeout=180,
)
resp.raise_for_status()
review_api_data = resp.json()
review_res = review_api_data.get("review_result", {})
route = review_api_data.get("route")
print(f"  Review decision: {review_res.get('decision')}")
print(f"  Is sufficient: {review_res.get('is_sufficient')}")
print(f"  Confidence: {review_res.get('confidence')}")
print(f"  Route: {route}")
print(f"  Status label: {review_api_data.get('status_label')}")
print(f"  Is conclusive: {review_api_data.get('is_conclusive')}")

# Step 9: Retrieve Complete Final Session State
print(f"\n[Step 9] GET /api/investigation/{session_id} ...")
resp = requests.get(f"{BASE_URL}/api/investigation/{session_id}", timeout=60)
resp.raise_for_status()
session_record = resp.json()
final_state = session_record.get("state", {})

# Save output to session-output-phase2.json
output_file = Path("session-output-phase2.json")
with open(output_file, "w", encoding="utf-8") as out:
    json.dump(session_record, out, indent=2, ensure_ascii=False)
print(f"  Saved session output to: {output_file.resolve()}")

# Step 10: Validation Audit Checks
print("\n" + "=" * 70)
print("PHASE 2 VALIDATION AUDIT RESULTS")
print("=" * 70)

evidence_items = final_state.get("evidence", []) or final_state.get("evidence_pool", [])
has_sql_declaration = any(
    "insert into" in str(e.get("matched_text") or e.get("snippet") or "").lower()
    for e in evidence_items
)
has_invocation = any(
    "insertwithdue.run" in str(e.get("matched_text") or e.get("snippet") or "").lower()
    for e in evidence_items
)

print(f"1. Complete SQL declaration captured: {has_sql_declaration}")
for e in evidence_items:
    snip = str(e.get("matched_text") or e.get("snippet") or "")
    if "insert into" in snip.lower():
        print(f"   - {e.get('evidence_id')} ({e.get('source')}:{e.get('line')}): {snip[:90]}...")

print(f"2. Invocation call site captured: {has_invocation}")
for e in evidence_items:
    snip = str(e.get("matched_text") or e.get("snippet") or "")
    if "insertwithdue.run" in snip.lower():
        print(f"   - {e.get('evidence_id')} ({e.get('source')}:{e.get('line')}): {snip[:90]}...")

print(f"3. Reviewer decision: {review_res.get('decision')} (is_sufficient={review_res.get('is_sufficient')})")
print(f"4. Reviewer confidence: {review_res.get('confidence')}")
print(f"5. Final status label: {final_state.get('status_label')}")
print(f"6. Is conclusive: {final_state.get('is_conclusive')}")
print(f"7. Reviewer reasoning: {review_res.get('reasoning')}")

diagnosis = final_state.get("diagnosis", {})
if isinstance(diagnosis, dict):
    print(f"8. Diagnosis root cause: {diagnosis.get('root_cause')}")
    print(f"9. Diagnosis confidence: {diagnosis.get('confidence')}")
    print(f"10. Diagnosis status label: {diagnosis.get('status_label')}")
    print(f"11. Diagnosis supporting evidence count: {len(diagnosis.get('supporting_evidence', []))}")

# Assertions for programmatic validation
assert has_sql_declaration, "SQL declaration evidence must be present"
assert has_invocation, "Invocation evidence must be present"
assert review_res.get("decision") == "sufficient", f"Expected sufficient, got {review_res.get('decision')}"
assert review_res.get("is_sufficient") is True, "Expected is_sufficient=True"
assert str(review_res.get("confidence")).lower() == "medium", f"Expected medium confidence, got {review_res.get('confidence')}"
assert final_state.get("status_label") == "Likely root cause", f"Expected 'Likely root cause', got {final_state.get('status_label')}"
assert final_state.get("is_conclusive") is True, "Expected is_conclusive=True"

print("\n>>> ALL PHASE 2 VALIDATION AUDIT CRITERIA PASSED SUCCESSFULLY! <<<")
