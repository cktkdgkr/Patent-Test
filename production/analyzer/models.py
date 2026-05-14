from enum import Enum
from pydantic import BaseModel, Field

class RiskGrade(str, Enum):
    HIGH = 'HIGH'
    MEDIUM = 'MEDIUM'
    LOW = 'LOW'
    SAFE = 'SAFE'

class RiskReport(BaseModel):
    reasoning: str = Field(description="Concise audit rationale explaining why this grade was chosen.")
    grade: RiskGrade = Field(description="The final computed risk grade.")
    confidence: float = Field(default=0.5, ge=0.0, le=1.0, description="Calibratable confidence for the final grade.")
