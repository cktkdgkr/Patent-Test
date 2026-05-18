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
    "patent_id": {"patent_id", "patent id", "patent", "mock_patent_id"},
    "title": {"title", "invention title", "patent title"},
    "applicant": {"applicant", "assignee", "owner"},
    "publication_number": {"publication_number", "publication number", "pub no", "publication"},
    "application_number": {"application_number", "application number", "app no", "application"},
    "patent_file": {"patent_file", "patent file", "file", "text file"},
    "claim_text": {"claim_text", "claim text", "claims", "claim", "claim 1"},
    "abstract": {"abstract", "summary"},
    "keywords": {"keywords", "keyword", "search terms", "search_terms"},
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


def _candidate_from_row(row: Dict[str, str], index: int) -> PatentCandidate:
    normalized = {
        _canonical_header(key): Sanitizer.sanitize(str(value).strip())
        for key, value in row.items()
        if value is not None and str(value).strip()
    }
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
        publication_number=normalized.get("publication_number"),
        application_number=normalized.get("application_number"),
        patent_file=normalized.get("patent_file"),
        claim_text=normalized.get("claim_text"),
        abstract=normalized.get("abstract"),
        keywords=keywords,
        metadata=metadata,
    )


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
