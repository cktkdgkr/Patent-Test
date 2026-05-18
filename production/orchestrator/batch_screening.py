from pathlib import Path
from typing import Dict, List, Optional

from pydantic import BaseModel, Field

from core.access_control import Layer
from core.trace_logger import TraceLogger
from production.analyzer import RiskGrade
from production.ingestion import PatentCandidate
from production.orchestrator.product_screening import (
    ProductPatentScreeningReport,
    ProductSpec,
    screen_product_against_patent_file,
    screen_product_against_patent_id,
    screen_product_against_patent_text,
)


class CandidateScreeningFailure(BaseModel):
    candidate_id: str
    reason: str


class ProductBatchScreeningReport(BaseModel):
    product: ProductSpec
    source_file: str
    total_candidates: int
    screened_count: int
    failed_candidates: List[CandidateScreeningFailure] = Field(default_factory=list)
    reports: List[ProductPatentScreeningReport] = Field(default_factory=list)
    summary_by_grade: Dict[str, int] = Field(default_factory=dict)
    run_id: str


async def screen_product_candidate_batch(
    product: ProductSpec,
    candidates: List[PatentCandidate],
    source_file: str,
    run_id: Optional[str] = None,
) -> ProductBatchScreeningReport:
    run_id = run_id or TraceLogger.start_run("excel_batch_screening")
    reports: List[ProductPatentScreeningReport] = []
    failures: List[CandidateScreeningFailure] = []

    for candidate in candidates:
        try:
            reports.append(await _screen_candidate(product, candidate, run_id, source_file))
        except Exception as exc:
            failures.append(
                CandidateScreeningFailure(
                    candidate_id=candidate.candidate_id,
                    reason=str(exc),
                )
            )

    summary: Dict[str, int] = {grade.value: 0 for grade in RiskGrade}
    for report in reports:
        summary[report.overall_grade.value] += 1

    batch_report = ProductBatchScreeningReport(
        product=product,
        source_file=source_file,
        total_candidates=len(candidates),
        screened_count=len(reports),
        failed_candidates=failures,
        reports=reports,
        summary_by_grade=summary,
        run_id=run_id,
    )

    TraceLogger.write_event(
        run_id=run_id,
        patent_id="_batch",
        agent_name="excel_batch_screening",
        event_name="batch_report",
        payload=batch_report.model_dump(),
        layer=Layer.PRODUCTION,
    )
    return batch_report


async def _screen_candidate(
    product: ProductSpec,
    candidate: PatentCandidate,
    run_id: str,
    source_file: str,
) -> ProductPatentScreeningReport:
    candidate_id = candidate.display_patent_id()
    if candidate.claim_text:
        return await screen_product_against_patent_text(
            product=product,
            patent_id=candidate_id,
            source="spreadsheet_claim_text",
            raw_text=candidate.claim_text,
            run_id=run_id,
        )
    if candidate.patent_file:
        patent_file = Path(candidate.patent_file)
        if not patent_file.is_absolute():
            patent_file = Path(source_file).resolve().parent / patent_file
        return await screen_product_against_patent_file(
            product=product,
            file_path=str(patent_file),
            patent_id=candidate_id,
            run_id=run_id,
        )
    if candidate.patent_id:
        return await screen_product_against_patent_id(
            product=product,
            patent_id=candidate.patent_id,
            run_id=run_id,
        )
    raise ValueError("Candidate row has no claim_text, patent_file, or patent_id")
