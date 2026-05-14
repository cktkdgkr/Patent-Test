from enum import Enum
from pydantic import BaseModel, Field

class RiskGrade(str, Enum):
    HIGH = 'HIGH'
    MEDIUM = 'MEDIUM'
    LOW = 'LOW'
    SAFE = 'SAFE'

class RiskReport(BaseModel):
    reasoning: str = Field(description="Chain of Thought reasoning explaining why this grade was chosen.")
    grade: RiskGrade = Field(description="The final computed risk grade.")
