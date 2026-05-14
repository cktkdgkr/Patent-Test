from typing import Dict, List

from pydantic import BaseModel, Field


class EvalMetrics(BaseModel):
    total_cases: int = Field(description="Total number of evaluated cases")
    positive_cases: int = Field(default=0, description="Human HIGH/MEDIUM cases requiring recall")
    true_positives: int = Field(default=0, description="Positive cases predicted as HIGH/MEDIUM/LOW")
    false_positives: int = Field(default=0, description="Human SAFE cases predicted as risky")
    false_negatives: int = Field(default=0, description="Positive cases predicted as SAFE")
    accuracy: float = Field(default=0.0, description="Overall exact grade match")
    recall: float = Field(default=0.0, description="Recall over human HIGH/MEDIUM cases")
    precision: float = Field(default=0.0, description="Precision over predicted risky cases")
    critical_misses: int = Field(default=0, description="Count of human HIGH cases predicted SAFE")
    ece: float = Field(default=0.0, description="Expected calibration error")
    per_category_recall: Dict[str, float] = Field(default_factory=dict)
    data_protection_violations: int = Field(default=0)
    context_cost: float = Field(default=0.0)
    latency_ms: float = Field(default=0.0)
    reasoning_quality: float = Field(default=0.0)


class EvalFailedCase(BaseModel):
    patent_id: str
    category: str = "uncategorized"
    human_grade: str
    machine_grade: str
    machine_confidence: float = 0.0
    machine_reasoning: str
    is_critical: bool
    is_false_negative: bool = False


class EvalReport(BaseModel):
    metrics: EvalMetrics
    failed_cases: List[EvalFailedCase]
