import re
from typing import Dict, Iterable, List, Optional
from xml.etree import ElementTree

from production.sequence.models import SequenceAlignmentResult

AMINO_ACID_ALPHABET = set("ABCDEFGHIKLMNPQRSTVWXYZUO")
MAX_REPORTED_CHANGES = 24
THREE_LETTER_AMINO_ACIDS = {
    "ALA": "A",
    "ARG": "R",
    "ASN": "N",
    "ASP": "D",
    "CYS": "C",
    "GLN": "Q",
    "GLU": "E",
    "GLY": "G",
    "HIS": "H",
    "ILE": "I",
    "LEU": "L",
    "LYS": "K",
    "MET": "M",
    "PHE": "F",
    "PRO": "P",
    "SER": "S",
    "THR": "T",
    "TRP": "W",
    "TYR": "Y",
    "VAL": "V",
    "ASX": "B",
    "GLX": "Z",
    "XAA": "X",
    "SEC": "U",
    "PYL": "O",
}


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
    sequences.update(extract_st26_sequence_listing_entries(text))
    sequences.update(extract_st25_sequence_listing_entries(text))
    sequences.update(_extract_fasta_seq_ids(text))
    sequences.update(_extract_inline_seq_ids(text))
    return sequences


def extract_st26_sequence_listing_entries(text: str) -> Dict[str, str]:
    sequences: Dict[str, str] = {}
    for root in _st26_xml_roots(text):
        for sequence_data in root.iter():
            if _xml_local_name(sequence_data.tag) != "SequenceData":
                continue
            seq_id = _st26_sequence_id(sequence_data)
            if not seq_id:
                continue
            sequence = _st26_amino_acid_sequence(sequence_data)
            if len(sequence) >= 4:
                sequences[seq_id] = sequence
    return sequences


def extract_st25_sequence_listing_entries(text: str) -> Dict[str, str]:
    sequences: Dict[str, str] = {}
    for block in _sequence_listing_blocks(text):
        seq_id = _block_seq_id(block)
        if not seq_id:
            continue
        sequence = _sequence_from_block(block)
        if len(sequence) >= 4:
            sequences[seq_id] = sequence
    return sequences


def sequence_listing_content_to_sequence(text: str) -> str:
    return _sequence_from_block(text)


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


def _st26_xml_roots(text: str) -> List[ElementTree.Element]:
    roots: List[ElementTree.Element] = []
    for candidate in _xml_document_candidates(text):
        try:
            roots.append(ElementTree.fromstring(candidate))
        except ElementTree.ParseError:
            continue
    return roots


def _xml_document_candidates(text: str) -> List[str]:
    stripped = text.strip()
    candidates = [stripped] if stripped else []
    for root_name in ("ST26SequenceListing", "SequenceListing"):
        match = re.search(rf"<(?:[A-Za-z_][\w.-]*:)?{root_name}\b", text)
        if not match:
            continue
        close_matches = list(re.finditer(rf"</(?:[A-Za-z_][\w.-]*:)?{root_name}>", text))
        if close_matches:
            candidates.append(text[match.start() : close_matches[-1].end()])
    deduped: List[str] = []
    for candidate in candidates:
        if candidate and candidate not in deduped:
            deduped.append(candidate)
    return deduped


def _st26_sequence_id(sequence_data: ElementTree.Element) -> Optional[str]:
    number = _xml_attr(sequence_data, "sequenceIDNumber")
    if not number:
        number = _xml_child_text(sequence_data, "INSDSeq_sequenceID")
    if not number:
        return None
    match = re.search(r"\d+", number)
    return f"SEQ ID NO:{int(match.group(0))}" if match else None


def _st26_amino_acid_sequence(sequence_data: ElementTree.Element) -> str:
    sequence = _xml_child_text(sequence_data, "INSDSeq_sequence")
    moltype = _xml_child_text(sequence_data, "INSDSeq_moltype")
    if sequence and _st26_is_amino_acid_moltype(moltype, sequence):
        return normalize_amino_acid_sequence(sequence)

    translation = _st26_translation_qualifier(sequence_data)
    if translation:
        return normalize_amino_acid_sequence(translation)
    return ""


