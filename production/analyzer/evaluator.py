import json
import logging
import os
import re
from typing import List, Optional

from core.access_control import Layer
from core.sanitizer import Sanitizer
from core.trace_logger import TraceLogger
from production.analyzer.models import RiskGrade, RiskReport
from production.parser.models import ClaimFeatures
from production.sequence import extract_mutation_terms

try:
    from google import genai
except Exception:  # pragma: no cover - depends on optional local package
    genai = None


logger = logging.getLogger(__name__)

try:
    client = genai.Client() if genai and os.getenv("GEMINI_API_KEY") else None
except Exception as e:  # pragma: no cover - exercised only with local credentials
    logger.warning("Could not initialize genai client: %s", e)
    client = None


class RiskAnalyzer:
    """
    Evaluates patent claim features against a target product specification.
    The local deterministic fallback is deliberately recall-first: near misses
    are escalated rather than marked safe.
    """

    CAUTION_BUFFER_PERCENT = 10.0

    @classmethod
    async def analyze_risk(
        cls,
        features: List[ClaimFeatures],
        target_spec: dict,
        run_id: Optional[str] = None,
        patent_id: str = "unknown",
    ) -> RiskReport:
        clean_target = Sanitizer.sanitize_payload(target_spec)
        features_json = Sanitizer.sanitize_payload([f.model_dump() for f in features])

        if not client:
            report = cls._deterministic_analyze(features, clean_target)
            cls._trace_report(run_id, patent_id, report, "deterministic")
            return report

        logger.info("Sending sanitized features and target spec to external LLM...")
        prompt = Sanitizer.sanitize(
            """
            You are a patent screening assistant for enzyme freedom-to-operate review.
            Return JSON matching the provided schema. Use a recall-first policy:
            when claim scope may plausibly cover the target, escalate risk.
            Do not output hidden chain-of-thought; provide a concise audit rationale.

            Extracted Claim Features:
            """
            + json.dumps(features_json, indent=2)
            + "\nTarget Product Specification:\n"
            + json.dumps(clean_target, indent=2)
        )

        try:
            response = await client.aio.models.generate_content(
                model="gemini-2.5-pro",
                contents=prompt,
                config={
                    "response_mime_type": "application/json",
                    "response_schema": RiskReport,
                    "temperature": 0.0,
                },
            )

            if response.parsed:
                report = response.parsed
            else:
                report = RiskReport(**json.loads(response.text))
            cls._trace_report(run_id, patent_id, report, "llm")
            return report
        except Exception as e:
            logger.error("LLM API error during risk analysis: %s", e)
            raise

    @classmethod
    def _deterministic_analyze(cls, features: List[ClaimFeatures], target_spec: dict) -> RiskReport:
        target_identity = target_spec.get("identity")
        identity_thresholds = [
            feature.percent_identity
            for feature in features
            if feature.percent_identity is not None
        ]

        if target_identity is not None and identity_thresholds:
            min_threshold = min(identity_thresholds)
            if target_identity >= min_threshold:
                return RiskReport(
                    grade=RiskGrade.HIGH,
                    confidence=0.9,
                    reasoning=(
                        f"Target identity {target_identity} is inside a claim threshold "
                        f"of at least {min_threshold} percent."
                    ),
                )
            if target_identity >= min_threshold - cls.CAUTION_BUFFER_PERCENT:
                return RiskReport(
                    grade=RiskGrade.HIGH,
                    confidence=0.74,
                    reasoning=(
                        f"Target identity {target_identity} is within the recall-first "
                        f"caution buffer below the {min_threshold} percent claim threshold."
                    ),
                )
            return RiskReport(
                grade=RiskGrade.SAFE,
                confidence=0.72,
                reasoning=(
                    f"Target identity {target_identity} is outside all detected identity "
                    "thresholds and outside the caution buffer."
                ),
            )
        mapped_residue_claim_matches = target_spec.get("mapped_residue_claim_matches") or []
        if mapped_residue_claim_matches:
            first_match = mapped_residue_claim_matches[0]
            return RiskReport(
                grade=RiskGrade.HIGH,
                confidence=0.82,
                reasoning=(
                    "Product sequence contains a residue matching a claimed patent "
                    f"position mapping: {first_match.get('seq_id')} position "
                    f"{first_match.get('reference_position')} maps to product position "
                    f"{first_match.get('product_position')}."
                ),
            )
        ambiguous_claim_residue_mappings = target_spec.get("ambiguous_claim_residue_mappings") or []
        if ambiguous_claim_residue_mappings:
            return RiskReport(
                grade=RiskGrade.MEDIUM,
                confidence=0.66,
                reasoning=(
                    "Detected a claimed residue position, but the corresponding product "
                    "residue could not be mapped with enough confidence for a low-risk call."
                ),
            )
        target_mutations = set(extract_mutation_terms(str(target_spec.get("variant") or "")))
        claim_mutations = {
            term
            for feature in features
            for term in feature.mutation_terms
            if re.fullmatch(r"[A-Z]\d+[A-Z]", term, flags=re.IGNORECASE)
        }
        if target_mutations and claim_mutations and {
            item.upper() for item in target_mutations
        } & {item.upper() for item in claim_mutations}:
            return RiskReport(
                grade=RiskGrade.HIGH,
                confidence=0.78,
                reasoning="Product variant contains a specific mutation recited by the claim.",
            )

        seq_id_referenced = any(feature.seq_id_references for feature in features)
        if identity_thresholds:
            return RiskReport(
                grade=RiskGrade.MEDIUM,
                confidence=0.58,
                reasoning=(
                    "Patent recites a sequence identity threshold but the reference "
                    "SEQ ID NO sequence could not be located (not in fetched claim "
                    "text, not in public sequence-listing mirrors, and not supplied "
                    "by the user). The product sequence was NOT compared against "
                    "the patent reference - this MEDIUM is a hedge based on the "
                    "presence of an identity claim alone. To get a real comparison, "
                    "add the reference sequence to the CSV 'reference_sequences' "
                    "column or paste it into the patent's sequence listing source."
                ),
            )

        if any(feature.markush_structures for feature in features):
            sequence_note = (
                " Patent referenced SEQ ID NO sequence(s) but they could not be "
                "located for actual identity comparison; consider adding them to the "
                "CSV 'reference_sequences' column for a definitive call."
                if seq_id_referenced
                else ""
            )
            return RiskReport(
                grade=RiskGrade.MEDIUM,
                confidence=0.6 if seq_id_referenced else 0.68,
                reasoning=(
                    "Detected variant or Markush language without enough target "
                    "detail to compute an overlap. The product sequence was NOT "
                    "compared against the patent reference."
                    + sequence_note
                ),
            )
        functional_limitations = [
            limitation
            for feature in features
            for limitation in feature.functional_limitations
        ]
        if functional_limitations:
            target_ph = cls._target_ph(target_spec)
            claim_ph_ranges = cls._claim_ph_ranges(functional_limitations)
            if target_ph is not None and claim_ph_ranges:
                if any(low <= target_ph <= high for low, high in claim_ph_ranges):
                    return RiskReport(
                        grade=RiskGrade.LOW,
                        confidence=0.66,
                        reasoning=(
                            f"Target pH {target_ph} overlaps a functional pH limitation "
                            "detected in the claims."
                        ),
                    )
                return RiskReport(
                    grade=RiskGrade.SAFE,
                    confidence=0.7,
                    reasoning=(
                        f"Target pH {target_ph} does not overlap detected claim pH "
                        "limitations."
                    ),
                )
            return RiskReport(
                grade=RiskGrade.SAFE,
                confidence=0.62,
                reasoning=(
                    "Detected functional claim limitations, but the target specification "
                    "does not provide matching functional conditions for an overlap finding."
                ),
            )
        return RiskReport(
            grade=RiskGrade.SAFE,
            confidence=0.55,
            reasoning="No identity, Markush, or functional overlap signals were detected.",
        )

    @staticmethod
    def _target_ph(target_spec: dict) -> Optional[float]:
        for key in ("pH", "ph", "target_pH", "target_ph"):
            value = target_spec.get(key)
            if isinstance(value, (int, float)):
                return float(value)
            if isinstance(value, str):
                try:
                    return float(value)
                except ValueError:
                    continue
        return None

    @staticmethod
    def _claim_ph_ranges(functional_limitations: List[str]) -> List[tuple[float, float]]:
        ranges: List[tuple[float, float]] = []
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
                ranges.append((low, high))
        return ranges

    @staticmethod
    def _trace_report(
        run_id: Optional[str],
        patent_id: str,
        report: RiskReport,
        mode: str,
    ) -> None:
        if not run_id:
            return
        TraceLogger.write_event(
            run_id=run_id,
            patent_id=patent_id,
            agent_name="risk_analyzer",
            event_name="risk_report",
            payload={"mode": mode, "report": report.model_dump()},
            layer=Layer.PRODUCTION,
        )
