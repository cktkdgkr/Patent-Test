import argparse
import asyncio
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from production.orchestrator import (
    ProductSpec,
    screen_product_against_patent_file,
    screen_product_against_patent_id,
)


def _repo_root() -> Path:
    return ROOT


async def _run(args: argparse.Namespace) -> int:
    root = _repo_root()
    product_path = Path(args.product)
    if not product_path.is_absolute():
        product_path = root / product_path
    product = ProductSpec(**json.loads(product_path.read_text(encoding="utf-8")))

    if args.patent_id:
        report = await screen_product_against_patent_id(product, args.patent_id)
    else:
        patent_file = Path(args.patent_file)
        if not patent_file.is_absolute():
            patent_file = root / patent_file
        report = await screen_product_against_patent_file(
            product,
            str(patent_file),
            patent_id=args.candidate_id,
        )

    output_path = Path(args.output)
    if not output_path.is_absolute():
        output_path = root / output_path
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")

    print(f"Product: {report.product.product_id}")
    print(f"Patent: {report.patent_id}")
    print(f"Overall grade: {report.overall_grade.value}")
    print(f"Report: {output_path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Screen one product against one candidate patent.")
    parser.add_argument("--product", required=True, help="Path to product JSON under the repo")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--patent-file", help="Path to candidate patent text under the repo")
    source.add_argument("--patent-id", help="Existing mock patent ID from data/mock_patents")
    parser.add_argument("--candidate-id", help="Optional display ID for --patent-file")
    parser.add_argument(
        "--output",
        default="build_log/product_screening_report.json",
        help="Path for the JSON screening report",
    )
    return asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
