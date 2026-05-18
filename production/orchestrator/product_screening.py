import asyncio
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from core.access_control import Layer
from core.sanitizer import Sanitizer
from core.trace_logger import TraceLogger
from production.analyzer import RiskAnalyzer, RiskGrade, RiskReport
from production.parser import ClaimFeatures, LLMExtractor, TreeBuilder
from production.retriever import retrieve_patent, retrieve_patent_from_file


class ProductSpec(BaseModel):
    product_id: str = Field(description="Internal product or project identifier")
    enzyme_name: Optional[str] = None
    identity: Optional[float] = Field(default=None, description="Sequence identity percentage")
    ph: Optional[float] = Field(default=None, description="Operating pH")
    temperature_c: Optional[float] = None
    substrate: Optional[str] = None
    enzyme_class: Optional[str] = None
    variant: Optional[str] = None
    jurisdiction: Optional[str] = None
    launch_date: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

    def to_target_spec(self) -> Dict[str, Any]:
        data = self.model_dump(exclude_none=True)
        data.pop("product_id", None)
        data.pop("metadata", None)
        return data


class ClaimScreeningResult(BaseModel):
    claim_id: int
    parent_id: Optional[int] = None
    claim_text: str
    features: ClaimFeatures
    grade: RiskGrade
    confidence: float
    reasoning: str
    overlap_signals: List[str] = Field(default_factory=list)


class DesignAroundOption(BaseModel):
    strategy_type: str
    claim_id: int
    basis: str
    proposed_direction: str
    verification_needed: str = "Requires patent counsel and experimental feasibility review."


class ProductPatentScreeningReport(BaseModel):
    product: ProductSpec
    patent_id: str
    source: str
    overall_grade: RiskGrade
    overall_confidence: float
    overall_reasoning: str
    claim_results: List[ClaimScreeningResult]
    design_around_options: List[DesignAroundOption] = Field(default_factory=list)
    recommended_next_actions: List[str]
    run_id: str


async def screen_product_against_patent_id(
    product: ProductSpec,
    patent_id: str,
    run_id: Optional[str] = None,
) -> ProductPatentScreeningReport:
    run_id = run_id or TraceLogger.start_run("product_screening")
    raw_text = retrieve_patent(patent_id, run_id=run_id)
    return await screen_product_against_patent_text(
        product=product,
        patent_id=patent_id,
        source="mock_patents",
        raw_text=raw_text,
        run_id=run_id,
    )


async def screen_product_against_patent_file(
    product: ProductSpec,
    file_path: str,
    patent_id: Optional[str] = None,
    run_id: Optional[str] = None,
) -> ProductPatentScreeningReport:
    run_id = run_id or TraceLogger.start_run("product_screening")
    candidate_id = patent_id or Path(file_path).stem
    raw_text = retrieve_patent_from_file(file_path, patent_id=candidate_id, run_id=run_id)
    return await screen_product_against_patent_text(
        product=product,
        patent_id=candidate_id,
        source=file_path,
        raw_text=raw_text,
        run_id=run_id,
    )


async def screen_product_against_patent_text(
    product: ProductSpec,
    patent_id: str,
    source: str,
    raw_text: str,
    run_id: Optional[str] = None,
) -> ProductPatentScreeningReport:
    run_id = run_id or TraceLogger.start_run("product_screening")
    clean_product = ProductSpec(**Sanitizer.sanitize_payload(product.model_dump()))
    clean_text = Sanitizer.sanitize(raw_text)
    target_spec = clean_product.to_target_spec()

    claim_nodes = TreeBuilder.parse_claims(clean_text)
    features_list = await asyncio.gather(
        *[
            LLMExtractor.extract_features(node, run_id=run_id, patent_id=patent_id)
            for node in claim_nodes
        ]
    )

    claim_results: List[ClaimScreeningResult] = []
    for node, features in zip(claim_nodes, features_list):
        report = await RiskAnalyzer.analyze_risk([features], target_spec)
        claim_results.append(
            ClaimScreeningResult(
                claim_id=node.id,
                parent_id=node.parent_id,
                claim_text=node.text,
                features=features,
                grade=report.grade,
                confidence=report.confidence,
                reasoning=report.reasoning,
                overlap_signals=_overlap_signals(features, target_spec),
            )
        )

    overall_report = _overall_report(claim_results)
    screening_report = ProductPatentScreeningReport(
        product=clean_product,
        patent_id=patent_id,
        source=source,
        overall_grade=overall_report.grade,
        overall_confidence=overall_report.confidence,
        overall_reasoning=overall_report.reasoning,
        claim_results=claim_results,
        design_around_options=_design_around_options(clean_product, claim_results),
        recommended_next_actions=_next_actions(overall_report.grade),
        run_id=run_id,
    )

    TraceLogger.write_event(
        run_id=run_id,
        patent_id=patent_id,
        agent_name="product_screening",
        event_name="screening_report",
        payload=screening_report.model_dump(),
        layer=Layer.PRODUCTION,
    )
    return screening_report


