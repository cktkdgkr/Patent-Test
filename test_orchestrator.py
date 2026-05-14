import asyncio
from production.orchestrator import run_screening_pipeline

async def main():
    target_spec = {'identity': 85.0}  # Safe case
    
    print("=== Integration Test: Running full pipeline for mock_enzyme_001 ===")
    try:
        report = await run_screening_pipeline("mock_enzyme_001", target_spec)
        print("\n--- Final Output ---")
        print(report.model_dump_json(indent=2))
    except Exception as e:
        print(f"Pipeline execution failed: {e}")

if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    asyncio.run(main())
