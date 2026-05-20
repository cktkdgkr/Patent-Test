import re
from typing import Dict, Iterable, List, Optional
from xml.etree import ElementTree

from production.sequence.models import (
    ClaimResidueCondition,
    ResiduePositionMapping,
    SequenceAlignmentResult,
)

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
AMINO_ACID_NAMES = {
    "ALANINE": "A",
    "ALA": "A",
    "ARGININE": "R",
    "ARG": "R",
    "ASPARAGINE": "N",
    "ASN": "N",
    "ASPARTIC": "D",
    "ASPARTATE": "D",
    "ASP": "D",
    "CYSTEINE": "C",
    "CYS": "C",
    "GLUTAMINE": "Q",
    "GLN": "Q",
    "GLUTAMIC": "E",
    "GLUTAMATE": "E",
    "GLU": "E",
    "GLYCINE": "G",
    "GLY": "G",
    "HISTIDINE": "H",
    "HIS": "H",
    "ISOLEUCINE": "I",
    "ILE": "I",
    "LEUCINE": "L",
    "LEU": "L",
    "LYSINE": "K",
    "LYS": "K",
    "METHIONINE": "M",
    "MET": "M",
    "PHENYLALANINE": "F",
    "PHE": "F",
    "PROLINE": "P",
    "PRO": "P",
    "SERINE": "S",
    "SER": "S",
    "THREONINE": "T",
    "THR": "T",
    "TRYPTOPHAN": "W",
    "TRP": "W",
    "TYROSINE": "Y",
    "TYR": "Y",
    "VALINE": "V",
    "VAL": "V",
}
RESIDUE_TOKEN_PATTERN = (
    r"A|R|N|D|C|Q|E|G|H|I|L|K|M|F|P|S|T|W|Y|V|"
    r"alanine|arginine|asparagine|aspartic|aspartate|cysteine|"
    r"glutamine|glutamic|glutamate|glycine|histidine|isoleucine|"
    r"leucine|lysine|methionine|phenylalanine|proline|serine|"
    r"threonine|tryptophan|tyrosine|valine|ala|arg|asn|asp|cys|"
    r"gln|glu|gly|his|ile|leu|lys|met|phe|pro|ser|thr|trp|tyr|val"
)


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


def extract_claim_residue_conditions(text: str) -> List[ClaimResidueCondition]:
    seq_refs = extract_seq_id_references(text)
    conditions: List[ClaimResidueCondition] = []
    for match in re.finditer(
        r"\b(?P<from>[ARNDCQEGHILKMFPSTWYV])(?P<pos>\d{1,5})(?P<to>[ARNDCQEGHILKMFPSTWYV])\b",
        text,
        flags=re.IGNORECASE,
    ):
        conditions.append(
            ClaimResidueCondition(
                raw_text=match.group(0),
                seq_id=_seq_id_near_match(text, match.start(), match.end(), seq_refs),
                mutation_type="substitution",
                reference_position=int(match.group("pos")),
                original_residue=_residue_to_one_letter(match.group("from")),
                claimed_residue=_residue_to_one_letter(match.group("to")),
            )
        )

    residue_patterns = (
        rf"\b(?:position|residue|amino\s+acid)\s+(?P<pos>\d{{1,5}})\s+"
        rf"(?:is|are|being|comprises|comprising|has|having|with|to)\s+"
        rf"(?P<to>{RESIDUE_TOKEN_PATTERN})\b",
        rf"\b(?:residue|amino\s+acid)\s+corresponding\s+to\s+"
        rf"(?:position|residue)\s+(?P<pos>\d{{1,5}})[^.;,\n]{{0,80}}?"
        rf"(?:is|are|being|comprises|comprising|has|having|with|to)\s+"
        rf"(?P<to>{RESIDUE_TOKEN_PATTERN})\b",
        rf"\b(?P<to>{RESIDUE_TOKEN_PATTERN})\s+(?:residue\s+)?"
        rf"at\s+(?:position|residue)\s+(?P<pos>\d{{1,5}})\b",
    )
    for pattern in residue_patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            claimed = _residue_to_one_letter(match.group("to"))
            if not claimed:
                continue
            conditions.append(
                ClaimResidueCondition(
                    raw_text=" ".join(match.group(0).split()),
                    seq_id=_seq_id_near_match(text, match.start(), match.end(), seq_refs),
                    mutation_type="residue_requirement",
                    reference_position=int(match.group("pos")),
                    claimed_residue=claimed,
                )
            )
    return _dedupe_residue_conditions(conditions)


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
    residue_conditions = extract_claim_residue_conditions(claim_text)
    refs = list(seq_id_references) or extract_seq_id_references(claim_text)
    if not refs and residue_conditions:
        condition_refs = [condition.seq_id for condition in residue_conditions if condition.seq_id]
        refs = [ref for index, ref in enumerate(condition_refs) if ref and ref not in condition_refs[:index]]
        if not refs and len(reference_sequences) == 1:
            refs = list(reference_sequences)
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
        ref_conditions = [
            condition
            for condition in residue_conditions
            if not condition.seq_id or condition.seq_id == ref
        ]
        results.append(
            align_sequences(
                product_sequence,
                reference_sequence,
                ref,
                threshold,
                residue_conditions=ref_conditions,
            )
        )
    return results


