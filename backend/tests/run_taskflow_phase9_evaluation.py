"""Run TaskFlow investigation through Phase 9 (Diagnosis & Verification) and evaluate output."""

import json
import sys
from pathlib import Path

# Add backend directory to sys.path
backend_dir = Path(__file__).resolve().parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from app.graph.graph import build_graph

def run_taskflow_evaluation():
    zip_path = Path("..") / "TaskFlow.zip"
    if not zip_path.resolve().exists():
        zip_path = Path("TaskFlow.zip")
    
    resolved_zip = str(zip_path.resolve())
    print(f"Using TaskFlow ZIP: {resolved_zip}")

    problem = (
        "Task creation fails when creating tasks with due dates in taskModel.js. "
        "Parameters are passed out of order."
    )
    problem_context = {
        "observed_behavior": "Creating a task with a due date either fails or associates the task with an incorrect/swapped user ID.",
        "expected_behavior": "Tasks with due dates are inserted into SQLite with correct due_date and user_id values.",
        "error": "SQL parameter mismatch or foreign key constraint error",
        "target_areas": ["taskModel.js", "tasks", "insertWithDue"],
    }

    initial_state = {
        "problem": problem,
        "project_path": resolved_zip,
        "problem_context": problem_context,
        "run_diagnosis": True,
    }

    print("\nStarting pipeline graph with run_diagnosis=True...")
    graph = build_graph()
    result = graph.invoke(initial_state)

    print("\n" + "=" * 70)
    print("TASKFLOW PHASE 9 EVALUATION RESULTS")
    print("=" * 70)
    print(f"Current step: {result.get('current_step')}")
    print(f"Status label: {result.get('status_label')}")
    print(f"Is conclusive: {result.get('is_conclusive')}")

    diagnosis = result.get("diagnosis", {})
    print(f"\nDiagnosis Root Cause: {diagnosis.get('root_cause')}")
    print(f"Diagnosis Status Label: {diagnosis.get('status_label')}")
    print(f"Diagnosis Confidence: {diagnosis.get('confidence')}")

    print("\nSuggested Fix:")
    print(result.get("suggested_fix"))

    print("\nVerification Steps:")
    for step in result.get("verification_steps", []):
        print(f"  - {step}")

    print("\nVerification Summary:")
    print(json.dumps(result.get("verification_summary", {}), indent=2))

    report = result.get("final_report", "")
    print("\n" + "=" * 70)
    print("FINAL INVESTIGATION REPORT (MARKDOWN)")
    print("=" * 70)
    print(report)

    # Save to file
    out_path = Path("taskflow_phase9_report.md")
    out_path.write_text(report, encoding="utf-8")
    print(f"\nReport saved to: {out_path.resolve()}")

    # Validations
    assert "1. Problem Summary" in report, "Section 1 missing"
    assert "2. Root-Cause Diagnosis" in report, "Section 2 missing"
    assert "3. Supporting Evidence" in report, "Section 3 missing"
    assert "4. Affected Components" in report, "Section 4 missing"
    assert "5. Suggested Fix" in report, "Section 5 missing"
    assert "6. Verification Steps" in report, "Section 6 missing"
    assert "7. Remaining Uncertainty" in report, "Section 7 missing"
    print("\n>>> All 7 sections verified in final report! <<<")

if __name__ == "__main__":
    run_taskflow_evaluation()
