from pydantic import BaseModel, Field
from typing import Optional, List

class ClaimNode(BaseModel):
    id: int = Field(description="The numeric ID of the claim")
    parent_id: Optional[int] = Field(default=None, description="The ID of the parent claim if this is a dependent claim")
    text: str = Field(description="The raw text of the claim")

class ClaimFeatures(BaseModel):
    percent_identity: Optional[float] = Field(default=None, description="Minimum percent identity required (e.g., 90.0 for 90%)")
    functional_limitations: List[str] = Field(default_factory=list, description="Extracted functional limitations (e.g., 'active at pH 7')")
    markush_structures: List[str] = Field(default_factory=list, description="Extracted Markush groups or variants")
