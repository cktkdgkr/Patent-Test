import re
import logging
from typing import Tuple

logger = logging.getLogger(__name__)

class DataProtectionViolation(Exception):
    pass

class Sanitizer:
    """
    Middleware to protect PII and Financial data (0번 규정).
    Must be executed before any data is passed to LLM or Layer 2/3.
    """
    
    PII_PATTERNS = {
        "SSN_KR": r"\b\d{6}[-]*[1-4]\d{6}\b",
        "CREDIT_CARD": r"\b(?:\d[ -]*?){13,16}\b",
        "EMAIL": r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,7}\b",
        "PHONE_KR": r"\b01[016789][-.\s]?\d{3,4}[-.\s]?\d{4}\b"
    }
    
    @classmethod
    def check_violations(cls, text: str) -> Tuple[bool, str]:
        """
        Returns (has_violation, matched_category).
        If any PII/Financial data is detected, it fails closed.
        """
        for category, pattern in cls.PII_PATTERNS.items():
            if re.search(pattern, text):
                logger.error(f"Data Protection Violation detected: {category}")
                return True, category
        return False, ""

    @classmethod
    def sanitize(cls, text: str) -> str:
        """
        Throws an exception if violation is found. 
        Fail-closed mechanism to prevent any leak.
        """
        has_violation, category = cls.check_violations(text)
        if has_violation:
            raise DataProtectionViolation(f"Operation blocked. Restricted data category [{category}] found in payload.")
        return text
