import json
import logging
import os
import re
from typing import Optional

from core.access_control import Layer
from core.sanitizer import Sanitizer
from core.trace_logger import TraceLogger
from production.parser.models import ClaimFeatures, ClaimNode
from production.sequence import extract_mutation_terms, extract_seq_id_references

try:
    from google import genai
except Exception:  # pragma: no cover - depends on optional local credentials/package
    genai = None


logger = logging.getLogger(__name__)

try:
    client = genai.Client() if genai and os.getenv("GEMINI_API_KEY") else None
except Exception as e:  # pragma: no cover - exercised only with local credentials
    logger.warning("Could not initialize genai client. GEMINI_API_KEY may be unset: %s", e)
    client = None


class LLMExtractor:
    """
    Extracts structured claim features.
    Uses the external LLM only after sanitizer approval; otherwise falls back to
    deterministic extraction so local harness tests remain reproducible.
    """

    @classmethod
    async def extract_features(
        cls,
        claim_node: ClaimNode,
        run_id: Optional[str] = None,
        patent_id: str = "unknown",
    ) -> ClaimFeatures:
        clean_text = Sanitizer.sanitize(claim_node.text)
        if not client:
            features = cls._deterministic_extract_features(clean_text)
            cls._trace_features(run_id, patent_id, claim_node.id, features, "deterministic")
            return features

        logger.info("Extracting features for claim %s via external LLM...", claim_node.id)
        prompt = Sanitizer.sanitize(
            """
            Extract key patent claim features as JSON.
            Include percent_identity when a minimum identity or homology is stated.
            Include functional limitations such as pH, temperature, activity, substrate,
            or stability conditions. Include Markush or variant groups when present.
            Include seq_id_references like SEQ ID NO:1, and mutation_terms such as
            A123V, deletions, insertions, substitutions, or truncations.

            Claim text:
            """
            + clean_text
        )

        try:
            response = await client.aio.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
                config={
                    "response_mime_type": "application/json",
                    "response_schema": ClaimFeatures,
                    "temperature": 0.0,
                },
            )

            if response.parsed:
                features = response.parsed
            else:
                features = ClaimFeatures(**json.loads(response.text))
            cls._trace_features(run_id, patent_id, claim_node.id, features, "llm")
            return features
        except Exception as e:
            logger.error("LLM API error during extraction: %s", e)
            raise

    @staticmethod
    def _deterministic_extract_features(text: str) -> ClaimFeatures:
        identity_match = re.search(
            r"(?:at\s+least\s+)?(\d+(?:\.\d+)?)\s*%\s*(?:identity|homology)",
            text,
            flags=re.IGNORECASE,
        )
        functional_limitations = []
        for pattern in (
            r"\bpH\s*\d+(?:\.\d+)?(?:\s*-\s*\d+(?:\.\d+)?)?",
            r"\b\d+(?:\.\d+)?\s*(?:C|degrees C)\b",
            r"\b(?:active|stable|activity|substrate|temperature|thermostable)\b[^.;]*",
        ):
            functional_limitations.extend(
                match.group(0).strip()
                for match in re.finditer(pattern, text, flags=re.IGNORECASE)
            )

        markush_structures = []
        if re.search(r"\bselected\s+from\s+the\s+group\s+consisting\s+of\b", text, re.IGNORECASE):
            markush_structures.append("selected from the group consisting of")
        if re.search(r"\bvariant(?:s)?\b|\bsubstitution(?:s)?\b", text, re.IGNORECASE):
            markush_structures.append("variant_or_substitution")

        return ClaimFeatures(
            percent_identity=float(identity_match.group(1)) if identity_match else None,
            functional_limitations=functional_limitations,
            markush_structures=markush_structures,
            seq_id_references=extract_seq_id_references(text),
            mutation_terms=extract_mutation_terms(text),
        )

    @staticmethod
    def _trace_features(
        run_id: Optional[str],
        patent_id: str,
        claim_id: int,
        features: ClaimFeatures,
        mode: str,
    ) -> None:
        if not run_id:
            return
        TraceLogger.write_event(
            run_id=run_id,
            patent_id=patent_id,
            agent_name="claim_parser",
            event_name=f"claim_{claim_id}_features",
            payload={"claim_id": claim_id, "mode": mode, "features": features.model_dump()},
            layer=Layer.PRODUCTION,
        )
