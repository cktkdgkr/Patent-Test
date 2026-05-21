import re
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


_COUNTRY_PREFIX_RE = re.compile(r"^([A-Z]{2})\d")
_COUNTRY_CODE_RE = re.compile(r"^[A-Za-z]{2}$")
_SEQ_ID_LINE_RE = re.compile(
    r"^(?:>?\s*)?SEQ\s*ID\s*(?:NO\.?|NO\s*:)\s*[:#]?\s*(\d+)\s*[:=]?\s*(.*)$",
    flags=re.IGNORECASE,
)
_FASTA_HEADER_RE = re.compile(r"^>\s*(.+?)\s*$")


class PatentCandidate(BaseModel):
    candidate_id: str = Field(description="Stable row-level candidate identifier")
    patent_id: Optional[str] = Field(default=None, description="Patent ID or local mock patent ID")
    title: Optional[str] = None
    applicant: Optional[str] = None
    country_code: Optional[str] = Field(
        default=None,
        description="ISO 2-letter office/country code (e.g. US, KR, EP, WO, JP, CN)",
    )
    publication_number: Optional[str] = None
    application_number: Optional[str] = None
    patent_file: Optional[str] = Field(default=None, description="Local candidate patent text path")
    claim_text: Optional[str] = Field(default=None, description="Raw claim text from the uploaded spreadsheet")
    abstract: Optional[str] = None
    keywords: List[str] = Field(default_factory=list)
    reference_sequences: Dict[str, str] = Field(
        default_factory=dict,
        description=(
            "User-supplied SEQ ID NO sequences keyed by canonical id "
            "('SEQ ID NO:1', 'SEQ ID NO:2', ...). Used when the patent's "
            "sequence listing is not on a public mirror so the screening "
            "workflow can still compare against the product sequence."
        ),
    )
    metadata: Dict[str, Any] = Field(default_factory=dict)

    def display_patent_id(self) -> str:
        return (
            self.patent_id
            or self.combined_identifier()
            or self.candidate_id
        )

    def combined_identifier(self) -> Optional[str]:
        """Return the best fetcher-ready identifier, combining `country_code` with the
        publication or application number when the number lacks a country prefix.
        """
        primary = self.publication_number or self.application_number
        if not primary:
            return None
        primary = primary.strip()
        if not primary:
            return None

        cleaned_number = re.sub(r"[^A-Za-z0-9]", "", primary)
        if _COUNTRY_PREFIX_RE.match(cleaned_number.upper()):
            return primary

        cc = (self.country_code or "").strip().upper()
        if cc and _COUNTRY_CODE_RE.match(cc):
            if not cleaned_number:
                return None
            return f"{cc}{cleaned_number}"
        return primary


def parse_reference_sequences_field(value: str) -> Dict[str, str]:
    """Parse a multi-line ``reference_sequences`` cell into a
    ``{"SEQ ID NO:1": "MKTAYI...", ...}`` mapping.

    Accepts three formats interchangeably:

    1. FASTA-style with ``>`` headers (any sequence text whose header parses
       as a SEQ ID number)::

         >SEQ ID NO:1
         MKTAYIAKQRQISFVK
         SHFSRQEILDLIC
         >SEQ ID NO:2
         MKDPLNK...

    2. Inline ``key=value`` separated by ``;`` or newlines::

         SEQ ID NO:1=MKTAYIAKQRQISFVKSHFSRQEILDLIC; SEQ ID NO:2=MKDPLN...

    3. Plain ``SEQ ID NO:N: SEQUENCE`` lines (colon as separator).

    Returns an empty mapping when the input is empty or unparseable.
    """
    if not value:
        return {}
    text = str(value).strip()
    if not text:
        return {}
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    if ">" in text:
        return _parse_fasta_block(text)

    out: Dict[str, str] = {}
    segments: List[str] = []
    for piece in text.split(";"):
        segments.extend(piece.split("\n"))
    for segment in segments:
        segment = segment.strip()
        if not segment:
            continue
        match = _SEQ_ID_LINE_RE.match(segment)
        if not match:
            continue
        number = int(match.group(1))
        sequence_raw = match.group(2).strip().lstrip(":=").strip()
        sequence = _clean_sequence(sequence_raw)
        if sequence:
            out[f"SEQ ID NO:{number}"] = sequence
    return out


def _parse_fasta_block(text: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    current_id: Optional[str] = None
    current_chunks: List[str] = []
    for raw_line in text.split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith(">"):
            if current_id and current_chunks:
                seq = _clean_sequence("".join(current_chunks))
                if seq:
                    out[current_id] = seq
            current_chunks = []
            header = _FASTA_HEADER_RE.match(line)
            header_text = header.group(1) if header else line.lstrip(">").strip()
            match = re.search(r"SEQ\s*ID\s*(?:NO\.?|NO\s*:)\s*(\d+)", header_text, flags=re.IGNORECASE)
            current_id = f"SEQ ID NO:{match.group(1)}" if match else None
            continue
        if current_id is None:
            continue
        current_chunks.append(line)
    if current_id and current_chunks:
        seq = _clean_sequence("".join(current_chunks))
        if seq:
            out[current_id] = seq
    return out


def _clean_sequence(value: str) -> str:
    return re.sub(r"[^A-Za-z*]", "", value).upper()