def _st26_is_amino_acid_moltype(moltype: str, sequence: str) -> bool:
    normalized_moltype = " ".join(moltype.upper().replace("_", " ").split())
    if normalized_moltype in {"AA", "PRT", "PROTEIN", "AMINO ACID", "PEPTIDE"}:
        return True
    if any(token in normalized_moltype for token in ("DNA", "RNA", "NUCLEOTIDE")):
        return False
    normalized_sequence = normalize_amino_acid_sequence(sequence)
    nucleotide_letters = set("ACGTUNRYSWKMBDHV")
    return bool(normalized_sequence) and any(char not in nucleotide_letters for char in normalized_sequence)


def _st26_translation_qualifier(sequence_data: ElementTree.Element) -> str:
    for qualifier in sequence_data.iter():
        if _xml_local_name(qualifier.tag) != "INSDQualifier":
            continue
        name = _xml_child_text(qualifier, "INSDQualifier_name")
        if name.strip().lower() != "translation":
            continue
        value = _xml_child_text(qualifier, "INSDQualifier_value")
        if value:
            return value
    return ""


def _xml_child_text(parent: ElementTree.Element, local_name: str) -> str:
    for node in parent.iter():
        if node is parent:
            continue
        if _xml_local_name(node.tag) == local_name and node.text:
            return node.text.strip()
    return ""


def _xml_attr(node: ElementTree.Element, local_name: str) -> str:
    for key, value in node.attrib.items():
        if _xml_local_name(key) == local_name:
            return str(value).strip()
    return ""


def _xml_local_name(name: str) -> str:
    return name.rsplit("}", 1)[-1].split(":", 1)[-1]


def _sequence_listing_blocks(text: str) -> List[str]:
    matches = list(
        re.finditer(
            r"(?:SEQ\s+ID\s+NO|<210>)\s*[:.]?\s*(?:NO:?\s*)?\d+",
            text,
            flags=re.IGNORECASE,
        )
    )
    blocks: List[str] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        blocks.append(text[match.start() : end])
    return blocks


def _block_seq_id(block: str) -> Optional[str]:
    match = re.search(r"\bSEQ\s+ID\s+NO[:.]?\s*(\d+)\b", block, flags=re.IGNORECASE)
    if not match:
        match = re.search(r"<210>\s*(\d+)", block, flags=re.IGNORECASE)
    return f"SEQ ID NO:{int(match.group(1))}" if match else None


def _sequence_from_block(block: str) -> str:
    if re.search(r"\bTYPE\s*:\s*PRT\b", block, flags=re.IGNORECASE):
        return _three_letter_sequence_to_one_letter(block)
    if re.search(r"<212>\s*PRT\b", block, flags=re.IGNORECASE):
        return _st25_400_sequence_to_one_letter(block)
    inline = _inline_sequence_after_marker(block)
    if inline:
        return inline
    return ""


def _inline_sequence_after_marker(block: str) -> str:
    match = re.search(
        r"\bSEQ\s+ID\s+NO[:.]?\s*\d+\s*[:=]\s*(?P<seq>(?:[A-Z]{5,}\s*){1,80})",
        block,
        flags=re.IGNORECASE,
    )
    return normalize_amino_acid_sequence(match.group("seq")) if match else ""


def _st25_400_sequence_to_one_letter(block: str) -> str:
    match = re.search(r"<400>\s*SEQUENCE:\s*\d+(?P<body>.*)", block, flags=re.IGNORECASE | re.DOTALL)
    return _sequence_lines_to_one_letter(match.group("body")) if match else ""


def _three_letter_sequence_to_one_letter(block: str) -> str:
    match = re.search(r"\bSEQUENCE\s*:\s*\d+(?P<body>.*)", block, flags=re.IGNORECASE | re.DOTALL)
    body = match.group("body") if match else block
    return _sequence_lines_to_one_letter(body)


def _sequence_lines_to_one_letter(text: str) -> str:
    residues: List[str] = []
    for token in re.findall(r"[A-Za-z]{1,3}", text.upper()):
        if len(token) == 1 and token in AMINO_ACID_ALPHABET:
            residues.append(token)
        elif token in THREE_LETTER_AMINO_ACIDS:
            residues.append(THREE_LETTER_AMINO_ACIDS[token])
    return "".join(residues)


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
