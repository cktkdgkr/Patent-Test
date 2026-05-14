import os
import json
import asyncio
import logging
from harness.evaluator import EvalRunner

async def main():
    target_spec = {'identity': 85.0} 
    
    print("=== [Integration Test] Running Eval Runner ===")
    try:
        report = await EvalRunner.evaluate_golden_set(target_spec)
        print("\n--- Evaluation Complete ---")
        
        # Save report to JSON file for Layer 3 to read
        report_path = os.path.join("meta_harness_workspace", "eval_report.json")
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(report.model_dump_json(indent=2))
            
        print(f"Report successfully saved to {report_path}")
        print(report.model_dump_json(indent=2))
        
    except Exception as e:
        print(f"Evaluation failed: {e}")

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    asyncio.run(main())
