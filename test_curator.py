from harness.curator import GoldenRecord, GoldenSetManager
from datetime import datetime

print("=== [Smoke Test] Appending to Golden Set ===")

record1 = GoldenRecord(
    patent_id="mock_enzyme_001",
    human_grade="HIGH",
    human_reasoning="Human confirmed 95% identity is a clear infringement.",
    metadata={"reviewer": "Alice", "time": str(datetime.now())}
)

record2 = GoldenRecord(
    patent_id="mock_enzyme_002",
    human_grade="SAFE",
    human_reasoning="Human confirmed pH range does not overlap.",
    metadata={"reviewer": "Bob", "time": str(datetime.now())}
)

try:
    GoldenSetManager.append_to_golden_set(record1)
    GoldenSetManager.append_to_golden_set(record2)
    print("Success! Two records appended.")
    
    # Verify the file contents
    import os
    path = os.path.join("meta_harness_workspace", "protected", "golden_set", "master.jsonl")
    with open(path, "r", encoding="utf-8") as f:
        lines = f.readlines()
        print(f"\nLines in master.jsonl: {len(lines)}")
        for i, line in enumerate(lines[-2:]):
            print(f"Record {i+1}: {line.strip()}")
            
except Exception as e:
    print(f"Failed: {e}")
