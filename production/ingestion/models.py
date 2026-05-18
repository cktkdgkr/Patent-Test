from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class PatentCandidate(BaseModel):
    candidate_id: str = Field(description="Stable row-level candidate identifier")
    patent_id: Optional[str] = Field(default=None, description="Patent ID or local mock patent ID")
    title: Optional[str] = None
    applicant: Optional[str] = None
    publication_number: Optional[str] = None
    application_number: Optional[str] = None
    patent_file: Optional[str] = Field(default=None, description="Local candidate patent text path")
    claim_text: Optional[str] = Field(default=None, description="Raw claim text from the uploaded spreadsheet")
    abstract: Optional[str] = None
    keywords: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    def display_patent_id(self) -> str:
        return self.patent_id or self.publication_number or self.application_number or self.candidate_id
