import re
from typing import Dict, Iterable, List, Optional

from production.sequence.models import SequenceAlignmentResult

AMINO_ACID_ALPHABET = set("ABCDEFGHIKLMNPQRSTVWXYZUO")
MAX_REPORTED_CHANGES = 24


def normalize_amino_acid_sequence(value: Optional[str]) -> str:
    if not value:
        return ""
    lines = []
    for line in str(value).splitlines():
        if line.strip().startswith(">"):
            continue
        lines.append(line)
    text = "".join(lines).upper()
    return "".join(char for char in text if char in AMINO_ACID_ALPHABET)


def first_fasta_sequence(value: Optional[str]) -> str:
    return normalize_amino_acid_sequence(value)


def extract_seq_id_references(text: str) -> List[str]:
    seen = set()
    refs: List[str] = []
    for match in re.finditer(r"\bSEQ\s+ID\s+NO[:.]?\s*(\d+)\b", text, flags=re.IGNORECASE):
        ref = f"SEQ ID NO:{int(match.group(1))}"
        if ref not in seen:
            seen.add(ref)
            refs.append(ref)
    return refs


def extract_mutation_terms(text: str) -> List[str]:
    terms: List[str] = []
    seen = set()
    for pattern in (
        r"\b[A-Z]\d+[A-Z]\b",
        r"\b(?:substitution|deletion|insertion|truncation)s?\s+of\s+[^.;,]+",
    ):
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            term = " ".join(match.group(0).split())
            key = term.upper()
            if key not in seen:
                seen.add(key)
                terms.append(term)
    return terms


def extract_reference_sequences(text: str) -> Dict[str, str]:
    sequences: Dict[str, str] = {}
    sequences.update(_extract_fasta_seq_ids(text))
    sequences.update(_extract_inline_seq_ids(text))
    return sequences


def compare_claim_sequences(
    product_sequence: str,
    claim_text: str,
    seq_id_references: Iterable[str],
    reference_sequences: Dict[str, str],
    threshold: Optional[float] = None,
) -> List[SequenceAlignmentResult]:
    refs = list(seq_id_references) or extract_seq_id_references(claim_text)
    if not refs:
        return []
    if not product_sequence:
        return [
            SequenceAlignmentResult(
                seq_id=ref,
                status="no_product_sequence",
                threshold=threshold,
                reasoning=f"{ref} is referenced, but no product amino acid sequence was provided.",
            )
            for ref in refs
        ]

    results: List[SequenceAlignmentResult] = []
    for ref in refs:
        reference_sequence = reference_sequences.get(ref)
        if not reference_sequence:
            results.append(
                SequenceAlignmentResult(
                    seq_id=ref,
                    status="missing_reference_sequence",
                    target_length=len(product_sequence),
                    threshold=threshold,
                    reasoning=(
                        f"{ref} is referenced, but the patent text available to this run "
                        "did not include a recoverable reference sequence."
                    ),
                )
            )
            continue
        results.append(align_sequences(product_sequence, reference_sequence, ref, threshold))
    return results


