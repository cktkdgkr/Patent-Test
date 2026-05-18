import asyncio

from production.analyzer import RiskGrade
from production.orchestrator import ProductSpec, screen_product_against_patent_text


async def main():
    product = ProductSpec(
        product_id="sequence_product",
        enzyme_name="Sequence enzyme",
        amino_acid_sequence="MKTAYIAKQRQISFVKSHFSRQEILDLIC",
        variant="A10V",
        ph=7.0,
        temperature_c=45.0,
        substrate="protein substrate",
        enzyme_class="protease",
    )
    patent_text = """
    1. A variant enzyme having at least 80% identity to SEQ ID NO:1.
    2. The variant enzyme of claim 1, wherein the variant comprises A10V substitution.
    SEQ ID NO:1: MKTAYIAKQRQISFVKSHFSRQDILDLIC
    """
    report = await screen_product_against_patent_text(
        product=product,
        patent_id="sequence_patent",
        source="inline_test",
        raw_text=patent_text,
    )
    print(report.model_dump_json(indent=2))
    assert report.overall_grade == RiskGrade.HIGH
    claim_1 = report.claim_results[0]
    assert claim_1.sequence_comparisons
    assert claim_1.sequence_comparisons[0].identity > 95.0
    assert claim_1.sequence_comparisons[0].threshold_met is True
    assert any(option.strategy_type == "sequence_identity_design_space" for option in report.design_around_options)


if __name__ == "__main__":
    asyncio.run(main())
