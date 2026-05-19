import json
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from pydantic import BaseModel, Field

from core.sanitizer import Sanitizer
from production.sequence.analysis import (
    extract_reference_sequences,
    normalize_amino_acid_sequence,
    sequence_listing_content_to_sequence,
)


class SequenceWebFetchResult(BaseModel):
    patent_id: str
    sequences: Dict[str, str] = Field(default_factory=dict)
    sources: Dict[str, str] = Field(default_factory=dict)
    errors: List[str] = Field(default_factory=list)


def fetch_sequence_references_for_patent(
    patent_id: str,
    seq_id_references: Iterable[str],
    timeout_seconds: int = 20,
) -> SequenceWebFetchResult:
    clean_patent_id = Sanitizer.sanitize(str(patent_id).strip())
    refs = [_canonical_seq_id(ref) for ref in seq_id_references]
    refs = [ref for index, ref in enumerate(refs) if ref and ref not in refs[:index]]
    result = SequenceWebFetchResult(patent_id=clean_patent_id)
    if not clean_patent_id or not refs:
        return result

    cached_sequences, cached_sources = _read_cache(clean_patent_id)
    _merge_matching(result, cached_sequences, refs, cached_sources or "data/sequence_cache/web")
    missing = [ref for ref in refs if ref not in result.sequences]
    if not missing:
        return result

    for fetcher in (_fetch_uspto_psips_sequences, _fetch_ncbi_patent_sequences, _fetch_google_patents_sequences):
        try:
            found = fetcher(clean_patent_id, missing, timeout_seconds)
            _merge_matching(result, found.sequences, missing, found.sources)
            result.errors.extend(found.errors)
        except Exception as exc:
            result.errors.append(f"{fetcher.__name__}: {exc}")
        missing = [ref for ref in refs if ref not in result.sequences]
        if not missing:
            break

    if result.sequences:
        _write_cache(clean_patent_id, result.sequences, result.sources)
    return result


def _fetch_uspto_psips_sequences(
    patent_id: str,
    seq_id_references: Iterable[str],
    timeout_seconds: int,
) -> SequenceWebFetchResult:
    result = SequenceWebFetchResult(patent_id=patent_id)
    detail = _psips_document_details(patent_id, timeout_seconds)
    if not detail:
        result.errors.append("USPTO PSIPS: document not found")
        return result
    publication_no = detail.get("publicationNo")
    if not publication_no:
        result.errors.append("USPTO PSIPS: missing publicationNo")
        return result

    for ref in seq_id_references:
        seq_num = _seq_id_number(ref)
        if seq_num is None:
            continue
        url = (
            "https://seqdata.uspto.gov/viewservice/api/documents/sequence-details?"
            f"documentId={urllib.parse.quote(str(publication_no))}&nums={seq_num}"
        )
        try:
            data = _get_json(url, timeout_seconds)
        except urllib.error.HTTPError as exc:
            result.errors.append(f"USPTO PSIPS {ref}: HTTP {exc.code}")
            continue
        contents = str(data.get("contents") or "")
        sequence = sequence_listing_content_to_sequence(contents)
        if sequence:
            result.sequences[ref] = sequence
            result.sources[ref] = f"USPTO PSIPS {publication_no} SEQ ID NO:{seq_num}"
    return result


def _fetch_ncbi_patent_sequences(
    patent_id: str,
    seq_id_references: Iterable[str],
    timeout_seconds: int,
) -> SequenceWebFetchResult:
    result = SequenceWebFetchResult(patent_id=patent_id)
    numeric_id = _patent_numeric_id(patent_id)
    if not numeric_id:
        result.errors.append("NCBI: no numeric patent id")
        return result
    query = f'"{numeric_id}"[All Fields]'
    search_url = (
        "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?"
        + urllib.parse.urlencode({"db": "protein", "retmode": "json", "retmax": "200", "term": query})
    )
    data = _get_json(search_url, timeout_seconds)
    ids = data.get("esearchresult", {}).get("idlist", [])
    if not ids:
        result.errors.append("NCBI: no protein records")
        return result

    fasta_url = (
        "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?"
        + urllib.parse.urlencode({"db": "protein", "id": ",".join(ids), "rettype": "fasta", "retmode": "text"})
    )
    fasta = _get_text(fasta_url, timeout_seconds)
    records = _parse_ncbi_fasta_records(fasta, numeric_id)
    for ref in seq_id_references:
        seq_num = _seq_id_number(ref)
        if seq_num is None:
            continue
        sequence = records.get(seq_num)
        if sequence:
            result.sequences[ref] = sequence
            result.sources[ref] = f"NCBI Protein patent {numeric_id} sequence {seq_num}"
    return result


