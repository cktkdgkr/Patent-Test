import html
import io
import json
import re
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from pydantic import BaseModel, Field

from core.sanitizer import Sanitizer
from production.sequence.analysis import (
    extract_reference_sequences,
    normalize_amino_acid_sequence,
    sequence_listing_content_to_sequence,
)

MAX_SEQUENCE_DOCUMENT_BYTES = 25_000_000
MAX_LINKED_SEQUENCE_DOCUMENTS = 12


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

    for fetcher in (
        _fetch_wipo_pct_sequences,
        _fetch_epo_public_sequences,
        _fetch_uspto_psips_sequences,
        _fetch_ncbi_patent_sequences,
        _fetch_google_patents_sequences,
    ):
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


def _fetch_wipo_pct_sequences(
    patent_id: str,
    seq_id_references: Iterable[str],
    timeout_seconds: int,
) -> SequenceWebFetchResult:
    result = SequenceWebFetchResult(patent_id=patent_id)
    folder = _wipo_publication_folder(patent_id)
    if not folder:
        result.errors.append("WIPO PCT: not a WO publication number")
        return result

    publication_year = _wipo_publication_year(patent_id)
    if not publication_year:
        result.errors.append("WIPO PCT: cannot infer publication year")
        return result

    base_url = f"https://www.wipo.int/published_pct_sequences/publication/{publication_year}/"
    try:
        year_html = _get_text(base_url, min(timeout_seconds, 8))
    except urllib.error.URLError as exc:
        result.errors.append(f"WIPO PCT: year index unavailable ({exc})")
        return result

    date_links = [
        link for link in _html_links(year_html, base_url)
        if re.search(rf"/{publication_year}/\d{{4}}/?$", link)
    ]
    if not date_links:
        result.errors.append("WIPO PCT: no publication date directories found")
        return result

    for date_url in date_links:
        missing = [ref for ref in seq_id_references if _canonical_seq_id(ref) not in result.sequences]
        if not missing:
            break
        try:
            date_html = _get_text(date_url, min(timeout_seconds, 8))
        except urllib.error.URLError:
            continue
        if folder not in date_html:
            continue
        publication_url = urllib.parse.urljoin(date_url, f"{folder}/")
        found = _fetch_sequence_documents_from_urls(
            "WIPO PCT sequence listing",
            patent_id,
            missing,
            [publication_url],
            timeout_seconds,
        )
        _merge_matching(result, found.sequences, missing, found.sources)
        result.errors.extend(found.errors)

    if not result.sequences:
        result.errors.append(f"WIPO PCT: no matching sequence listing found for {folder}")
    return result


def _fetch_epo_public_sequences(
    patent_id: str,
    seq_id_references: Iterable[str],
    timeout_seconds: int,
) -> SequenceWebFetchResult:
    result = SequenceWebFetchResult(patent_id=patent_id)
    country, number = _patent_country_number(patent_id)
    if country != "EP" or not number:
        result.errors.append("EPO public documents: not an EP publication number")
        return result

    compact = f"EP{number}"
    urls = [
        f"https://register.epo.org/application?number={urllib.parse.quote(compact)}&lng=en&tab=doclist",
        "https://worldwide.espacenet.com/patent/search?"
        + urllib.parse.urlencode({"q": f"pn={compact}"}),
    ]
    found = _fetch_sequence_documents_from_urls(
        "EPO public sequence document",
        patent_id,
        seq_id_references,
        urls,
        timeout_seconds,
    )
    _merge_matching(result, found.sequences, seq_id_references, found.sources)
    result.errors.extend(found.errors)
    if not result.sequences:
        result.errors.append("EPO public documents: no matching sequence listing links found")
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
    tokens = _ncbi_patent_search_tokens(patent_id)
    if not tokens:
        result.errors.append("NCBI: no searchable patent id")
        return result
    query = " OR ".join(f'"{token}"[All Fields]' for token in tokens)
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
    records = _parse_ncbi_fasta_records(fasta, patent_id)
    for ref in seq_id_references:
        seq_num = _seq_id_number(ref)
        if seq_num is None:
            continue
        sequence = records.get(seq_num)
        if sequence:
            result.sequences[ref] = sequence
            result.sources[ref] = f"NCBI Protein patent {patent_id} sequence {seq_num}"
    return result


def _fetch_google_patents_sequences(
    patent_id: str,
    seq_id_references: Iterable[str],
    timeout_seconds: int,
) -> SequenceWebFetchResult:
    result = SequenceWebFetchResult(patent_id=patent_id)
    url = f"https://patents.google.com/patent/{urllib.parse.quote(patent_id)}/en"
    found = _fetch_sequence_documents_from_urls(
        "Google Patents",
        patent_id,
        seq_id_references,
        [url],
        timeout_seconds,
    )
    _merge_matching(result, found.sequences, seq_id_references, found.sources)
    result.errors.extend(found.errors)
    if not result.sequences:
        result.errors.append("Google Patents: no matching sequence listing text")
    return result


