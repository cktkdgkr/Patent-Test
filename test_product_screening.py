import asyncio
from pathlib import Path

from production.analyzer import RiskGrade
from production.orchestrator import ProductSpec, screen_product_against_patent_file


ROOT = Path(__file__).resolve().parent


async def main():
    product = ProductSpec.model_validate_json(
        (ROOT / "examples" / "product_alpha.json").read_text(encoding="utf-8")
    )
    report = await screen_product_against_patent_file(
        product,
        str(ROOT / "examples" / "candidate_patent_ph_overlap.txt"),
        patent_id="candidate_patent_ph_overlap",
    )
    print(report.model_dump_json(indent=2))

    assert report.product.product_id == "enzyme_product_alpha"
    assert report.patent_id == "candidate_patent_ph_overlap"
    assert report.overall_grade == RiskGrade.LOW
    assert any(result.claim_id == 2 and result.grade == RiskGrade.LOW for result in report.claim_results)
    assert any(result.claim_id == 3 and result.grade == RiskGrade.SAFE for result in report.claim_results)


if __name__ == "__main__":
    asyncio.run(main())
