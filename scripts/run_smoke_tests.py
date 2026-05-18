import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TESTS = [
    "test_retriever.py",
    "test_parser.py",
    "test_analyzer.py",
    "test_curator.py",
    "test_v3_controls.py",
    "test_orchestrator.py",
    "test_evaluator.py",
    "test_product_screening.py",
    "test_excel_batch_screening.py",
    "test_sequence_analysis.py",
    "test_sequence_product_screening.py",
    "test_web_fetcher.py",
    "test_ui_server.py",
]


def main() -> int:
    for test_file in TESTS:
        print(f"\n=== Running {test_file} ===", flush=True)
        result = subprocess.run(
            [sys.executable, "-u", test_file],
            cwd=ROOT,
            check=False,
        )
        if result.returncode != 0:
            print(f"\nFAILED: {test_file} exited with {result.returncode}", flush=True)
            return result.returncode
    print("\nAll smoke tests passed.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
