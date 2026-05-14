import logging
import re
from typing import Any, Tuple


logger = logging.getLogger(__name__)


class DataProtectionViolation(Exception):
    pass


class Sanitizer:
    """
    Fail-closed middleware for Rule 0 data protection.
    It must run before LLM calls, filesystem trace writes, and Layer 2/3 handoff.
    """

    PII_PATTERNS = {
        "SSN_KR": r"\b\d{6}[-]*[1-4]\d{6}\b",
        "PASSPORT_LIKE": r"\b(?:M|S|R|D)\d{8}\b",
        "DRIVER_LICENSE_KR": r"\b\d{2}[- ]?\d{2}[- ]?\d{6}[- ]?\d{2}\b",
        "CREDIT_CARD": r"\b(?:\d[ -]*?){13,16}\b",
        "BANK_ACCOUNT_LIKE": r"\b\d{2,6}[- ]\d{2,6}[- ]\d{4,8}\b",
        "EMAIL": r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,7}\b",
        "PHONE_KR": r"\b01[016789][-.\s]?\d{3,4}[-.\s]?\d{4}\b",
        "US_SSN": r"\b\d{3}-\d{2}-\d{4}\b",
        "DATE_OF_BIRTH_MARKER": r"\b(?:date of birth|dob|birthdate|생년월일)\b",
        "MEDICAL_RECORD_MARKER": r"\b(?:medical record|patient id|진료 기록|환자번호)\b",
        "HR_PRIVATE_MARKER": r"\b(?:salary|payroll|performance review|인사 평가|급여)\b",
    }

    @classmethod
    def check_violations(cls, text: str) -> Tuple[bool, str]:
        """
        Returns (has_violation, matched_category).
        If restricted data is detected, the caller must fail closed.
        """
        if not text:
            return False, ""
        for category, pattern in cls.PII_PATTERNS.items():
            if re.search(pattern, text, flags=re.IGNORECASE):
                logger.error("Data protection violation detected: %s", category)
                return True, category
        return False, ""

    @classmethod
    def sanitize(cls, text: str) -> str:
        has_violation, category = cls.check_violations(text)
        if has_violation:
            raise DataProtectionViolation(
                f"Operation blocked. Restricted data category [{category}] found in payload."
            )
        return text

    @classmethod
    def sanitize_payload(cls, payload: Any) -> Any:
        """
        Recursively validates structured payloads and returns the same shape.
        The harness blocks rather than redacts because the domain has no need
        to process PII, financial, HR, or medical data.
        """
        if isinstance(payload, str):
            return cls.sanitize(payload)
        if isinstance(payload, dict):
            return {
                cls.sanitize(str(key)): cls.sanitize_payload(value)
                for key, value in payload.items()
            }
        if isinstance(payload, (list, tuple)):
            return [cls.sanitize_payload(value) for value in payload]
        return payload