def _overall_report(claim_results: List[ClaimScreeningResult]) -> RiskReport:
    if not claim_results:
        return RiskReport(
            grade=RiskGrade.SAFE,
            confidence=0.0,
            reasoning="No claims were parsed from the candidate patent.",
        )
    severity = {
        RiskGrade.SAFE: 0,
        RiskGrade.LOW: 1,
        RiskGrade.MEDIUM: 2,
        RiskGrade.HIGH: 3,
    }
    top = max(claim_results, key=lambda item: (severity[item.grade], item.confidence))
    return RiskReport(
        grade=top.grade,
        confidence=top.confidence,
        reasoning=f"Highest-risk claim is claim {top.claim_id}: {top.reasoning}",
    )


def _overlap_signals(features: ClaimFeatures, target_spec: Dict[str, Any]) -> List[str]:
    signals: List[str] = []
    identity = target_spec.get("identity")
    if identity is not None and features.percent_identity is not None:
        if identity >= features.percent_identity:
            signals.append("identity_threshold_overlap")
        else:
            signals.append("identity_below_claim_threshold")
    if target_spec.get("ph") is not None and any("pH" in item or "ph" in item.lower() for item in features.functional_limitations):
        signals.append("functional_ph_condition_present")
    if features.markush_structures:
        signals.append("markush_or_variant_scope_present")
    if not signals:
        signals.append("no_direct_overlap_signal")
    return signals


def _design_around_options(
    product: ProductSpec,
    claim_results: List[ClaimScreeningResult],
) -> List[DesignAroundOption]:
    options: List[DesignAroundOption] = []
    for result in claim_results:
        if result.grade == RiskGrade.SAFE:
            continue
        features = result.features
        if product.identity is not None and features.percent_identity is not None:
            threshold = features.percent_identity
            screening_floor = max(threshold - RiskAnalyzer.CAUTION_BUFFER_PERCENT, 0.0)
            options.append(
                DesignAroundOption(
                    strategy_type="sequence_identity_design_space",
                    claim_id=result.claim_id,
                    basis=(
                        f"Claim {result.claim_id} contains a {threshold}% identity "
                        f"threshold and product identity is {product.identity}%."
                    ),
                    proposed_direction=(
                        f"Explore enzyme variants below the claimed {threshold}% identity "
                        f"threshold; for conservative screening, prioritize variants below "
                        f"{screening_floor:.1f}% while preserving activity."
                    ),
                )
            )

        if product.ph is not None:
            for low, high in _claim_ph_ranges(features.functional_limitations):
                if low <= product.ph <= high:
                    options.append(
                        DesignAroundOption(
                            strategy_type="process_condition_shift",
                            claim_id=result.claim_id,
                            basis=(
                                f"Claim {result.claim_id} recites pH {low:g}-{high:g} "
                                f"and product pH is {product.ph:g}."
                            ),
                            proposed_direction=(
                                f"Evaluate whether the process can operate outside pH "
                                f"{low:g}-{high:g}, such as below {low:g} or above {high:g}, "
                                "without losing required enzyme performance."
                            ),
                        )
                    )

        if features.markush_structures:
            options.append(
                DesignAroundOption(
                    strategy_type="claim_scope_substitution",
                    claim_id=result.claim_id,
                    basis=(
                        f"Claim {result.claim_id} contains Markush, variant, or "
                        "substitution-style scope language."
                    ),
                    proposed_direction=(
                        "Map the product enzyme class and mutation pattern against the "
                        "listed groups; prioritize alternatives outside the listed classes, "
                        "variants, or substitution families."
                    ),
                )
            )
    return options


def _claim_ph_ranges(functional_limitations: List[str]) -> List[tuple[float, float]]:
    ranges: List[tuple[float, float]] = []
    seen: set[tuple[float, float]] = set()
    for limitation in functional_limitations:
        for match in re.finditer(
            r"\bpH\s*(\d+(?:\.\d+)?)(?:\s*-\s*(\d+(?:\.\d+)?))?",
            limitation,
            flags=re.IGNORECASE,
        ):
            low = float(match.group(1))
            high = float(match.group(2)) if match.group(2) else low
            if low > high:
                low, high = high, low
            item = (low, high)
            if item not in seen:
                seen.add(item)
                ranges.append(item)
    return ranges


def _next_actions(grade: RiskGrade) -> List[str]:
    if grade == RiskGrade.HIGH:
        return [
            "Route to patent counsel before launch decision.",
            "Request claim chart review for the highest-risk claims.",
        ]
    if grade == RiskGrade.MEDIUM:
        return [
            "Request expert review of claim scope and product mapping.",
            "Check jurisdiction, expiry, and family status.",
        ]
    if grade == RiskGrade.LOW:
        return [
            "Keep in watch list and verify product conditions against claim limitations.",
            "Confirm no dependent claim adds a missing overlap element.",
        ]
    return [
        "Archive screening report with product assumptions.",
        "Re-run if product conditions or patent claims change.",
    ]
