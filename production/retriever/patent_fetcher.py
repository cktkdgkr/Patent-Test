import re
from pathlib import Path
from typing import Optional

from pydantic import BaseModel

from core.access_control import AccessAction, DataCategory, Layer, assert_access
from core.sanitizer import Sanitizer
from core.trace_logger import TraceLogger
from production.retriever.web_fetcher import fetch_google_patents_claims, write_web_cache


class PatentFetchUnavailable(Exception):
    """Raised when a patent identifier cannot be resolved by configured fetchers."""


class PatentFetchResult(BaseModel):
    patent_id: str
    source: str
    raw_text: str


def fetch_patent_by_identifier(
    identifier: str,
    run_id: Optional[str] = None,
    allow_web: bool = False,
) -> PatentFetchResult:
    """
    Resolves a patent/publication/application identifier to local claim text.
    The current MVP supports local cache and mock patents; external patent
    database connectors can plug in behind this interface next.
    """
    clean_identifier = Sanitizer.sanitize(identifier.strip())
    if not clean_identifier:
        raise PatentFetchUnavailable("empty patent identifier")

    assert_access(
        Layer.PRODUCTION,
        DataCategory.PATENT_DATA,
        AccessAction.READ,
        reason=f"fetch_patent:{clean_identifier}",
    )

    for path in _candidate_paths(clean_identifier):
        if path.exists() and path.is_file():
            raw_text = Sanitizer.sanitize(path.read_text(encoding="utf-8"))
            _trace_fetch(run_id, clean_identifier, path, raw_text)
            return PatentFetchResult(
                patent_id=clean_identifier,
                source=str(path.relative_to(_repo_root())),
                raw_text=raw_text,
            )

    if allow_web:
        source_url, raw_text = fetch_google_patents_claims(clean_identifier)
        cache_path = write_web_cache(clean_identifier, raw_text)
        _trace_fetch(run_id, clean_identifier, cache_path, raw_text)
        return PatentFetchResult(
            patent_id=clean_identifier,
            source=source_url,
            raw_text=raw_text,
        )

    raise PatentFetchUnavailable(
        "No local patent text found for identifier "
        f"{clean_identifier}. Add claim text, patent_file, or a cache file under data/patent_cache."
    )


def _candidate_paths(identifier: str) -> list[Path]:
    root = _repo_root()
    normalized = _normalize_identifier(identifier)
    raw = identifier.strip()
    names = {
        raw,
        normalized,
        raw.replace("/", "_").replace("-", "_"),
        normalized.replace("/", "_").replace("-", "_"),
    }
    return [
        root / "data" / "patent_cache" / f"{name}.txt"
        for name in names
    ] + [
        root / "data" / "patent_cache" / "web" / f"{name}.txt"
        for name in names
    ] + [
        root / "data" / "mock_patents" / f"{name}.txt"
        for name in names
    ]


def _normalize_identifier(identifier: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "", identifier).upper()


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _trace_fetch(run_id: Optional[str], patent_id: str, path: Path, raw_text: str) -> None:
    if not run_id:
        return
    TraceLogger.write_event(
        run_id=run_id,
        patent_id=patent_id,
        agent_name="patent_fetcher",
        event_name="fetched_patent",
        payload={
            "patent_id": patent_id,
            "source": str(path.relative_to(_repo_root())),
            "character_count": len(raw_text),
        },
        layer=Layer.PRODUCTION,
    )
