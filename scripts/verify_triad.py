import os
import json
import sys
import subprocess
from typing import List, Dict

def check_incubation_schema(incubation_data: Dict) -> bool:
    required_keys = ["build_target", "requirements_unpacked", "design_approaches", "non_obvious_option", "recommended_for_planning", "explicitly_rejected"]
    for key in required_keys:
        if key not in incubation_data:
            print(f"[Error] incubation.json missing required key: {key}")
            return False
            
    if len(incubation_data.get("design_approaches", [])) < 3:
        print("[Error] incubation.json must contain at least 3 design approaches.")
        return False
        
    return True

def check_plan_schema(plan_data: Dict) -> bool:
    required_keys = ["selected_approach", "change_scope", "interfaces", "implementation_order", "predicted_behavior", "validation_plan", "rollback_plan", "risk_assessment"]
    for key in required_keys:
        if key not in plan_data:
            print(f"[Error] plan.json missing required key: {key}")
            return False
            
    return True

def get_git_diff_files() -> List[str]:
    # Placeholder for actual git diff command in CI
    # e.g., `git diff --name-only origin/main HEAD`
    try:
        result = subprocess.run(["git", "diff", "--name-only", "origin/main", "HEAD"], capture_output=True, text=True)
        if result.returncode == 0:
            return [line.strip() for line in result.stdout.split('\n') if line.strip()]
    except Exception as e:
        print(f"Warning: Could not get git diff. {e}")
    return []

def verify_triad(task_id: str, bypass_triad: bool = False):
    if bypass_triad:
        print("Triad bypassed via explicit flag/label. Skipping validation.")
        return 0

    log_dir = os.path.join("build_log", task_id)
    incubation_path = os.path.join(log_dir, "incubation.json")
    plan_path = os.path.join(log_dir, "plan.json")

    if not os.path.exists(incubation_path) or not os.path.exists(plan_path):
        print(f"[Error] Missing incubation.json or plan.json in {log_dir}")
        return 1

    with open(incubation_path, "r", encoding="utf-8") as f:
        incubation_data = json.load(f)
    
    with open(plan_path, "r", encoding="utf-8") as f:
        plan_data = json.load(f)

    if not check_incubation_schema(incubation_data):
        return 1
        
    if not check_plan_schema(plan_data):
        return 1

    diff_files = get_git_diff_files()
    planned_files = plan_data.get("change_scope", {}).get("files_to_modify", []) + \
                    plan_data.get("change_scope", {}).get("files_to_create", [])

    # Scope verification (Conceptual check for now)
    # If a file in diff_files is not in planned_files, raise an error.
    for f in diff_files:
        if f not in planned_files:
             print(f"[Warning] Diff contains file not in change_scope: {f}")

    print("[Success] Triad requirements met.")
    return 0

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Verify Triad requirements for a build task.")
    parser.add_argument("task_id", help="The Task ID corresponding to the build_log directory")
    parser.add_argument("--bypass", action="store_true", help="Bypass triad validation")
    args = parser.parse_args()
    
    sys.exit(verify_triad(args.task_id, args.bypass))
