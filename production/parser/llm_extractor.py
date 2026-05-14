import logging
import json
from google import genai
from pydantic import BaseModel
from production.parser.models import ClaimNode, ClaimFeatures

logger = logging.getLogger(__name__)

# Initialize client. It will automatically pick up GEMINI_API_KEY from environment.
try:
    client = genai.Client()
except Exception as e:
    logger.warning(f"Could not initialize genai client. GEMINI_API_KEY may not be set: {e}")
    client = None

class LLMExtractor:
    """
    Extracts structured features from a single claim using Gemini 1.5 Flash.
    """
    
    @classmethod
    async def extract_features(cls, claim_node: ClaimNode) -> ClaimFeatures:
        if not client:
            raise RuntimeError("GEMINI_API_KEY is missing. Cannot run LLM extractor.")
            
        logger.info(f"Extracting features for claim {claim_node.id} via Gemini Flash...")
        
        prompt = f"""
        Extract key functional features from the following patent claim text.
        If a specific percent identity or homology is mentioned (e.g. 'at least 90% identity'), output that number.
        Otherwise, output null for percent_identity.
        Extract any functional limitations (e.g. pH ranges, temperature ranges) into the list.
        
        Claim text:
        {claim_node.text}
        """
        
        try:
            response = await client.aio.models.generate_content(
                model='gemini-2.5-flash',
                contents=prompt,
                config={
                    'response_mime_type': 'application/json',
                    'response_schema': ClaimFeatures,
                    'temperature': 0.0
                }
            )
            
            if response.parsed:
                return response.parsed
            else:
                data = json.loads(response.text)
                return ClaimFeatures(**data)
                
        except Exception as e:
            logger.error(f"LLM API Error during extraction: {e}")
            raise
