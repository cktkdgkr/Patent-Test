import logging
import json
from typing import List
from google import genai
from production.parser.models import ClaimFeatures
from production.analyzer.models import RiskReport, RiskGrade

logger = logging.getLogger(__name__)

try:
    client = genai.Client()
except Exception as e:
    logger.warning(f"Could not initialize genai client: {e}")
    client = None

class RiskAnalyzer:
    """
    Evaluates extracted patent features against a target product specification
    to determine the infringement risk using Gemini 1.5 Pro.
    """
    
    @classmethod
    async def analyze_risk(cls, features: List[ClaimFeatures], target_spec: dict) -> RiskReport:
        if not client:
            raise RuntimeError("GEMINI_API_KEY is missing. Cannot run Risk Analyzer.")
            
        logger.info("Sending features and target_spec to Gemini Pro for reasoning...")
        
        features_json = [f.model_dump() for f in features]
        
        prompt = f"""
        You are an expert patent attorney analyzing infringement risk.
        Compare the extracted patent claim features against the target product specification.
        
        Extracted Claim Features:
        {json.dumps(features_json, indent=2)}
        
        Target Product Specification:
        {json.dumps(target_spec, indent=2)}
        
        Step 1. Provide a detailed step-by-step reasoning (Chain of Thought) comparing the features.
        If the target specification falls within the patent's claimed ranges (e.g., target identity is >= patent identity), it is a HIGH risk.
        Otherwise, if the target is completely outside the claimed scope, it is SAFE.
        Step 2. Determine the final risk grade (HIGH, MEDIUM, LOW, SAFE).
        """
        
        try:
            response = await client.aio.models.generate_content(
                model='gemini-2.5-pro',
                contents=prompt,
                config={
                    'response_mime_type': 'application/json',
                    'response_schema': RiskReport,
                    'temperature': 0.0
                }
            )
            
            if response.parsed:
                return response.parsed
            else:
                data = json.loads(response.text)
                return RiskReport(**data)
                
        except Exception as e:
            logger.error(f"LLM API Error during risk analysis: {e}")
            raise
