import os
import logging
from core.sanitizer import Sanitizer, DataProtectionViolation

logger = logging.getLogger(__name__)

# Base path relative to this script
BASE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data", "mock_patents")

def retrieve_patent(patent_id: str) -> str:
    """
    Retrieves a patent by ID from the mock directory and sanitizes it.
    Throws FileNotFoundError if not found.
    Throws DataProtectionViolation if PII is detected.
    """
    file_path = os.path.join(BASE_DIR, f"{patent_id}.txt")
    
    if not os.path.exists(file_path):
        logger.error(f"Patent ID {patent_id} not found.")
        raise FileNotFoundError(f"Patent {patent_id} not found in {BASE_DIR}")
        
    with open(file_path, 'r', encoding='utf-8') as f:
        raw_text = f.read()
        
    # RULE 0: MANDATORY SANITIZATION
    logger.info(f"Passing patent {patent_id} through Sanitizer...")
    clean_text = Sanitizer.sanitize(raw_text)
    logger.info(f"Patent {patent_id} successfully sanitized.")
    
    return clean_text
