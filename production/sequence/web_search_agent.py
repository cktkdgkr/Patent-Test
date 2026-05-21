"""Claude-driven web-search fallback for patent amino acid sequences.

When every deterministic fetcher in :mod:`production.sequence.web_retriever`
fails to recover a SEQ ID NO sequence, this agent gets one more shot: it
uses Claude with the server-side ``web_search_20260209`` and
``web_fetch_20260209`` tools to navigate the public web the way a human
researcher would — Google Patents description, Espacenet, WIPO
PATENTSCOPE, KIPRIS / USPTO / NCBI Protein DB, patent family pages — and
returns whatever sequences it could ground in a URL.

Activated only when ``ANTHROPIC_API_KEY`` is set; silently skipped
otherwise so the rest of the pipeline keeps working without it. Each call
incurs Anthropic API + web-tool charges (~$0.10–$0.30 per patent), so the
chain reserves it as a last resort.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Dict, Iterable, List, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

try:
    from anthropic import Anthropic  # type: ignore[import-not-found]
except Exception:  # pragma: no cover - optional dependency
    Anthropic = None  # type: ignore[assignment, misc]


_SYSTEM_PROMPT = """You are a patent sequence retrieval agent. A user gives
you a patent identifier (e.g. ``CN202510536859``, ``KR1020240087254``,
``JP2016247708``) plus a list of SEQ ID NO references. Your job is to
search public web sources, locate the actual amino acid sequences, and
return them grounded in source URLs.

# Search strategy

Try sources in roughly this order, stopping per-reference as soon as the
sequence is found:

1. Google Patents — search both the application number and any resolved
   publication number; look inside the description / sequence listing
   section, not just claims.
2. Espacenet (worldwide.espacenet.com) — biblio + linked sequence
   listing documents.
3. WIPO PATENTSCOPE — particularly useful when the patent is a PCT
   national-phase entry (Korean ``10-YYYY-7XXXXXX`` numbers are PCT
   entries; the WO publication often carries the sequence).
4. KIPRIS (kipris.or.kr) for KR patents.
5. USPTO PEDS / PatFT / patentscope for US patents.
6. NCBI Protein DB — search "<patent_number>[Patent]" or by inventor +
   year.