def align_sequences(
    target_sequence: str,
    reference_sequence: str,
    seq_id: str = "reference",
    threshold: Optional[float] = None,
) -> SequenceAlignmentResult:
    target = normalize_amino_acid_sequence(target_sequence)
    reference = normalize_amino_acid_sequence(reference_sequence)
    if not target:
        return SequenceAlignmentResult(
            seq_id=seq_id,
            status="no_product_sequence",
            reference_length=len(reference),
            threshold=threshold,
            reasoning="No product amino acid sequence was provided.",
        )
    if not reference:
        return SequenceAlignmentResult(
            seq_id=seq_id,
            status="missing_reference_sequence",
            target_length=len(target),
            threshold=threshold,
            reasoning=f"{seq_id} has no recoverable reference sequence.",
        )

    aligned_target, aligned_reference = _needleman_wunsch(target, reference)
    matches = 0
    substitutions: List[str] = []
    deletions: List[str] = []
    insertions: List[str] = []
    ref_pos = 0

    for target_char, reference_char in zip(aligned_target, aligned_reference):
        if reference_char != "-":
            ref_pos += 1
        if target_char == reference_char and target_char != "-":
            matches += 1
        elif target_char == "-" and reference_char != "-":
            _append_limited(deletions, f"del{ref_pos}{reference_char}")
        elif reference_char == "-" and target_char != "-":
            _append_limited(insertions, f"ins{ref_pos}{target_char}")
        elif target_char != "-" and reference_char != "-":
            _append_limited(substitutions, f"{reference_char}{ref_pos}{target_char}")

    identity = round((matches / max(len(target), len(reference))) * 100, 2)
    aligned_reference_positions = sum(1 for char in aligned_reference if char != "-")
    covered_reference_positions = sum(
        1
        for target_char, reference_char in zip(aligned_target, aligned_reference)
        if target_char != "-" and reference_char != "-"
    )
    coverage = round((covered_reference_positions / aligned_reference_positions) * 100, 2)
    threshold_met = identity >= threshold if threshold is not None else None
    reasoning = f"{seq_id} alignment identity {identity}% with {coverage}% reference coverage."
    if threshold is not None:
        reasoning += f" Claim threshold is at least {threshold}%."

    return SequenceAlignmentResult(
        seq_id=seq_id,
        status="matched",
        identity=identity,
        coverage=coverage,
        target_length=len(target),
        reference_length=len(reference),
        matches=matches,
        substitutions=substitutions,
        deletions=deletions,
        insertions=insertions,
        threshold=threshold,
        threshold_met=threshold_met,
        reasoning=reasoning,
    )


def _extract_fasta_seq_ids(text: str) -> Dict[str, str]:
    sequences: Dict[str, str] = {}
    pattern = re.compile(
        r">[^\n]*(SEQ\s+ID\s+NO[:.]?\s*(?P<num>\d+))[^\n]*\n(?P<seq>(?:[A-Za-z]{5,}[\s\r\n]*)+)",
        flags=re.IGNORECASE,
    )
    for match in pattern.finditer(text):
        seq = normalize_amino_acid_sequence(match.group("seq"))
        if len(seq) >= 10:
            sequences[f"SEQ ID NO:{int(match.group('num'))}"] = seq
    return sequences


def _extract_inline_seq_ids(text: str) -> Dict[str, str]:
    sequences: Dict[str, str] = {}
    pattern = re.compile(
        r"\bSEQ\s+ID\s+NO[:.]?\s*(?P<num>\d+)\s*[:=]\s*(?P<seq>(?:[A-Z]{5,}\s*){1,80})",
        flags=re.IGNORECASE,
    )
    for match in pattern.finditer(text):
        seq = normalize_amino_acid_sequence(match.group("seq"))
        if len(seq) >= 10:
            sequences[f"SEQ ID NO:{int(match.group('num'))}"] = seq
    return sequences


def _needleman_wunsch(target: str, reference: str) -> tuple[str, str]:
    rows = len(target) + 1
    cols = len(reference) + 1
    scores = [[0] * cols for _ in range(rows)]
    trace = [[""] * cols for _ in range(rows)]

    for i in range(1, rows):
        scores[i][0] = -i
        trace[i][0] = "up"
    for j in range(1, cols):
        scores[0][j] = -j
        trace[0][j] = "left"

    for i in range(1, rows):
        for j in range(1, cols):
            diagonal = scores[i - 1][j - 1] + (1 if target[i - 1] == reference[j - 1] else -1)
            up = scores[i - 1][j] - 1
            left = scores[i][j - 1] - 1
            best = max(diagonal, up, left)
            scores[i][j] = best
            trace[i][j] = "diag" if best == diagonal else "up" if best == up else "left"

    aligned_target: List[str] = []
    aligned_reference: List[str] = []
    i = len(target)
    j = len(reference)
    while i > 0 or j > 0:
        move = trace[i][j]
        if move == "diag":
            aligned_target.append(target[i - 1])
            aligned_reference.append(reference[j - 1])
            i -= 1
            j -= 1
        elif move == "up":
            aligned_target.append(target[i - 1])
            aligned_reference.append("-")
            i -= 1
        else:
            aligned_target.append("-")
            aligned_reference.append(reference[j - 1])
            j -= 1

    return "".join(reversed(aligned_target)), "".join(reversed(aligned_reference))


def _append_limited(items: List[str], value: str) -> None:
    if len(items) < MAX_REPORTED_CHANGES:
        items.append(value)
