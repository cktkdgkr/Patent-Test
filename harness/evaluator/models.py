from pydantic import BaseModel, Field
from typing import List

class EvalMetrics(BaseModel):
    total_cases: int = Field(description="Total number of evaluated cases")
    accuracy: float = Field(description="Overall accuracy (0.0 to 1.0)")
    critical_misses: int = Field(description="Count of critical misses (Human: HIGH, Machine: SAFE)")

class EvalFailedCase(BaseModel):
    patent_id: str
    human_grade: str
    machine_grade: str
    machine_reasoning: str
    is_critical: bool

class EvalReport(BaseModel):
    metrics: EvalMetrics
    failed_cases: List[EvalFailedCase]
