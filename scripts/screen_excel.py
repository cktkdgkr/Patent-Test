import argparse
import asyncio
import csv
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from production.ingestion import read_patent_candidates
from production.orchestrator import ProductSpec, screen_product_candidate_batch


async def _run(args: argparse.Namespace) -> int:
    product_path = _resolve(args.product)
    candidates_path = _resolve(args.excel)
    product = ProductSpec(**json.loads(product_path.read_text(encoding="utf-8")))
    candidates = read_patent_candidates(str(candidates_path))
    report = await screen_product_candidate_batch(product, candidates, str(candidates_path))

    output_path = _resolve(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")

    if args.summary_csv:
        _write_summary_csv(report, _resolve(args.summary_csv))

    print(f"Product: {report.product.product_id}")
    print(f"Candidates: {report.total_candidates}")
    print(f"Screened: {report.screened_count}")
    print(f"Failed: {len(report.failed_candidates)}")
    print(f"Summary: {report.summary_by_grade}")
    print(f"Report: {output_path}")
    return 0 if not report.failed_candidates else 2


def _resolve(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def _write_summary_csv(report, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "patent_id",
                "source",
                "overall_grade",
                "overall_confidence",
                "claim_count",
                "design_around_count",
                "overall_reasoning",
            ],
        )
        writer.writeheader()
        for item in report.reports:
            writer.writerow(
                {
                    "patent_id": item.patent_id,
                    "source": item.source,
                    "overall_grade": item.overall_grade.value,
                    "overall_confidence": item.overall_confidence,
                    "claim_count": len(item.claim_results),
                    "design_around_count": len(item.design_around_options),
                    "overall_reasoning": item.overall_reasoning,
                }
            )


def main() -> int:
    parser = argparse.ArgumentParser(description="Screen one product against candidate patents from Excel/CSV.")
    parser.add_argument("--product", required=True, help="Path to product JSON under the repo")
    parser.add_argument("--excel", required=True, help="Path to .xlsx or .csv patent candidate file")
    parser.add_argument(
        "--output",
        default="build_log/batch_screening_report.json",
        help="Path for the JSON batch report",
    )
    parser.add_argument(
        "--summary-csv",
        default="build_log/batch_screening_summary.csv",
        help="Path for the CSV summary",
    )
    return asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
