import re
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


_COUNTRY_PREFIX_RE = re.compile(r"^([A-Z]{2})\d")
_COUNTRY_CODE_RE = re.compile(r"^[A-Za-z]{2}$")


class PatentCandidate(BaseModel):
    candidate_id: str = Field(description="Stable row-level candidate identifier")
    patent_id: Optional[str] = Field(default=None, description="Patent ID or local mock patent ID")
    title: Optional[str] = None
    applicant: Optional[str] = None
    country_code: Optional[str] = Field(
        default=None,
        description="ISO 2-letter office/country code (e.g. US, KR, EP, WO, JP, CN)",
    )
    publication_number: Optional[str] = None
    application_number: Optional[str] = None
    patent_file: Optional[str] = Field(default=None, description="Local candidate patent text path")
    claim_text: Optional[str] = Field(default=None, description="Raw claim text from the uploaded spreadsheet")
    abstract: Optional[str] = None
    keywords: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    def display_patent_id(self) -> str:
        return (
            self.patent_id
            or self.combined_identifier()
            or self.candidate_id
        )

    def combined_identifier(self) -> Optional[str]:
        """Return the best fetcher-ready identifier, combining `country_code` with the
        publication or application number when the number lacks a country prefix.

        Rules:
        - If publication/application number already starts with a 2-letter country
          prefix (e.g. ``US11723967``), it is returned unchanged.
        - Otherwise, when ``country_code`` is set, the cleaned country code is
          prepended to the number (e.g. ``KR`` + ``10-2020-0012345`` → ``KR1020200012345``).
        - Without a country code, the bare number is returned (legacy behavior).
        - Returns ``None`` when no publication/application number is available.
        """
        primary = self.publication_number or self.application_number
        if not primary:
            return None
        primary = primary.strip()
        if not primary:
            return None

        cleaned_number = re.sub(r"[^A-Za-z0-9]", "", primary)
        if _COUNTRY_PREFIX_RE.match(cleaned_number.upper()):
            # Already prefixed; reuse the user's spelling for display fidelity
            return primary

        cc = (self.country_code or "").strip().upper()
        if cc and _COUNTRY_CODE_RE.match(cc):
            if not cleaned_number:
                return None
            return f"{cc}{cleaned_number}"
        return primary
