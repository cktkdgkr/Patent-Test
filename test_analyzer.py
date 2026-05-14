import asyncio
from production.parser.models import ClaimFeatures
from production.analyzer.evaluator import RiskAnalyzer
from production.analyzer.models import RiskGrade

async def main():
    features = [
        ClaimFeatures(percent_identity=90.0, functional_limitations=[]),
        ClaimFeatures(percent_identity=95.0, functional_limitations=[])
    ]
    
    target_spec_high = {'identity': 92.0}
    target_spec_buffer = {'identity': 85.0}
    functional_features = [
        ClaimFeatures(percent_identity=None, functional_limitations=["pH 9"])
    ]
    
    print("=== Testing HIGH risk case ===")
    report_high = await RiskAnalyzer.analyze_risk(features, target_spec_high)
    print(report_high.model_dump_json(indent=2))
    assert report_high.grade == RiskGrade.HIGH
    
    print("\n=== Testing recall buffer case ===")
    report_buffer = await RiskAnalyzer.analyze_risk(features, target_spec_buffer)
    print(report_buffer.model_dump_json(indent=2))
    assert report_buffer.grade == RiskGrade.HIGH

    print("\n=== Testing functional limitation without target overlap evidence ===")
    report_no_functional_target = await RiskAnalyzer.analyze_risk(
        functional_features,
        {"identity": 70.0},
    )
    print(report_no_functional_target.model_dump_json(indent=2))
    assert report_no_functional_target.grade == RiskGrade.SAFE

    print("\n=== Testing functional pH overlap ===")
    report_ph_overlap = await RiskAnalyzer.analyze_risk(functional_features, {"ph": 9.0})
    print(report_ph_overlap.model_dump_json(indent=2))
    assert report_ph_overlap.grade == RiskGrade.LOW

    print("\n=== Testing functional pH non-overlap ===")
    report_ph_nonoverlap = await RiskAnalyzer.analyze_risk(functional_features, {"ph": 7.0})
    print(report_ph_nonoverlap.model_dump_json(indent=2))
    assert report_ph_nonoverlap.grade == RiskGrade.SAFE

if __name__ == "__main__":
    asyncio.run(main())
