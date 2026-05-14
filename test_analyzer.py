import asyncio
from production.parser.models import ClaimFeatures
from production.analyzer.evaluator import RiskAnalyzer

async def main():
    features = [
        ClaimFeatures(percent_identity=90.0, functional_limitations=[]),
        ClaimFeatures(percent_identity=95.0, functional_limitations=[])
    ]
    
    target_spec_high = {'identity': 92.0}
    target_spec_safe = {'identity': 85.0}
    
    print("=== Testing HIGH risk case ===")
    report_high = await RiskAnalyzer.analyze_risk(features, target_spec_high)
    print(report_high.model_dump_json(indent=2))
    
    print("\n=== Testing SAFE risk case ===")
    report_safe = await RiskAnalyzer.analyze_risk(features, target_spec_safe)
    print(report_safe.model_dump_json(indent=2))

if __name__ == "__main__":
    asyncio.run(main())
