"""Country-code-aware patent candidate ingestion tests."""

import csv
import tempfile
from pathlib import Path

from production.ingestion import read_patent_candidates
from production.ingestion.models import PatentCandidate


def test_combined_identifier_prepends_country_code() -> None:
    candidate = PatentCandidate(
        candidate_id="row_1",
        country_code="KR",
        publication_number="10-2020-0012345",
    )
    assert candidate.combined_identifier() == "KR1020200012345"
    assert candidate.display_patent_id() == "KR1020200012345"


def test_combined_identifier_keeps_prefixed_publication_number_intact() -> None:
    candidate = PatentCandidate(
        candidate_id="row_2",
        country_code="US",
        publication_number="US11723967B2",
    )
    # Already prefixed; preserve the user's original spelling.
    assert candidate.combined_identifier() == "US11723967B2"


def test_combined_identifier_uses_application_when_publication_missing() -> None:
    candidate = PatentCandidate(
        candidate_id="row_3",
        country_code="JP",
        application_number="2020-123456",
    )
    assert candidate.combined_identifier() == "JP2020123456"


def test_combined_identifier_without_country_code_returns_bare_number() -> None:
    candidate = PatentCandidate(
        candidate_id="row_4",
        publication_number="20210012345",
    )
    assert candidate.combined_identifier() == "20210012345"


def test_combined_identifier_returns_none_when_numbers_missing() -> None:
    candidate = PatentCandidate(candidate_id="row_5", country_code="US")
    assert candidate.combined_identifier() is None
    # display_patent_id falls back to candidate_id when no number is available
    assert candidate.display_patent_id() == "row_5"


def test_reader_accepts_country_code_column() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "candidates.csv"
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["candidate_id", "country_code", "publication_number", "title"])
            writer.writerow(["row_1", "US", "11723967B2", "US case"])
            writer.writerow(["row_2", "KR", "10-2020-0012345", "KR case"])
            writer.writerow(["row_3", "Japan", "2020-9876", "JP case via country name"])
        candidates = read_patent_candidates(str(path))

    assert len(candidates) == 3
    by_id = {c.candidate_id: c for c in candidates}
    assert by_id["row_1"].country_code == "US"
    assert by_id["row_1"].combined_identifier() == "US11723967B2"
    assert by_id["row_2"].country_code == "KR"
    assert by_id["row_2"].combined_identifier() == "KR1020200012345"
    assert by_id["row_3"].country_code == "JP"
    assert by_id["row_3"].combined_identifier() == "JP20209876"


def test_reader_accepts_korean_country_column_header() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "candidates.csv"
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["candidate_id", "국가코드", "publication_number"])
            writer.writerow(["row_1", "대한민국", "10-2020-0099999"])
            writer.writerow(["row_2", "미국", "16/123456"])
        candidates = read_patent_candidates(str(path))

    by_id = {c.candidate_id: c for c in candidates}
    assert by_id["row_1"].country_code == "KR"
    assert by_id["row_1"].combined_identifier() == "KR1020200099999"
    assert by_id["row_2"].country_code == "US"
    assert by_id["row_2"].combined_identifier() == "US16123456"


def test_reader_accepts_korean_patent_number_column_header() -> None:
    """Regression: a Korean column header `특허 출원 번호` combined with
    13-digit KR publication numbers should NOT raise the sanitizer's
    DRIVER_LICENSE_KR or BANK_ACCOUNT_LIKE patterns.
    """
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "candidates.csv"
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["candidate_id", "특허 출원 번호", "국가코드"])
            writer.writerow(["row_1", "2025-10536859", "CN"])
            writer.writerow(["row_2", "10-2025-7013439", "KR"])
            writer.writerow(["row_3", "10-2024-0087254", "KR"])
            writer.writerow(["row_4", "14/323932", "US"])
            writer.writerow(["row_5", "2016-247708", "JP"])
        candidates = read_patent_candidates(str(path))

    by_id = {c.candidate_id: c for c in candidates}
    assert by_id["row_1"].application_number == "2025-10536859"
    assert by_id["row_1"].combined_identifier() == "CN202510536859"
    assert by_id["row_2"].application_number == "10-2025-7013439"
    assert by_id["row_2"].combined_identifier() == "KR1020257013439"
    assert by_id["row_4"].combined_identifier() == "US14323932"
    assert by_id["row_5"].combined_identifier() == "JP2016247708"


def test_sanitizer_no_longer_flags_patent_numbers() -> None:
    """Direct sanitizer-level regression: real patent identifiers must not
    trigger DRIVER_LICENSE_KR / BANK_ACCOUNT_LIKE while a true KR driver
    license format still does.
    """
    from core.sanitizer import DataProtectionViolation, Sanitizer

    safe_values = [
        "2025-10536859",        # CN application
        "10-2025-7013439",      # KR application
        "10-2024-0087254",      # KR application
        "10-2024-0194689",      # KR application
        "14/323932",            # US application
        "2016-247708",          # JP application
        "KR1020257013439",      # combined identifier
        "CN202510536859",       # combined identifier
    ]
    for value in safe_values:
        Sanitizer.sanitize(value)  # would raise if blocked

    # A real Korean DL still gets blocked
    try:
        Sanitizer.sanitize("12-34-567890-12")
        raise AssertionError("Korean driver license must still be blocked")
    except DataProtectionViolation:
        pass


def main() -> int:
    test_combined_identifier_prepends_country_code()
    test_combined_identifier_keeps_prefixed_publication_number_intact()
    test_combined_identifier_uses_application_when_publication_missing()
    test_combined_identifier_without_country_code_returns_bare_number()
    test_combined_identifier_returns_none_when_numbers_missing()
    test_reader_accepts_country_code_column()
    test_reader_accepts_korean_country_column_header()
    test_reader_accepts_korean_patent_number_column_header()
    test_sanitizer_no_longer_flags_patent_numbers()
    print("country code ingestion: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