def align_sequences(
    target_sequence: str,
    reference_sequence: str,
    seq_id: str = "reference",
    threshold: Optional[float] = None,
    residue_conditions: Optional[Iterable[ClaimResidueCondition]] = None,
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
    residue_position_mappings = _map_claim_residue_positions(
        aligned_target=aligned_target,
        aligned_reference=aligned_reference,
        seq_id=seq_id,
        conditions=list(residue_conditions or []),
        identity=identity,
        coverage=coverage,
    )

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
        residue_position_mappings=residue_position_mappings,
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


def _seq_id_near_match(text: str, start: int, end: int, seq_refs: List[str]) -> Optional[str]:
    window = text[max(0, start - 120) : min(len(text), end + 120)]
    local_refs = extract_seq_id_references(window)
    if local_refs:
        return local_refs[0]
    return seq_refs[0] if len(seq_refs) == 1 else None


def _residue_to_one_letter(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    normalized = re.sub(r"[^A-Za-z]", "", value).upper()
    if len(normalized) == 1 and normalized in AMINO_ACID_ALPHABET:
        return normalized
    return AMINO_ACID_NAMES.get(normalized)


def _dedupe_residue_conditions(
    conditions: List[ClaimResidueCondition],
) -> List[ClaimResidueCondition]:
    deduped: List[ClaimResidueCondition] = []
    seen = set()
    for condition in conditions:
        key = (
            condition.seq_id,
            condition.mutation_type,
            condition.reference_position,
            condition.original_residue,
            condition.claimed_residue,
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(condition)
    return deduped


def _map_claim_residue_positions(
    aligned_target: str,
    aligned_reference: str,
    seq_id: str,
    conditions: List[ClaimResidueCondition],
    identity: Optional[float],
    coverage: Optional[float],
) -> List[ResiduePositionMapping]:
    if not conditions:
        return []

    conditions_by_position: Dict[int, List[tuple[int, ClaimResidueCondition]]] = {}
    for index, condition in enumerate(conditions):
        conditions_by_position.setdefault(condition.reference_position, []).append((index, condition))

    mappings: List[ResiduePositionMapping] = []
    mapped_condition_indexes: set[int] = set()
    target_pos = 0
    reference_pos = 0
    for target_char, reference_char in zip(aligned_target, aligned_reference):
        current_target_pos = None
        current_reference_pos = None
        if target_char != "-":
            target_pos += 1
            current_target_pos = target_pos
        if reference_char != "-":
            reference_pos += 1
            current_reference_pos = reference_pos

        if current_reference_pos is None or current_reference_pos not in conditions_by_position:
            continue
        for index, condition in conditions_by_position[current_reference_pos]:
            mapped_condition_indexes.add(index)
            mappings.append(
                _residue_mapping_from_alignment_column(
                    seq_id=seq_id,
                    condition=condition,
                    reference_residue=reference_char,
                    target_position=current_target_pos,
                    target_residue=target_char if target_char != "-" else None,
                    identity=identity,
                    coverage=coverage,
                )
            )

    for index, condition in enumerate(conditions):
        if index in mapped_condition_indexes:
            continue
        mappings.append(
            ResiduePositionMapping(
                seq_id=seq_id,
                raw_claim=condition.raw_text,
                mutation_type=condition.mutation_type,
                reference_position=condition.reference_position,
                original_residue=condition.original_residue,
                claimed_residue=condition.claimed_residue,
                status="reference_position_out_of_range",
                confidence="low",
                reasoning=(
                    f"{condition.raw_text} cites {seq_id} position {condition.reference_position}, "
                    "but that position is outside the recovered reference sequence."
                ),
            )
        )
    return mappings


def _residue_mapping_from_alignment_column(
    seq_id: str,
    condition: ClaimResidueCondition,
    reference_residue: str,
    target_position: Optional[int],
    target_residue: Optional[str],
    identity: Optional[float],
    coverage: Optional[float],
) -> ResiduePositionMapping:
    original_matches = (
        reference_residue == condition.original_residue
        if condition.original_residue and reference_residue
        else None
    )
    claim_match = (
        target_residue == condition.claimed_residue
        if target_residue and condition.claimed_residue
        else None
    )
    status = "mapped" if target_position is not None else "target_gap"
    confidence = _residue_mapping_confidence(status, identity, coverage, original_matches)
    if status == "mapped":
        match_text = ""
        if condition.claimed_residue:
            match_text = (
                f" Product residue {target_residue} {'matches' if claim_match else 'does not match'} "
                f"claimed residue {condition.claimed_residue}."
            )
        reasoning = (
            f"{seq_id} position {condition.reference_position} maps to product position "
            f"{target_position} by global alignment.{match_text}"
        )
    else:
        reasoning = (
            f"{seq_id} position {condition.reference_position} aligns to a gap in the "
            "product sequence."
        )
    if original_matches is False:
        reasoning += (
            f" The claim's stated original residue {condition.original_residue} does not "
            f"match recovered reference residue {reference_residue}."
        )

    return ResiduePositionMapping(
        seq_id=seq_id,
        raw_claim=condition.raw_text,
        mutation_type=condition.mutation_type,
        reference_position=condition.reference_position,
        reference_residue=reference_residue,
        original_residue=condition.original_residue,
        original_residue_matches=original_matches,
        product_position=target_position,
        product_residue=target_residue,
        claimed_residue=condition.claimed_residue,
        claim_match=claim_match,
        status=status,
        confidence=confidence,
        reasoning=reasoning,
    )


def _residue_mapping_confidence(
    status: str,
    identity: Optional[float],
    coverage: Optional[float],
    original_matches: Optional[bool],
) -> str:
    if original_matches is False:
        return "low"
    if identity is None or coverage is None:
        return "low"
    if status == "mapped" and identity >= 60.0 and coverage >= 80.0:
        return "high"
    if status in {"mapped", "target_gap"} and identity >= 30.0 and coverage >= 50.0:
        return "medium"
    return "low"


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
