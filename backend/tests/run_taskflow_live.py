"""Run live TaskFlow investigation against the running FastAPI server and record results."""

import json
import os
import sys
import requests
from pathlib import Path

BASE_URL = "http://127.0.0.1:8000"
ZIP_PATH = Path(r"C:\Users\Dellj'\Downloads\TaskFlow.zip")

if not ZIP_PATH.exists():
    print(f"Error: ZIP file not found at {ZIP_PATH}", file=sys.stderr)
    sys.exit(1)

problem = (
    "The email in the login token does not match the email stored for "
    "the user. Investigate the authentication flow, token generation, "
    "stored user email, and any mismatch between them. Identify relevant "
    "files and report findings based on actual code."
)

developer_context = (
    "The reported issue is that the email in the login token does not match "
    "the email stored for the user. The exact runtime symptom, error message, "
    "and HTTP status code have not yet been provided. Inspect the actual TaskFlow "
    "source code to trace where the user email is stored, how the login token is "
    "generated, and how the token email is validated or consumed. Identify the "
    "relevant files and possible mismatch based on code evidence; do not assume "
    "the root cause."
)

print("=" * 70)
print("RUNNING LIVE TASKFLOW INVESTIGATION (PHASES 1-6)")
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
print(f"  Status: {start_data.get('status')}")
print(f"  Current step: {start_data.get('current_step')}")
print(f"  Clarifying questions: {start_data.get('clarifying_questions')}")

# Step 2: Add Developer Context
print(f"\n[Step 2] POST /api/investigation/{session_id}/context ...")
resp = requests.post(
    f"{BASE_URL}/api/investigation/{session_id}/context",
    json={"additional_context": developer_context},
    timeout=60,
)
resp.raise_for_status()
ctx_data = resp.json()
print(f"  Status: {ctx_data.get('status')}")
print(f"  Context status: {ctx_data.get('context_status')}")

# Step 3: Workspace creation
print(f"\n[Step 3] POST /api/investigation/{session_id}/workspace ...")
resp = requests.post(
    f"{BASE_URL}/api/investigation/{session_id}/workspace",
    timeout=60,
)
resp.raise_for_status()
ws_data = resp.json()
print(f"  Status: {ws_data.get('status')}")
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
summary = repo_data.get("state", {}).get("repository_summary", {})
print(f"  Languages: {summary.get('languages')}")
print(f"  Frameworks: {summary.get('frameworks')}")
print(f"  Entry points: {summary.get('entry_points')}")
print(f"  Total files: {summary.get('project_structure', {}).get('file_count')}")

# Step 5: Context Gathering
print(f"\n[Step 5] POST /api/investigation/{session_id}/context-gathering ...")
resp = requests.post(
    f"{BASE_URL}/api/investigation/{session_id}/context-gathering",
    timeout=120,
)
resp.raise_for_status()
gather_data = resp.json()
print(f"  Status: {gather_data.get('status')}")
g_ctx = gather_data.get("state", {}).get("gathered_context", {})
q_eval = g_ctx.get("quality_evaluation", {})
print(f"  Quality tier: {q_eval.get('quality_tier')}")
print(f"  Relevant files count: {len(g_ctx.get('relevant_files', []))}")
print(f"  Symptom sites count: {len(g_ctx.get('symptom_sites', []))}")
print(f"  Trace flows count: {len(g_ctx.get('trace_flows', []))}")
print(f"  Unresolved questions: {g_ctx.get('unresolved_questions', [])[:3]}")

# Step 6: Investigation Plan
print(f"\n[Step 6] POST /api/investigation/{session_id}/plan ...")
resp = requests.post(
    f"{BASE_URL}/api/investigation/{session_id}/plan",
    timeout=60,
)
resp.raise_for_status()
plan_data = resp.json()
plan_steps = plan_data.get("plan", [])
print(f"  Planned steps count: {len(plan_steps)}")
for s in plan_steps:
    print(f"    - [{s.get('step_id')}] {s.get('investigator')}: {s.get('task')}")

# Step 7: Investigation Execution (Deep Agent Orchestration)
print(f"\n[Step 7] POST /api/investigation/{session_id}/execute ...")
resp = requests.post(
    f"{BASE_URL}/api/investigation/{session_id}/execute",
    timeout=300,
)
resp.raise_for_status()
exec_data = resp.json()
print(f"  Status: {exec_data.get('status')}")
print(f"  Execution status: {exec_data.get('execution_status')}")
print(f"  Investigation status: {exec_data.get('investigation_status')}")
print(f"  Status label: {exec_data.get('status_label')}")
print(f"  Is conclusive: {exec_data.get('is_conclusive')}")

# Retrieve Final Session State
print(f"\n[Final] GET /api/investigation/{session_id} ...")
resp = requests.get(f"{BASE_URL}/api/investigation/{session_id}", timeout=60)
resp.raise_for_status()
session_record = resp.json()
final_state = session_record.get("state", {})

# Save output to session-output.json
output_file = Path("session-output.json")
with open(output_file, "w", encoding="utf-8") as out:
    json.dump(session_record, out, indent=2, ensure_ascii=False)
print(f"\nSaved updated session output to: {output_file.resolve()}")

# Print key audit metrics
print("\n" + "=" * 70)
print("FINAL AUDIT SUMMARY")
print("=" * 70)
print(f"Session ID: {session_id}")
print(f"Total findings: {len(final_state.get('findings', []))}")
print(f"Total evidence items: {len(final_state.get('evidence', []))}")
print(f"Completed tasks: {final_state.get('completed_investigation_tasks', [])}")
print(f"Skipped tasks: {final_state.get('skipped_investigation_tasks', [])}")
print(f"Stop reason: {final_state.get('investigation_stop_reason')}")

diagnosis = final_state.get("diagnosis", {})
if isinstance(diagnosis, dict):
    print(f"\nDiagnosis summary:")
    print(f"  Status: {diagnosis.get('status_label')}")
    print(f"  Confidence: {diagnosis.get('confidence')}")
    print(f"  Mechanism: {diagnosis.get('mechanism')}")
    print(f"  Write site: {diagnosis.get('write_site')}")
    print(f"  Read site: {diagnosis.get('read_site')}")
    print(f"  Query site: {diagnosis.get('query_site')}")
    print(f"  Uncertainty: {diagnosis.get('uncertainty')}")

print("\nFinished successfully.")
