import csv
import re
import zipfile
from pathlib import Path
from typing import Dict, Iterable, List, Optional
from xml.etree import ElementTree

from core.sanitizer import Sanitizer
from production.ingestion.models import PatentCandidate


HEADER_ALIASES = {
    "candidate_id": {"candidate_id", "candidate id", "id", "row_id", "row id"},
    "patent_id": {
        "patent_id",
        "patent id",
        "patent",
        "mock_patent_id",
        "특허번호",
        "특허 번호",
        "등록번호",
        "등록 번호",
        "특허등록번호",
        "특허 등록 번호",
    },
    "title": {"title", "invention title", "patent title", "특허명", "특허 명", "발명의 명칭"},
    "applicant": {"applicant", "assignee", "owner", "출원인", "권리자"},
    "country_code": {
        "country_code",
        "country code",
        "country",
        "cc",
        "country (cc)",
        "jurisdiction",
        "jurisdiction code",
        "office",
        "patent office",
        "kind country",
        "국가",
        "국가코드",
        "출원국",
        "공개국가",
        "공개국",
    },
    "publication_number": {
        "publication_number",
        "publication number",
        "pub no",
        "publication",
        "공개번호",
        "공개 번호",
        "특허 공개 번호",
        "특허공개번호",
    },
    "application_number": {
        "application_number",
        "application number",
        "app no",
        "application",
        "출원번호",
        "출원 번호",
        "특허 출원 번호",
        "특허 출원번호",
        "특허출원번호",
    },
    "patent_file": {"patent_file", "patent file", "file", "text file"},
    "claim_text": {"claim_text", "claim text", "claims", "claim", "claim 1", "청구항"},
    "abstract": {"abstract", "summary", "요약"},
    "keywords": {"keywords", "keyword", "search terms", "search_terms", "키워드"},
}

NS = {
    "main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
}


def read_patent_candidates(path: str) -> List[PatentCandidate]:
    source = Path(path)
    suffix = source.suffix.lower()
    if suffix == ".csv":
        rows = _read_csv(source)
    elif suffix == ".xlsx":
        rows = _read_xlsx(source)
    else:
        raise ValueError(f"Unsupported patent candidate file type: {source.suffix}")
    return [_candidate_from_row(row, index) for index, row in enumerate(rows, start=1)]


