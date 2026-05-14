import os
from datetime import datetime

from harness.curator import GoldenRecord, GoldenSetManager


print("=== [Smoke Test] Appending to Golden Set ===")

record1 = GoldenRecord(
    patent_id="mock_enzyme_001",
    human_grade="HIGH",
    human_reasoning="Human confirmed 95% identity is a clear infringement.",
    category="percent_identity",
    target_spec={"identity": 85.0},
    metadata={"reviewer_id": "labeler_a", "time": str(datetime.now())},
)

record2 = GoldenRecord(
    patent_id="mock_enzyme_002",
    human_grade="SAFE",
    human_reasoning="Human confirmed pH range does not overlap.",
    category="functional_claim",
    target_spec={"identity": 70.0},
    metadata={"reviewer_id": "labeler_b", "time": str(datetime.now())},
)

try:
    temp_root = os.path.join(os.getcwd(), "build_log")
    path = os.path.join(temp_root, "curator_test_tmp.jsonl")
    if os.path.exists(path):
        os.remove(path)

    GoldenSetManager.append_to_golden_set(record1, master_file=path)
    GoldenSetManager.append_to_golden_set(record2, master_file=path)

    with open(path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    os.remove(path)

    assert len(lines) == 2
    print("Success! Two records appended to a temporary golden set.")
except Exception as e:
    print(f"Failed: {e}")
    raise
