import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Dict, List


ALLOWED_EXEMPTIONS = {
    "typo_doc",
    "format_only",
    "test_text_only",
    "metadata_comment",
}


def check_incubation_schema(incubation_data: Dict) -> bool:
    required_keys = [
        "build_target",
        "requirements_unpacked",
        "design_approaches",
        "non_obvious_option",
        "recommended_for_planning",
        "explicitly_rejected",
        "open_questions_for_human",
    ]
    for key in required_keys:
        if key not in incubation_data:
            print(f"[Error] incubation.json missing required key: {key}")
            return False

    approaches = incubation_data.get("design_approaches", [])
    if len(approaches) < 3:
        print("[Error] incubation.json must contain at least 3 design approaches.")
        return False

    normalized = {
        _normalize_text(
            " ".join(str(approach.get(part, "")) for part in ("name", "idea", "trade_off"))
            if isinstance(approach, dict)
            else str(approach)
        )
        for approach in approaches
    }
    if len(normalized) < 3:
        print("[Error] design approaches are not genuinely distinct.")
        return False

    forbidden_markers = ("```", "def ", "class ", "lambda ", "import ")
    incubator_blob = json.dumps(incubation_data, ensure_ascii=False)
    if any(marker in incubator_blob for marker in forbidden_markers):
        print("[Error] incubation.json appears to contain code or pseudocode.")
        return False

    return True


def check_plan_schema(plan_data: Dict) -> bool:
    required_keys = [
        "selected_approach",
        "change_scope",
        "interfaces",
        "change_atomicity",
        "implementation_order",
        "predicted_behavior",
        "validation_plan",
        "rollback_plan",
        "risk_assessment",
        "human_review_required",
    ]
    for key in required_keys:
        if key not in plan_data:
            print(f"[Error] plan.json missing required key: {key}")
            return False

    atomicity = plan_data.get("change_atomicity", {})
    if isinstance(atomicity, dict) and atomicity.get("atomic_change_count") not in (1, "1"):
        print("[Error] plan.json must describe exactly one atomic change.")
        return False

    if not plan_data.get("change_scope", {}).get("untouched_areas"):
        print("[Error] plan.json must explicitly list untouched areas.")
        return False

    if not plan_data.get("rollback_plan"):
        print("[Error] plan.json must include a rollback plan.")
        return False

    return True


def get_git_diff_files() -> List[str]:
    commands = [
        ["git", "diff", "--name-only", "origin/main", "HEAD"],
        ["git", "diff", "--name-only"],
    ]
    for command in commands:
        try:
            result = subprocess.run(command, capture_output=True, text=True, check=False)
        except Exception:
            continue
        if result.returncode == 0:
            return [line.strip().replace("\\", "/") for line in result.stdout.splitlines() if line.strip()]
    return []


def verify_triad(task_id: str, exemption_type: str = "", approved_by: str = "") -> int:
    if exemption_type:
        if exemption_type not in ALLOWED_EXEMPTIONS or not approved_by:
            print("[Error] Invalid Triad exemption. Use a registered type and approver.")
            return 1
        print(f"Triad exempted via {exemption_type}, approved by {approved_by}.")
        return 0

    log_dir = _find_build_log_dir(task_id)
    incubation_path = log_dir / "incubation.json"
    plan_path = log_dir / "plan.json"

    if not incubation_path.exists() or not plan_path.exists():
        print(f"[Error] Missing incubation.json or plan.json in {log_dir}")
        return 1

    incubation_data = json.loads(incubation_path.read_text(encoding="utf-8"))
    plan_data = json.loads(plan_path.read_text(encoding="utf-8"))

    if not check_incubation_schema(incubation_data):
        return 1
    if not check_plan_schema(plan_data):
        return 1

    diff_files = get_git_diff_files()
    planned_files = {
        path.replace("\\", "/")
        for path in (
            plan_data.get("change_scope", {}).get("files_to_modify", [])
            + plan_data.get("change_scope", {}).get("files_to_create", [])
        )
    }

    out_of_scope = []
    for diff_file in diff_files:
        if "__pycache__/" in diff_file or diff_file.endswith(".pyc"):
            continue
        if diff_file not in planned_files and f"enzyme-patent-harness/{diff_file}" not in planned_files:
            out_of_scope.append(diff_file)
    if out_of_scope:
        print("[Error] Diff contains files not listed in plan change_scope:")
        for path in out_of_scope:
            print(f"  - {path}")
        return 1

    print("[Success] Triad requirements met.")
    return 0


def _find_build_log_dir(task_id: str) -> Path:
    candidates = [
        Path("build_log") / task_id,
        Path("enzyme-patent-harness") / "build_log" / task_id,
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def _normalize_text(text: str) -> str:
    return " ".join(text.lower().split())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Verify build-time Triad requirements.")
    parser.add_argument("task_id", help="Task ID under build_log/")
    parser.add_argument("--exemption-type", default="", choices=[""] + sorted(ALLOWED_EXEMPTIONS))
    parser.add_argument("--approved-by", default="")
    args = parser.parse_args()

    sys.exit(verify_triad(args.task_id, args.exemption_type, args.approved_by))
