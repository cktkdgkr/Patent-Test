import os
import asyncio
import logging
from harness.evaluator import EvalRunner

async def main():
    print("=== [Integration Test] Running Eval Runner ===")
    try:
        report = await EvalRunner.evaluate_golden_set()
        print("\n--- Evaluation Complete ---")
        
        # Save report to JSON file for Layer 3 to read
        report_path = os.path.join("meta_harness_workspace", "eval_report.json")
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(report.model_dump_json(indent=2))
            
        print(f"Report successfully saved to {report_path}")
        print(report.model_dump_json(indent=2))

        expected_categories = {
            "percent_identity",
            "functional_claim",
            "markush",
            "variant_claim",
            "non_english",
        }
        assert report.metrics.total_cases == 6
        assert report.metrics.accuracy == 1.0
        assert not report.failed_cases
        assert expected_categories.issubset(report.metrics.per_category_accuracy)
        
    except Exception as e:
        print(f"Evaluation failed: {e}")
        raise

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    asyncio.run(main())
