import json
import logging
import os
from typing import List, Optional

from core.access_control import Layer
from core.sanitizer import Sanitizer
from core.trace_logger import TraceLogger
from production.analyzer.models import RiskGrade, RiskReport
from production.parser.models import ClaimFeatures

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

        if any(feature.markush_structures for feature in features):
            return RiskReport(
                grade=RiskGrade.MEDIUM,
                confidence=0.68,
                reasoning="Detected variant or Markush language without enough target detail.",
            )
        if any(feature.functional_limitations for feature in features):
            return RiskReport(
                grade=RiskGrade.LOW,
                confidence=0.6,
                reasoning="Detected functional claim limitations but no direct identity overlap.",
            )
        return RiskReport(
            grade=RiskGrade.SAFE,
            confidence=0.55,
            reasoning="No identity, Markush, or functional overlap signals were detected.",
        )

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
