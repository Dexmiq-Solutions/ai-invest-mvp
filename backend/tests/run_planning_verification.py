"""Run live verification of the Investigation Planning Phase using the TaskFlow project and requested defect."""

import json
import os
import sys
from pathlib import Path

# Add backend directory to sys.path
backend_dir = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(backend_dir))

from fastapi.testclient import TestClient
from app.api import app

ZIP_PATH = Path("d:/MVP-Ai-software-investigation/TaskFlow.zip")
if not ZIP_PATH.exists():
    # Also check workspace root relative
    ZIP_PATH = backend_dir.parent / "TaskFlow.zip"

if not ZIP_PATH.exists():
    print(f"Error: TaskFlow.zip not found at {ZIP_PATH}", file=sys.stderr)
    sys.exit(1)

PROBLEM_STATEMENT = (
    "Tasks created or seeded as 'In progress' never appear under the 'In progress' tab, "
    "although the API returns them. Their status badge in the list is empty."
)

print("\n" + "=" * 80)
print("STARTING INVESTIGATION PLANNING VERIFICATION")
print(f"Problem: {PROBLEM_STATEMENT}")
print(f"ZIP Path: {ZIP_PATH}")
print("=" * 80 + "\n", flush=True)

client = TestClient(app)

# Step 1: Start investigation
print("--> [Step 1] Initializing Investigation Session (/api/investigation/start)...", flush=True)
with open(ZIP_PATH, "rb") as f:
    resp = client.post(
        "/api/investigation/start",
        data={"problem": PROBLEM_STATEMENT},
        files={"project_zip": ("TaskFlow.zip", f, "application/zip")},
    )

if resp.status_code != 200:
    print(f"Error in /start: {resp.status_code} - {resp.text}", file=sys.stderr)
    sys.exit(1)

start_data = resp.json()
session_id = start_data["session_id"]
print(f"    Session ID: {session_id}")
print(f"    Context Status: {start_data.get('context_status')}")
print(f"    Validation Errors: {start_data.get('validation_errors', [])}", flush=True)

# Step 2: Setup Workspace
print(f"\n--> [Step 2] Setting up Workspace (/api/investigation/{session_id}/workspace)...", flush=True)
resp = client.post(f"/api/investigation/{session_id}/workspace")
if resp.status_code != 200:
    print(f"Error in /workspace: {resp.status_code} - {resp.text}", file=sys.stderr)
    sys.exit(1)
ws_data = resp.json()
print(f"    Workspace Path: {ws_data.get('workspace_path')}", flush=True)

# Step 3: Repository Analysis
print(f"\n--> [Step 3] Analyzing Repository (/api/investigation/{session_id}/repository-analysis)...", flush=True)
resp = client.post(f"/api/investigation/{session_id}/repository-analysis")
if resp.status_code != 200:
    print(f"Error in /repository-analysis: {resp.status_code} - {resp.text}", file=sys.stderr)
    sys.exit(1)
repo_data = resp.json()
repo_summary = repo_data.get("state", {}).get("repository_summary", {})
print(f"    Languages: {repo_summary.get('languages')}")
print(f"    Frameworks: {repo_summary.get('frameworks')}")
print(f"    File count: {repo_summary.get('project_structure', {}).get('file_count')}", flush=True)

# Step 4: Context Gathering
print(f"\n--> [Step 4] Gathering Context (/api/investigation/{session_id}/context-gathering)...", flush=True)
resp = client.post(f"/api/investigation/{session_id}/context-gathering")
if resp.status_code != 200:
    print(f"Error in /context-gathering: {resp.status_code} - {resp.text}", file=sys.stderr)
    sys.exit(1)
gather_data = resp.json()
g_ctx = gather_data.get("state", {}).get("gathered_context", {})
print(f"    Relevant files count: {len(g_ctx.get('relevant_files', []))}")
for rf in g_ctx.get("relevant_files", [])[:5]:
    path_val = rf.get("path") if isinstance(rf, dict) else rf
    print(f"      - {path_val}")
print(f"    Trace flows count: {len(g_ctx.get('trace_flows', []))}", flush=True)

# Step 5: Investigation Planning Phase
print(f"\n--> [Step 5] Triggering Planning Phase (/api/investigation/{session_id}/plan)...", flush=True)
resp = client.post(f"/api/investigation/{session_id}/plan")
if resp.status_code != 200:
    print(f"Error in /plan: {resp.status_code} - {resp.text}", file=sys.stderr)
    sys.exit(1)

plan_data = resp.json()
print("\n" + "=" * 80)
print("INVESTIGATION PLANNING RESPONSE RECEIVED FROM API")
print("=" * 80)
print(f"Plan ID: {plan_data.get('plan_id')}")
print(f"Lifecycle Status: {plan_data.get('lifecycle_status')}")
print(f"Selected Investigators: {plan_data.get('selected_investigators')}")
print(f"Plan Step Count: {len(plan_data.get('investigation_plan', []))}")
print(f"Plan Validation Valid: {plan_data.get('plan_validation', {}).get('is_valid')}")
print(f"Plan Validation Errors: {plan_data.get('plan_validation', {}).get('errors', [])}")
print(f"Plan Validation Warnings: {plan_data.get('plan_validation', {}).get('warnings', [])}")
print("=" * 80 + "\n", flush=True)