7. Patent family equivalents on Google Patents ("Worldwide applications"
   on the patent's detail page).

# Output requirements

- Return single-letter amino acid sequences (``MKTAYI...``), not
  three-letter codes. Convert ``Met Lys Thr`` -> ``MKT`` if necessary.
- Strip whitespace, position numbers (``1   MKTAYIAKQR``), HTML, and
  prose from the sequence.
- The sequence must contain only the standard 20 amino acid letters
  (ACDEFGHIKLMNPQRSTVWY). Reject anything that looks like English prose
  (``DETAILED``, ``DESCRIPTION``, ``EXAMPLES``...) — that is not a
  sequence.
- Never fabricate. If a sequence cannot be located after a thorough
  search, omit it from ``findings`` and add a short reason to
  ``not_found_reasons`` instead.
- Always provide the URL where the sequence was actually read.

# Quality guard

Cross-check the sequence length when possible (the patent often states
``SEQ ID NO:1 is 47 amino acids long``); if your extracted length is
wildly different, search again before reporting.

Return JSON matching the ``AgentResult`` schema exactly. Do not add
prose, explanations, or markdown around the JSON.
"""


class SequenceFinding(BaseModel):
    seq_id: str = Field(description="Canonical SEQ ID reference, e.g. 'SEQ ID NO:1'.")
    amino_acid_sequence: str = Field(
        description="Single-letter amino acid sequence read from the source page."
    )
    source_url: str = Field(description="URL where the sequence was actually located.")
    confidence: Optional[str] = Field(
        default=None,
        description="Optional self-reported confidence: 'high' / 'medium' / 'low'.",
    )


class AgentResult(BaseModel):
    findings: List[SequenceFinding] = Field(default_factory=list)
    not_found_reasons: Dict[str, str] = Field(default_factory=dict)


def is_available() -> bool:
    """Whether the web search agent has its required dependencies and credentials."""
    if Anthropic is None:
        return False
    return bool(os.getenv("ANTHROPIC_API_KEY", "").strip())


def fetch_sequences_via_agent(
    patent_id: str,
    seq_id_references: Iterable[str],
    country_code: Optional[str] = None,
    model: str = "claude-opus-4-7",
    max_tokens: int = 12_000,
    max_continuations: int = 4,
) -> Dict[str, Dict[str, str]]:
    """Drive a Claude agent through web_search + web_fetch to find sequences.

    Returns ``{"sequences": {"SEQ ID NO:N": "MKTAYI..."},
                "sources":   {"SEQ ID NO:N": "web_search_agent (URL)"}}``.
    Returns empty dicts when:

    * the anthropic package is not installed
    * ANTHROPIC_API_KEY is unset
    * the agent returned no usable findings (e.g. all sources blocked, or
      patent is genuinely unpublished)
    * the API call itself raised — errors are logged and swallowed so the
      downstream chain still completes
    """
    if not is_available():
        logger.debug("Web search agent: skipping (no ANTHROPIC_API_KEY)")
        return {"sequences": {}, "sources": {}}

    refs_list = [str(ref).strip() for ref in seq_id_references if str(ref).strip()]
    if not refs_list:
        return {"sequences": {}, "sources": {}}

    country_hint = f"  (country: {country_code})" if country_code else ""
    user_message = (
        f"Patent identifier: {patent_id}{country_hint}\n\n"
        f"Please find amino acid sequences for these references:\n"
        + "\n".join(f"  - {ref}" for ref in refs_list)
        + "\n\nSearch the web and return findings as JSON matching the AgentResult "
        "schema. Only include sequences you actually located on a web page; do not "
        "fabricate or guess."
    )

    client = Anthropic()  # type: ignore[misc]
    messages: List[Dict] = [{"role": "user", "content": user_message}]
    schema = _strict_json_schema(AgentResult.model_json_schema())

    response = None
    for _ in range(max_continuations):
        try:
            response = client.messages.create(
                model=model,
                max_tokens=max_tokens,
                thinking={"type": "adaptive"},
                system=[
                    {
                        "type": "text",
                        "text": _SYSTEM_PROMPT,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=messages,
                output_config={"format": {"type": "json_schema", "schema": schema}},
                tools=[
                    {"type": "web_search_20260209", "name": "web_search"},
                    {"type": "web_fetch_20260209", "name": "web_fetch"},
                ],
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Web search agent: Claude API call failed: %s", exc)
            return {"sequences": {}, "sources": {}}

        if getattr(response, "stop_reason", None) != "pause_turn":
            break
        # Server-side web tools hit iteration cap; resume by echoing the
        # assistant's prior content back as context.
        messages = [
            {"role": "user", "content": user_message},
            {"role": "assistant", "content": response.content},
        ]

    if response is None:
        return {"sequences": {}, "sources": {}}

    text_parts: List[str] = []
    for block in response.content:
        if getattr(block, "type", None) == "text":
            text_parts.append(block.text)
    text = "".join(text_parts).strip()
    if not text:
        return {"sequences": {}, "sources": {}}

    try:
        data = json.loads(text)
        parsed = AgentResult.model_validate(data)
    except Exception as exc:
        logger.warning(
            "Web search agent: failed to parse structured response: %s "
            "(first 200 chars: %r)",
            exc,
            text[:200],
        )
        return {"sequences": {}, "sources": {}}

    sequences: Dict[str, str] = {}
    sources: Dict[str, str] = {}
    standard = set("ACDEFGHIKLMNPQRSTVWY")
    for finding in parsed.findings:
        raw = finding.amino_acid_sequence or ""
        cleaned = "".join(c for c in raw if c.isalpha()).upper()
        if len(cleaned) < 10:
            continue
        # Light sanity gate: at least 80% of the letters must be standard
        # amino acids. Real proteins occasionally use B/J/X but a much higher
        # rate of non-standard letters is almost always a hallucination.
        std_count = sum(1 for c in cleaned if c in standard)
        if std_count / max(len(cleaned), 1) < 0.8:
            continue
        sequences[_canonical_seq_id(finding.seq_id)] = cleaned
        sources[_canonical_seq_id(finding.seq_id)] = (
            f"web_search_agent ({finding.source_url})"
        )

    if not sequences and parsed.not_found_reasons:
        logger.info(
            "Web search agent: %s reported no findings (%s)",
            patent_id,
            "; ".join(f"{k}: {v}" for k, v in parsed.not_found_reasons.items()),
        )

    return {"sequences": sequences, "sources": sources}


def _canonical_seq_id(value: str) -> str:
    import re

    match = re.search(r"SEQ\s*ID\s*NO[:.]?\s*(\d+)", str(value), flags=re.IGNORECASE)
    return f"SEQ ID NO:{int(match.group(1))}" if match else str(value)


def _strict_json_schema(schema: dict) -> dict:
    """Anthropic structured outputs require ``additionalProperties: false`` on
    every object and reject numeric/length constraints. Pydantic emits neither
    automatically; this helper patches the schema in-place to satisfy both.
    """

    def visit(node: object) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object":
                node.setdefault("additionalProperties", False)
            for key in list(node.keys()):
                if key in {
                    "minimum",
                    "maximum",
                    "exclusiveMinimum",
                    "exclusiveMaximum",
                    "multipleOf",
                    "minLength",
                    "maxLength",
                    "minItems",
                    "maxItems",
                    "uniqueItems",
                    "pattern",
                }:
                    node.pop(key)
                else:
                    visit(node[key])
        elif isinstance(node, list):
            for item in node:
                visit(item)

    visit(schema)
    return schema
