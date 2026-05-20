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
    from anthropic import AsyncAnthropic
except Exception:  # pragma: no cover - optional dependency
    AsyncAnthropic = None


logger = logging.getLogger(__name__)

_CLAUDE_MODEL = "claude-opus-4-7"
_CLAUDE_MAX_TOKENS = 4096

# Static extraction instructions. Kept in `system` and marked with
# cache_control so repeated calls only pay full-price input tokens for the
# variable claim text. The prompt is intentionally detailed so it both
# improves extraction quality and grows toward the Opus 4.7 prefix-cache
# minimum (~4096 tokens); short prefixes silently won't cache and the call
# still works, just without the cache discount.
_EXTRACTION_SYSTEM_PROMPT = """You are an expert patent claim parser specialized in biotechnology and enzyme patents. Your task is to extract structured features from a single patent claim and return them as JSON matching the required ClaimFeatures schema.

# Goals

- Read the patent claim text carefully.
- Identify the five categories below.
- Return only what the claim literally recites. Do not infer, guess, or expand the claim's scope.
- Preserve the claim's own wording for short phrases when possible.

# Extraction Categories

## 1. percent_identity (number | null)

The minimum percent identity, similarity, or homology the claim requires against a referenced sequence.

What to look for:
- "at least X% identity", "X% or greater identity", "at least X% sequence identity"
- "X% homology", "X% similar", "X% identical"
- Sometimes phrased as "having identity of at least X percent"

Rules:
- Return the bare numeric value as a float (e.g., 80.0 for "80%"). Never include the % symbol in the output.
- If multiple percentages are stated, prefer the one limiting the primary claimed entity (typically the lowest threshold against the main reference sequence).
- If no identity / similarity / homology is recited at all, return null.

Examples:
- "an enzyme having at least 80% sequence identity to SEQ ID NO:1" -> 80.0
- "polypeptide with 95% or greater homology to the reference" -> 95.0
- "isolated enzyme of SEQ ID NO:1" (no threshold) -> null
- "at least 70 percent identity" -> 70.0

## 2. functional_limitations (array of strings)

Operational, functional, or condition-of-use limitations placed on the claimed subject matter.

What to capture:
- pH ranges or values: "pH 6-8", "pH 7.0", "at pH between 5 and 9"
- Temperature ranges or values: "active at 60C", "stable at temperatures up to 70 degrees C"
- Activity / substrate / specificity statements: "exhibits protease activity", "active on cellulose", "selective for D-isomers"
- Stability / thermostability descriptors: "thermostable", "stable above 50 C"
- Cofactor or reaction-condition requirements: "in the presence of Ca2+", "requires NADPH"
- Industrial / use-condition limitations: "active in detergent formulations", "stable in non-aqueous solvent"

Rules:
- Capture each limitation as a short phrase, preferably copied from the claim wording.
- Do not paraphrase or generalize. "active at pH 6-8" stays as "pH 6-8" plus "active" if both wordings exist.
- Return an empty list if the claim recites no functional limitations.

Examples:
- "wherein the enzyme is active at pH 6-8 and stable above 50 C" -> ["pH 6-8", "above 50 C", "active", "stable"]
- "exhibits protease activity on casein" -> ["protease activity", "on casein"]
- "an enzyme of SEQ ID NO:1" -> []

## 3. markush_structures (array of strings)

Markush groups or variant-set constructions that define a closed set of alternatives.

What to look for:
- Classic Markush wording: "selected from the group consisting of ...", "selected from", "consisting of A, B, or C"
- Sequence Markush ranges: "any one of SEQ ID NO:1-50", "any of SEQ ID NOs:1, 2, or 3"
- Variant or substitution sets without explicit Markush wording: "a variant having substitutions at positions 10, 20, and 30"
- Homolog or derivative groupings: "a homolog of any one of ..."

Rules:
- Capture a short label or the literal Markush opener (e.g., "selected from the group consisting of"). Do not enumerate all of the alternatives inside it.
- For variant / substitution sets without literal Markush wording, use the descriptor "variant_or_substitution".
- Return an empty list if no Markush or variant grouping is present.

Examples:
- "selected from the group consisting of SEQ ID NO:1, SEQ ID NO:2, and SEQ ID NO:3" -> ["selected from the group consisting of"]
- "a variant comprising substitutions at positions 10, 20, and 30" -> ["variant_or_substitution"]
- "an isolated enzyme of SEQ ID NO:1" -> []

## 4. seq_id_references (array of strings)

All SEQ ID NO references cited anywhere in the claim.

Rules:
- Normalize to the canonical form "SEQ ID NO:<number>" with a colon and no internal whitespace.
- Preserve the order of first appearance in the claim.
- Expand short ranges where unambiguous (e.g., "SEQ ID NO:1-3" -> "SEQ ID NO:1", "SEQ ID NO:2", "SEQ ID NO:3"). If a range is large or open-ended, list the explicitly enumerated endpoints instead of fabricating intermediate IDs.
- Include references that appear inside Markush groups individually when explicitly enumerated.
- Do not include the sequence content itself, only the identifiers.
- Return an empty list when no SEQ ID is cited.

Examples:
- "having at least 90% identity to SEQ ID NO:1" -> ["SEQ ID NO:1"]
- "any one of SEQ ID NOs:1, 2, or 3" -> ["SEQ ID NO:1", "SEQ ID NO:2", "SEQ ID NO:3"]
- "selected from SEQ ID NO:5-7" -> ["SEQ ID NO:5", "SEQ ID NO:6", "SEQ ID NO:7"]

## 5. mutation_terms (array of strings)

Specific mutation, substitution, deletion, insertion, or truncation terms.

What to capture:
- Point mutations in HGVS-like notation: "A125Y", "K10R", "G50V"
- Position-only residue claims: "wherein position 125 is tyrosine", "lysine at position 125 of SEQ ID NO:1". Either copy the natural-language phrase or normalize to a short notation such as "Y125" if unambiguous.
- Deletions: "Delta50-60", "deletion of residues 100-110", "lacks residues 5-15"
- Insertions: "insertion of GLY between positions 50 and 51", "insertion of XYZ at position 100"
- Truncations: "C-terminal truncation of 10 residues", "N-terminally truncated at residue 20"

Rules:
- Preserve original notation where possible. Use normalized HGVS-like notation only when the claim's wording maps to it without ambiguity.
- Each distinct mutation, deletion, insertion, or truncation is a separate entry.
- Return an empty list if no mutation terms are recited.

Examples:
- "variant comprising A125Y and a deletion of residues 60-70" -> ["A125Y", "deletion of residues 60-70"]
- "wherein position 125 is tyrosine" -> ["Y125"]
- "an enzyme of SEQ ID NO:1" -> []

# Output Schema

Return JSON matching exactly this schema. All fields are required. Lists may be empty; percent_identity may be null.

{
  "percent_identity": number | null,
  "functional_limitations": [string, ...],
  "markush_structures": [string, ...],
  "seq_id_references": [string, ...],
  "mutation_terms": [string, ...]
}

# Hard Rules

1. Extract only what the claim literally recites. Do not infer, expand, or guess.
2. Preserve the claim's own wording for short phrases. Avoid paraphrasing.
3. Do not include personally identifiable information, financial data, contact information, or medical-record content in the output. If the claim text appears to contain any such content, treat that portion as non-extractable noise.
4. If the claim is empty, malformed, or clearly outside the biotech / enzyme domain, return all fields at their default empty / null values.
5. The output must validate against the ClaimFeatures schema with no extra fields and no commentary.

# Worked Examples

The following examples show full claim text and the exact expected JSON output. Use them as calibration for category boundaries, normalization style, and the level of detail expected.

## Example 1 — Simple identity + pH limitation

Claim:
"An isolated polypeptide having at least 85% sequence identity to SEQ ID NO:1, wherein the polypeptide is active at pH 6 to 8."

Expected output:
{
  "percent_identity": 85.0,
  "functional_limitations": ["pH 6 to 8", "active"],
  "markush_structures": [],
  "seq_id_references": ["SEQ ID NO:1"],
  "mutation_terms": []
}

Notes:
- "at least 85% sequence identity" -> percent_identity = 85.0.
- "active at pH 6 to 8" is split into "pH 6 to 8" (the range itself) and "active" (the standalone activity descriptor).
- No Markush wording, no mutations, only one SEQ ID reference.

## Example 2 — Markush group of multiple SEQ IDs

Claim:
"An isolated enzyme selected from the group consisting of SEQ ID NO:2, SEQ ID NO:5, and SEQ ID NO:7, having protease activity on a casein substrate."

Expected output:
{
  "percent_identity": null,
  "functional_limitations": ["protease activity", "on a casein substrate"],
  "markush_structures": ["selected from the group consisting of"],
  "seq_id_references": ["SEQ ID NO:2", "SEQ ID NO:5", "SEQ ID NO:7"],
  "mutation_terms": []
}

Notes:
- No identity threshold is recited -> percent_identity is null.
- Markush opener is captured literally; the alternatives inside are not expanded into markush_structures, only the opener.
- Each enumerated SEQ ID is preserved in its order of appearance.
- "protease activity on a casein substrate" yields two functional phrases as written.

## Example 3 — Variant claim with point mutations

Claim:
"A variant of SEQ ID NO:3 comprising substitutions A125Y and K200R, wherein the variant exhibits thermostability at 70 degrees C."

Expected output:
{
  "percent_identity": null,
  "functional_limitations": ["thermostability", "at 70 degrees C"],
  "markush_structures": ["variant_or_substitution"],
  "seq_id_references": ["SEQ ID NO:3"],
  "mutation_terms": ["A125Y", "K200R"]
}

Notes:
- "variant ... comprising substitutions" is captured as the Markush-style descriptor "variant_or_substitution" even though there is no literal "selected from" wording.
- Each point mutation is its own entry in mutation_terms.
- "thermostability at 70 degrees C" splits into the property ("thermostability") and the condition ("at 70 degrees C").

## Example 4 — Range expansion of SEQ ID NO references

Claim:
"A polynucleotide encoding a polypeptide having at least 90% identity to any one of SEQ ID NO:10-12."

Expected output:
{
  "percent_identity": 90.0,
  "functional_limitations": [],
  "markush_structures": ["selected from the group consisting of"],
  "seq_id_references": ["SEQ ID NO:10", "SEQ ID NO:11", "SEQ ID NO:12"],
  "mutation_terms": []
}

Notes:
- "any one of SEQ ID NO:10-12" is a Markush-style range; expand it to the explicit SEQ IDs.
- Use "selected from the group consisting of" as the Markush descriptor even though the literal Markush opener is not used, because the construction defines a closed alternative set.
- No functional or mutation language is recited.

## Example 5 — Deletion and truncation

Claim:
"The enzyme of claim 1, further comprising a deletion of residues 50-60 and a C-terminal truncation of 10 residues."

Expected output:
{
  "percent_identity": null,
  "functional_limitations": [],
  "markush_structures": [],
  "seq_id_references": [],
  "mutation_terms": ["deletion of residues 50-60", "C-terminal truncation of 10 residues"]
}

Notes:
- Dependent claim wording ("The enzyme of claim 1") does not by itself produce any output entry; only the new limitations added in this claim matter.
- Deletions and truncations preserve original wording in mutation_terms.

## Example 6 — Combined identity, mutation, and functional limitation

Claim:
"An isolated polypeptide having at least 95% identity to SEQ ID NO:1, wherein position 125 of SEQ ID NO:1 is tyrosine, and wherein the polypeptide is stable in non-aqueous solvent."

Expected output:
{
  "percent_identity": 95.0,
  "functional_limitations": ["stable", "in non-aqueous solvent"],
  "markush_structures": [],
  "seq_id_references": ["SEQ ID NO:1"],
  "mutation_terms": ["Y125"]
}

Notes:
- "position 125 of SEQ ID NO:1 is tyrosine" maps unambiguously to "Y125" using single-letter amino acid code.
- The same SEQ ID NO:1 appears twice in the text but is listed once in seq_id_references because the array represents the set of referenced IDs, not their occurrences. (Order is the order of first appearance.)
- Solvent / stability language is captured in functional_limitations.

## Example 7 — Out-of-domain or malformed input

Claim:
"This is not a patent claim."

Expected output:
{
  "percent_identity": null,
  "functional_limitations": [],
  "markush_structures": [],
  "seq_id_references": [],
  "mutation_terms": []
}

Notes:
- When the input is empty, malformed, or clearly outside the biotech / enzyme domain, return the schema with all default empty / null values. Do not invent content.

## Example 8 — Combined Markush of SEQ IDs and a variant set

Claim:
"An enzyme variant of any one of SEQ ID NO:1, SEQ ID NO:2, or SEQ ID NO:4, comprising one or more substitutions selected from A10V, G50S, and L200P, wherein the variant retains protease activity at pH 7."

Expected output:
{
  "percent_identity": null,
  "functional_limitations": ["protease activity", "at pH 7"],
  "markush_structures": ["selected from the group consisting of", "variant_or_substitution"],
  "seq_id_references": ["SEQ ID NO:1", "SEQ ID NO:2", "SEQ ID NO:4"],
  "mutation_terms": ["A10V", "G50S", "L200P"]
}

Notes:
- Two Markush-style constructions appear in this claim: the SEQ ID alternative set and the substitution alternative set. Both are captured in markush_structures.
- The three point substitutions are each listed in mutation_terms.
- "retains protease activity at pH 7" decomposes into "protease activity" and "at pH 7" in functional_limitations.

# Additional Edge Cases

## Identity language pitfalls

- "X% homology" and "X% similarity" both count as identity equivalents for this extractor. Capture the numeric threshold under percent_identity.
- "X% identical OVER 50 amino acids of SEQ ID NO:1": still record the percent_identity as X. Do not encode the window length in this field.
- "from about 80% to about 100% identity": treat the lower bound as the limiting threshold, so percent_identity = 80.0.
- "essentially identical" or "substantially identical" with no number: percent_identity = null.

## Functional limitation pitfalls

- Generic claim language like "an enzyme of SEQ ID NO:1" contains no functional limitations even though it describes an enzyme. Do not invent "enzymatic activity" as a limitation when the claim does not state it.
- Lists of substrates should each become an entry: "active on cellulose, hemicellulose, and xylan" -> ["active", "on cellulose", "on hemicellulose", "on xylan"] when the substrate phrasing is uniform, or a single compact entry when more natural. Prefer the most compact, claim-faithful phrasing.
- "having improved [property] over the parent enzyme": this is a comparative functional limitation. Capture it as a short phrase such as "improved [property]".

## Markush pitfalls

- A list of three or more alternatives joined by "or" or "and/or" without explicit "selected from" wording can still be a Markush-style closed set. Use the descriptor "selected from the group consisting of" only when the wording or structure clearly defines a closed alternative set.
- "comprising" inside a claim is generally open-ended, not a Markush opener.
- A variant set with a single substitution is still captured as "variant_or_substitution" if the claim language frames it as a variant relative to a reference sequence.

## Mutation pitfalls

- "lysine to arginine at position 10" -> "K10R" using single-letter codes.
- "lysine at position 10 of SEQ ID NO:1 is replaced by arginine" -> "K10R" by the same rule.
- "an X at position N where X is selected from A, V, L, or I": capture as "X10A", "X10V", "X10L", "X10I" when positions are explicit; if positions are described abstractly ("any conservative substitution at one or more positions"), use "variant_or_substitution" in markush_structures and leave mutation_terms empty.
- "shortened by 10 residues" vs "C-terminal truncation of 10 residues": both go in mutation_terms. Preserve whichever phrasing the claim uses.

# Worked Examples (continued)

## Example 9 — Insertion with stability language

Claim:
"A polypeptide of SEQ ID NO:8 comprising an insertion of GLY between positions 50 and 51, wherein the polypeptide is thermostable above 65 degrees C."

Expected output:
{
  "percent_identity": null,
  "functional_limitations": ["thermostable", "above 65 degrees C"],
  "markush_structures": [],
  "seq_id_references": ["SEQ ID NO:8"],
  "mutation_terms": ["insertion of GLY between positions 50 and 51"]
}

## Example 10 — Two SEQ IDs, comparative functional limitation

Claim:
"A polypeptide having at least 92% identity to SEQ ID NO:11 and at least 88% identity to SEQ ID NO:12, wherein the polypeptide exhibits improved thermostability over the polypeptide of SEQ ID NO:11."

Expected output:
{
  "percent_identity": 88.0,
  "functional_limitations": ["improved thermostability"],
  "markush_structures": [],
  "seq_id_references": ["SEQ ID NO:11", "SEQ ID NO:12"],
  "mutation_terms": []
}

Notes:
- When multiple identity thresholds are recited against different references, choose the threshold most limiting to the broadest claim scope. Here the polypeptide must satisfy BOTH thresholds, so the binding lower bound is the smaller of the two (88%).
- "improved thermostability" is a comparative functional limitation; capture it as a single short phrase.
- Both SEQ ID references are kept in order of first appearance.

## Example 11 — Conservative substitution language

Claim:
"A variant of SEQ ID NO:1 comprising one or more conservative substitutions at positions selected from 10, 50, and 100, wherein the variant retains at least 80% of the wild-type activity."

Expected output:
{
  "percent_identity": null,
  "functional_limitations": ["retains at least 80% of the wild-type activity"],
  "markush_structures": ["variant_or_substitution", "selected from the group consisting of"],
  "seq_id_references": ["SEQ ID NO:1"],
  "mutation_terms": []
}

Notes:
- "at least 80% of the wild-type activity" is a functional retention threshold, NOT a sequence identity threshold. percent_identity stays null.
- The substitution positions are framed as a closed alternative set ("selected from 10, 50, and 100"), captured as the Markush opener.
- Specific identities of the substituting residues are not given, so mutation_terms is empty. The variant descriptor goes in markush_structures.

## Example 12 — Nested limitations and units

Claim:
"An enzyme having at least 75% sequence identity to SEQ ID NO:14, wherein the enzyme retains at least 50% of its activity after incubation at pH 9 for 1 hour at 60 degrees C."

Expected output:
{
  "percent_identity": 75.0,
  "functional_limitations": ["retains at least 50% of its activity", "after incubation at pH 9", "for 1 hour", "at 60 degrees C"],
  "markush_structures": [],
  "seq_id_references": ["SEQ ID NO:14"],
  "mutation_terms": []
}

Notes:
- Multiple condition-of-use phrases can each appear separately in functional_limitations when they describe different aspects of the same incubation condition.
- "retains at least 50% of its activity" is an activity-retention threshold; do NOT confuse the 50% number with a sequence identity threshold.

# Final Reminder

Return only the JSON object matching the ClaimFeatures schema. Do not include explanations, commentary, or markdown formatting around the JSON. The schema validator will reject any extra fields or surrounding text.
"""


