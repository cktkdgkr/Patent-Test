from production.retriever import retrieve_patent
from core.sanitizer import DataProtectionViolation

print("=== [Smoke Test] Testing Clean Patent ===")
try:
    clean_text = retrieve_patent("mock_enzyme_001")
    print(f"Success! Retrieved text:\n{clean_text}\n")
except Exception as e:
    print(f"Failed: {e}\n")

print("=== [Smoke Test] Testing Malicious Patent ===")
try:
    retrieve_patent("mock_malicious_001")
    print("FAILED: Malicious patent was loaded without exception!")
except DataProtectionViolation as e:
    print(f"SUCCESS: Caught violation! Error details:\n{e}\n")