def _fetch_sequence_documents_from_urls(
    source_name: str,
    patent_id: str,
    seq_id_references: Iterable[str],
    urls: Iterable[str],
    timeout_seconds: int,
) -> SequenceWebFetchResult:
    result = SequenceWebFetchResult(patent_id=patent_id)
    refs = [_canonical_seq_id(ref) for ref in seq_id_references]
    refs = [ref for ref in refs if ref]
    visited: set[str] = set()

    for url in urls:
        missing = [ref for ref in refs if ref not in result.sequences]
        if not missing:
            break
        try:
            sequences, sources, linked_errors = _collect_sequences_from_url(
                url=url,
                source_name=source_name,
                timeout_seconds=timeout_seconds,
                visited=visited,
                link_budget=MAX_LINKED_SEQUENCE_DOCUMENTS,
            )
        except urllib.error.HTTPError as exc:
            result.errors.append(f"{source_name} {url}: HTTP {exc.code}")
            continue
        except urllib.error.URLError as exc:
            result.errors.append(f"{source_name} {url}: {exc}")
            continue
        _merge_matching(result, sequences, missing, sources)
        result.errors.extend(linked_errors)
    return result


def _collect_sequences_from_url(
    url: str,
    source_name: str,
    timeout_seconds: int,
    visited: set[str],
    link_budget: int,
) -> Tuple[Dict[str, str], Dict[str, str], List[str]]:
    sequences: Dict[str, str] = {}
    sources: Dict[str, str] = {}
    errors: List[str] = []
    if url in visited:
        return sequences, sources, errors
    visited.add(url)

    payload, final_url, content_type = _get_bytes_response(url, min(timeout_seconds, 10))
    direct_sequences, direct_sources = _extract_sequences_from_payload(
        payload,
        f"{source_name} {final_url}",
    )
    sequences.update(direct_sequences)
    sources.update(direct_sources)

    if link_budget <= 0:
        return sequences, sources, errors
    if "html" not in content_type.lower() and b"<html" not in payload[:500].lower():
        return sequences, sources, errors

    text = _decode_bytes(payload)
    for link in _sequence_document_links(text, final_url)[:link_budget]:
        try:
            linked_sequences, linked_sources, linked_errors = _collect_sequences_from_url(
                url=link,
                source_name=source_name,
                timeout_seconds=timeout_seconds,
                visited=visited,
                link_budget=0,
            )
        except urllib.error.HTTPError as exc:
            errors.append(f"{source_name} {link}: HTTP {exc.code}")
            continue
        except urllib.error.URLError as exc:
            errors.append(f"{source_name} {link}: {exc}")
            continue
        for ref, sequence in linked_sequences.items():
            sequences.setdefault(ref, sequence)
            sources.setdefault(ref, linked_sources.get(ref, f"{source_name} {link}"))
        errors.extend(linked_errors)
    return sequences, sources, errors


def _extract_sequences_from_payload(payload: bytes, source_label: str) -> Tuple[Dict[str, str], Dict[str, str]]:
    sequences: Dict[str, str] = {}
    sources: Dict[str, str] = {}
    if len(payload) > MAX_SEQUENCE_DOCUMENT_BYTES:
        return sequences, sources

    if zipfile.is_zipfile(io.BytesIO(payload)):
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            for item in archive.infolist():
                if item.is_dir() or item.file_size > MAX_SEQUENCE_DOCUMENT_BYTES:
                    continue
                if not _looks_like_sequence_document(item.filename):
                    continue
                document_text = _decode_bytes(archive.read(item))
                _merge_extracted_sequences(
                    sequences,
                    sources,
                    extract_reference_sequences(document_text),
                    f"{source_label}#{item.filename}",
                )
        return sequences, sources

    text = _decode_bytes(payload)
    _merge_extracted_sequences(sequences, sources, extract_reference_sequences(text), source_label)
    return sequences, sources


def _merge_extracted_sequences(
    sequences: Dict[str, str],
    sources: Dict[str, str],
    extracted: Dict[str, str],
    source_label: str,
) -> None:
    for ref, sequence in extracted.items():
        if sequence and ref not in sequences:
            sequences[ref] = sequence
            sources[ref] = source_label


def _sequence_document_links(text: str, base_url: str) -> List[str]:
    links: List[str] = []
    for raw_link in re.findall(r"""href\s*=\s*["']([^"']+)["']""", text, flags=re.IGNORECASE):
        link = urllib.parse.urljoin(base_url, html.unescape(raw_link))
        if _looks_like_sequence_document(link) and link not in links:
            links.append(link)
    return links


def _html_links(text: str, base_url: str) -> List[str]:
    links: List[str] = []
    for raw_link in re.findall(r"""href\s*=\s*["']([^"']+)["']""", text, flags=re.IGNORECASE):
        link = urllib.parse.urljoin(base_url, html.unescape(raw_link))
        if link not in links:
            links.append(link)
    return links


