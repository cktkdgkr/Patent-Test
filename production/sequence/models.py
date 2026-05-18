from typing import List, Optional

from pydantic import BaseModel, Field


class SequenceAlignmentResult(BaseModel):
    seq_id: str
    status: str = Field(description="matched, missing_reference_sequence, no_product_sequence, or no_seq_id_reference")
    identity: Optional[float] = None
    coverage: Optional[float] = None
    target_length: int = 0
    reference_length: int = 0
    matches: int = 0
    substitutions: List[str] = Field(default_factory=list)
    deletions: List[str] = Field(default_factory=list)
    insertions: List[str] = Field(default_factory=list)
    threshold: Optional[float] = None
    threshold_met: Optional[bool] = None
    reasoning: str