def _fetch_google_patents_sequences(
    patent_id: str,
    seq_id_references: Iterable[str],
    timeout_seconds: int,
) -> SequenceWebFetchResult:
    result = SequenceWebFetchResult(patent_id=patent_id)
    url = f"https://patents.google.com/patent/{urllib.parse.quote(patent_id)}/en"
    html = _get_text(url, timeout_seconds)
    sequences = extract_reference_sequences(html)
    _merge_matching(result, sequences, seq_id_references, f"Google Patents {url}")
    if not result.sequences:
        result.errors.append("Google Patents: no matching sequence listing text")
    return result


def _psips_document_details(patent_id: str, timeout_seconds: int) -> Optional[dict]:
    for candidate in _psips_document_id_candidates(patent_id):
        url = (
            "https://seqdata.uspto.gov/viewservice/api/documents/details?"
            f"documentId={urllib.parse.quote(candidate)}"
        )
        try:
            return _get_json(url, timeout_seconds)
        except urllib.error.HTTPError:
            continue
    return None


def _psips_document_id_candidates(patent_id: str) -> List[str]:
    compact = re.sub(r"[^A-Za-z0-9]", "", patent_id).upper()
    candidates = [compact]
    match = re.fullmatch(r"US(RE\d{5,6})([A-Z]\d)?", compact)
    if match:
        candidates.extend([f"US{match.group(1)}", match.group(1)])
    match = re.fullmatch(r"US(\d{7,8})([A-Z]\d)?", compact)
    if match:
        candidates.extend([f"US{match.group(1)}{match.group(2) or ''}", f"US{match.group(1)}", match.group(1)])
    match = re.fullmatch(r"(\d{7,8})([A-Z]\d)?", compact)
    if match:
        candidates.extend([match.group(0), match.group(1)])
    deduped: List[str] = []
    for item in candidates:
        if item and item not in deduped:
            deduped.append(item)
    return deduped


def _parse_ncbi_fasta_records(fasta: str, patent_number: str) -> Dict[int, str]:
    records: Dict[int, str] = {}
    current_header = ""
    current_lines: List[str] = []

    def commit() -> None:
        if not current_header:
            return
        match = re.search(
            rf"Sequence\s+(\d+)\s+from\s+patent(?:\s+number)?\s+US\s*{re.escape(patent_number)}\b",
            current_header,
            flags=re.IGNORECASE,
        )
        if match:
            records[int(match.group(1))] = normalize_amino_acid_sequence("".join(current_lines))

    for line in fasta.splitlines():
        if line.startswith(">"):
            commit()
            current_header = line
            current_lines = []
        else:
            current_lines.append(line)
    commit()
    return records


def _get_json(url: str, timeout_seconds: int) -> dict:
    return json.loads(_get_text(url, timeout_seconds))


def _get_text(url: str, timeout_seconds: int) -> str:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "enzyme-patent-harness/0.1 (+sequence listing research)"},
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        return response.read().decode("utf-8", errors="replace")


def _merge_matching(
    result: SequenceWebFetchResult,
    sequences: Dict[str, str],
    refs: Iterable[str],
    sources,
) -> None:
    for ref in refs:
        canonical = _canonical_seq_id(ref)
        sequence = sequences.get(canonical)
        if sequence:
            result.sequences[canonical] = sequence
            result.sources[canonical] = sources.get(canonical, "web") if isinstance(sources, dict) else str(sources)


def _canonical_seq_id(value: str) -> str:
    match = re.search(r"\bSEQ\s+ID\s+NO[:.]?\s*(\d+)\b", str(value), flags=re.IGNORECASE)
    return f"SEQ ID NO:{int(match.group(1))}" if match else ""


def _seq_id_number(value: str) -> Optional[int]:
    canonical = _canonical_seq_id(value)
    match = re.search(r"(\d+)$", canonical)
    return int(match.group(1)) if match else None


def _patent_numeric_id(patent_id: str) -> str:
    compact = re.sub(r"[^A-Za-z0-9]", "", patent_id).upper()
    match = re.search(r"US(\d{7,11})", compact)
    if match:
        return match.group(1)
    match = re.search(r"\b(\d{7,11})\b", compact)
    return match.group(1) if match else ""


def _cache_path(patent_id: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9]+", "", patent_id).upper()
    return Path(__file__).resolve().parents[2] / "data" / "sequence_cache" / "web" / f"{safe}.json"


def _read_cache(patent_id: str) -> tuple[Dict[str, str], Dict[str, str]]:
    path = _cache_path(patent_id)
    if not path.exists():
        return {}, {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}, {}
    sequences = {key: str(value) for key, value in data.get("sequences", {}).items()}
    sources = {key: str(value) for key, value in data.get("sources", {}).items()}
    return sequences, sources


def _write_cache(patent_id: str, sequences: Dict[str, str], sources: Dict[str, str]) -> None:
    path = _cache_path(patent_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"sequences": sequences, "sources": sources}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