def _looks_like_sequence_document(name: str) -> bool:
    lower = name.lower()
    if lower.endswith((".zip", ".xml", ".seq", ".app", ".fa", ".faa", ".fasta")):
        return True
    if lower.endswith((".txt", ".htm", ".html")):
        return any(token in lower for token in ("sequence", "seqlist", "seq_list", "listing", "st25", "st26"))
    return any(token in lower for token in ("sequence", "seqlist", "seq-list", "st25", "st26"))


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


def _parse_ncbi_fasta_records(fasta: str, patent_id: str) -> Dict[int, str]:
    records: Dict[int, str] = {}
    identity = _patent_country_number(patent_id)
    current_header = ""
    current_lines: List[str] = []

    def commit() -> None:
        if not current_header:
            return
        if not _ncbi_header_matches_patent(current_header, identity):
            return
        sequence_number = _ncbi_header_sequence_number(current_header)
        if sequence_number is not None:
            records[sequence_number] = normalize_amino_acid_sequence("".join(current_lines))

    for line in fasta.splitlines():
        if line.startswith(">"):
            commit()
            current_header = line
            current_lines = []
        else:
            current_lines.append(line)
    commit()
    return records


def _ncbi_header_matches_patent(header: str, identity: tuple[str, str]) -> bool:
    country, number = identity
    if not number:
        return False
    compact_header = re.sub(r"[^A-Za-z0-9]+", "", header).upper()
    compact_patent = f"{country}{number}" if country else number
    if compact_patent and compact_patent in compact_header:
        return True
    if number not in compact_header:
        return False
    return not country or country in compact_header


def _ncbi_header_sequence_number(header: str) -> Optional[int]:
    patterns = (
        r"\bSequence\s+(\d+)\s+from\s+patent\b",
        r"\bSEQ\s+ID\s+NO[:.]?\s*(\d+)\b",
        r"\bsequence\s+identifier\s+(\d+)\b",
    )
    for pattern in patterns:
        match = re.search(pattern, header, flags=re.IGNORECASE)
        if match:
            return int(match.group(1))
    return None


def _get_json(url: str, timeout_seconds: int) -> dict:
    return json.loads(_get_text(url, timeout_seconds))


def _get_text(url: str, timeout_seconds: int) -> str:
    payload, _, _ = _get_bytes_response(url, timeout_seconds)
    return _decode_bytes(payload)


def _get_bytes_response(url: str, timeout_seconds: int) -> tuple[bytes, str, str]:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "enzyme-patent-harness/0.1 (+sequence listing research)"},
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        content_type = response.headers.get("Content-Type", "")
        payload = response.read(MAX_SEQUENCE_DOCUMENT_BYTES + 1)
        if len(payload) > MAX_SEQUENCE_DOCUMENT_BYTES:
            raise ValueError(f"Sequence document is larger than {MAX_SEQUENCE_DOCUMENT_BYTES} bytes")
        return payload, response.geturl(), content_type


def _decode_bytes(payload: bytes) -> str:
    if payload.startswith(b"\xff\xfe") or payload.startswith(b"\xfe\xff"):
        return payload.decode("utf-16", errors="replace")
    return payload.decode("utf-8-sig", errors="replace")


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


def _patent_country_number(patent_id: str) -> tuple[str, str]:
    compact = re.sub(r"[^A-Za-z0-9]", "", patent_id).upper()
    match = re.match(r"(?P<country>[A-Z]{2})(?P<number>\d{5,12})(?P<kind>[A-Z]\d?)?$", compact)
    if match:
        return match.group("country"), match.group("number")
    match = re.search(r"(?P<country>[A-Z]{2})(?P<number>\d{5,12})", compact)
    if match:
        return match.group("country"), match.group("number")
    match = re.search(r"(\d{5,12})", compact)
    return ("", match.group(1)) if match else ("", "")


def _ncbi_patent_search_tokens(patent_id: str) -> List[str]:
    country, number = _patent_country_number(patent_id)
    if not number:
        return []
    tokens = [number]
    if country:
        tokens.extend([f"{country}{number}", f"{country} {number}", f"{country}/{number}"])
    compact = re.sub(r"[^A-Za-z0-9]+", "", patent_id).upper()
    if compact and compact not in tokens:
        tokens.append(compact)
    deduped: List[str] = []
    for token in tokens:
        if token and token not in deduped:
            deduped.append(token)
    return deduped


def _wipo_publication_year(patent_id: str) -> str:
    country, number = _patent_country_number(patent_id)
    return number[:4] if country == "WO" and len(number) >= 10 else ""


def _wipo_publication_folder(patent_id: str) -> str:
    country, number = _patent_country_number(patent_id)
    if country != "WO" or len(number) < 10:
        return ""
    return f"WO{number[2:4]}_{number[4:10]}"


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