try:
    _client: Optional["AsyncAnthropic"] = (
        AsyncAnthropic() if AsyncAnthropic and os.getenv("ANTHROPIC_API_KEY") else None
    )
except Exception as e:  # pragma: no cover - exercised only with local credentials
    logger.warning(
        "Could not initialize Anthropic client; ANTHROPIC_API_KEY may be misconfigured: %s",
        e,
    )
    _client = None


class LLMExtractor:
    """
    Extracts structured claim features.
    Uses the Claude API when ANTHROPIC_API_KEY is set; otherwise falls back to
    deterministic regex extraction so harness tests remain reproducible without
    network access or credentials.
    """

    @classmethod
    async def extract_features(
        cls,
        claim_node: ClaimNode,
        run_id: Optional[str] = None,
        patent_id: str = "unknown",
    ) -> ClaimFeatures:
        clean_text = Sanitizer.sanitize(claim_node.text)
        if not _client:
            features = cls._deterministic_extract_features(clean_text)
            cls._trace_features(run_id, patent_id, claim_node.id, features, "deterministic")
            return features

        logger.info("Extracting features for claim %s via Claude...", claim_node.id)
        try:
            response = await _client.messages.parse(
                model=_CLAUDE_MODEL,
                max_tokens=_CLAUDE_MAX_TOKENS,
                thinking={"type": "adaptive"},
                system=[
                    {
                        "type": "text",
                        "text": _EXTRACTION_SYSTEM_PROMPT,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=[
                    {
                        "role": "user",
                        "content": f"Claim text:\n{clean_text}",
                    }
                ],
                output_format=ClaimFeatures,
            )
            features = response.parsed_output
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
