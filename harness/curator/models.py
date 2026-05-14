from pydantic import BaseModel, Field
from typing import Dict, Any

class GoldenRecord(BaseModel):
    patent_id: str = Field(description="The ID of the patent")
    human_grade: str = Field(description="The final risk grade determined by a human reviewer (e.g., HIGH, SAFE)")
    human_reasoning: str = Field(description="The reasoning provided by the human reviewer")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Additional metadata such as reviewer ID, timestamp, etc.")