def _read_csv(path: Path) -> List[Dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _read_xlsx(path: Path) -> List[Dict[str, str]]:
    with zipfile.ZipFile(path) as archive:
        shared_strings = _shared_strings(archive)
        sheet_name = _first_sheet_name(archive)
        root = ElementTree.fromstring(archive.read(sheet_name))

    matrix: List[List[str]] = []
    for row in root.findall(".//main:sheetData/main:row", NS):
        values: Dict[int, str] = {}
        for cell in row.findall("main:c", NS):
            column = _column_index(cell.attrib.get("r", ""))
            values[column] = _cell_value(cell, shared_strings)
        if values:
            width = max(values) + 1
            matrix.append([values.get(index, "") for index in range(width)])

    if not matrix:
        return []
    headers = [_normalize_header(value) for value in matrix[0]]
    rows: List[Dict[str, str]] = []
    for raw_row in matrix[1:]:
        row = {
            headers[index]: raw_row[index].strip()
            for index in range(min(len(headers), len(raw_row)))
            if headers[index]
        }
        if any(row.values()):
            rows.append(row)
    return rows


def _shared_strings(archive: zipfile.ZipFile) -> List[str]:
    try:
        root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
    except KeyError:
        return []
    values: List[str] = []
    for item in root.findall("main:si", NS):
        parts = [node.text or "" for node in item.findall(".//main:t", NS)]
        values.append("".join(parts))
    return values


def _first_sheet_name(archive: zipfile.ZipFile) -> str:
    workbook = ElementTree.fromstring(archive.read("xl/workbook.xml"))
    first_sheet = workbook.find(".//main:sheets/main:sheet", NS)
    if first_sheet is None:
        raise ValueError("XLSX workbook has no sheets")
    # The local MVP writes/reads simple workbooks where the first sheet is sheet1.
    sheet_id = first_sheet.attrib.get("sheetId", "1")
    return f"xl/worksheets/sheet{sheet_id}.xml"


def _cell_value(cell: ElementTree.Element, shared_strings: List[str]) -> str:
    cell_type = cell.attrib.get("t")
    if cell_type == "inlineStr":
        parts = [node.text or "" for node in cell.findall(".//main:t", NS)]
        return "".join(parts)
    value_node = cell.find("main:v", NS)
    if value_node is None or value_node.text is None:
        return ""
    raw = value_node.text
    if cell_type == "s":
        index = int(raw)
        return shared_strings[index] if index < len(shared_strings) else ""
    return raw


def _column_index(cell_ref: str) -> int:
    letters = re.sub(r"[^A-Z]", "", cell_ref.upper())
    total = 0
    for letter in letters:
        total = total * 26 + (ord(letter) - ord("A") + 1)
    return max(total - 1, 0)


_STRUCTURED_ID_FIELDS = {
    "candidate_id",
    "patent_id",
    "publication_number",
    "application_number",
    "patent_file",
}


def _candidate_from_row(row: Dict[str, str], index: int) -> PatentCandidate:
    # Patent identifier fields are structured numeric/alphanumeric IDs (e.g.
    # KR publication numbers like ``10-2020-0012345``) that resemble
    # bank-account or credit-card patterns but never carry PII destined for
    # the LLM. Validate their shape, skip the heavy sanitizer, and keep full
    # sanitization on free-text fields (title, claim_text, abstract, ...).
    # ``country_code`` accepts localized strings (예: "미국", "대한민국") and
    # is normalized downstream, so it bypasses the strict identifier check.
    normalized: Dict[str, str] = {}
    for key, value in row.items():
        if value is None:
            continue
        text = str(value).strip()
        if not text:
            continue
        canonical = _canonical_header(key)
        if canonical in _STRUCTURED_ID_FIELDS:
            normalized[canonical] = _validate_identifier(canonical, text)
        elif canonical == "country_code":
            normalized[canonical] = text
        else:
            normalized[canonical] = Sanitizer.sanitize(text)
    metadata = {
        key: value
        for key, value in normalized.items()
        if key not in PatentCandidate.model_fields
    }
    keywords = _split_keywords(normalized.get("keywords"))
    candidate_id = normalized.get("candidate_id") or normalized.get("patent_id") or f"row_{index}"
    return PatentCandidate(
        candidate_id=candidate_id,
        patent_id=normalized.get("patent_id"),
        title=normalized.get("title"),
        applicant=normalized.get("applicant"),
        country_code=_normalize_country_code(normalized.get("country_code")),
        publication_number=normalized.get("publication_number"),
        application_number=normalized.get("application_number"),
        patent_file=normalized.get("patent_file"),
        claim_text=normalized.get("claim_text"),
        abstract=normalized.get("abstract"),
        keywords=keywords,
        metadata=metadata,
    )


_COUNTRY_NAME_TO_CODE = {
    "us": "US",
    "usa": "US",
    "united states": "US",
    "미국": "US",
    "kr": "KR",
    "korea": "KR",
    "republic of korea": "KR",
    "south korea": "KR",
    "대한민국": "KR",
    "한국": "KR",
    "jp": "JP",
    "japan": "JP",
    "일본": "JP",
    "cn": "CN",
    "china": "CN",
    "중국": "CN",
    "ep": "EP",
    "epo": "EP",
    "europe": "EP",
    "european patent": "EP",
    "유럽": "EP",
    "wo": "WO",
    "wipo": "WO",
    "pct": "WO",
    "world": "WO",
    "gb": "GB",
    "uk": "GB",
    "united kingdom": "GB",
    "영국": "GB",
    "de": "DE",
    "germany": "DE",
    "독일": "DE",
    "fr": "FR",
    "france": "FR",
    "프랑스": "FR",
    "ca": "CA",
    "canada": "CA",
    "캐나다": "CA",
    "au": "AU",
    "australia": "AU",
    "호주": "AU",
    "in": "IN",
    "india": "IN",
    "인도": "IN",
    "tw": "TW",
    "taiwan": "TW",
    "대만": "TW",
}


_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9._/\-\\ ]+$")
_IDENTIFIER_MAX_LEN = 128


def _validate_identifier(field: str, value: str) -> str:
    """Lightweight shape check for structured ID columns; rejects free-text
    PII while permitting patent identifier formats (US11723967B2,
    KR10-2020-0012345, EP 3 000 000 A1, file paths under data/patent_cache/...).
    """
    if len(value) > _IDENTIFIER_MAX_LEN:
        raise ValueError(f"{field} value exceeds {_IDENTIFIER_MAX_LEN} characters")
    if not _IDENTIFIER_PATTERN.match(value):
        raise ValueError(
            f"{field} value contains characters outside the allowed identifier set: {value!r}"
        )
    return value


def _normalize_country_code(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    cleaned = str(value).strip()
    if not cleaned:
        return None
    upper = cleaned.upper()
    if re.fullmatch(r"[A-Z]{2}", upper):
        return upper
    lookup = cleaned.lower()
    if lookup in _COUNTRY_NAME_TO_CODE:
        return _COUNTRY_NAME_TO_CODE[lookup]
    # Pull a 2-letter prefix from forms like "US-United States" or "US (United States)"
    prefix = re.match(r"^([A-Za-z]{2})\b", upper)
    if prefix:
        return prefix.group(1)
    return None


def _canonical_header(header: str) -> str:
    normalized = _normalize_header(header)
    for canonical, aliases in HEADER_ALIASES.items():
        if normalized in aliases:
            return canonical
    return normalized.replace(" ", "_")


def _normalize_header(value: str) -> str:
    return " ".join(str(value).strip().lower().replace("-", " ").replace("_", " ").split())


def _split_keywords(value: Optional[str]) -> List[str]:
    if not value:
        return []
    return [part.strip() for part in re.split(r"[,;|]", value) if part.strip()]
