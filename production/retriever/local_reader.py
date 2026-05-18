import logging
import os
from pathlib import Path
from typing import Optional

from core.access_control import AccessAction, DataCategory, Layer, assert_access
from core.trace_logger import TraceLogger
from core.sanitizer import Sanitizer, DataProtectionViolation

logger = logging.getLogger(__name__)

# Base path relative to this script
BASE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data", "mock_patents")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]

def retrieve_patent(patent_id: str, run_id: Optional[str] = None) -> str:
    """
    Retrieves a patent by ID from the mock directory and sanitizes it.
    Throws FileNotFoundError if not found.
    Throws DataProtectionViolation if PII is detected.
    """
    assert_access(
        Layer.PRODUCTION,
        DataCategory.PATENT_DATA,
        AccessAction.READ,
        reason=f"retrieve:{patent_id}",
    )
    file_path = os.path.join(BASE_DIR, f"{patent_id}.txt")
    
    if not os.path.exists(file_path):
        logger.error(f"Patent ID {patent_id} not found.")
        raise FileNotFoundError(f"Patent {patent_id} not found in {BASE_DIR}")
        
    with open(file_path, 'r', encoding='utf-8') as f:
        raw_text = f.read()
        
    # RULE 0: MANDATORY SANITIZATION
    logger.info(f"Passing patent {patent_id} through Sanitizer...")
    clean_text = Sanitizer.sanitize(raw_text)
    logger.info(f"Patent {patent_id} successfully sanitized.")

    if run_id:
        TraceLogger.write_event(
            run_id=run_id,
            patent_id=patent_id,
            agent_name="retriever",
            event_name="retrieved_patent",
            payload={
                "patent_id": patent_id,
                "source": "mock_patents",
                "character_count": len(clean_text),
            },
            layer=Layer.PRODUCTION,
        )
    
    return clean_text


def retrieve_patent_from_file(
    file_path: str,
    patent_id: Optional[str] = None,
    run_id: Optional[str] = None,
) -> str:
    """
    Reads a local candidate patent text file from this repository workspace.
    This is the local-upload path before real patent database connectors exist.
    """
    assert_access(
        Layer.PRODUCTION,
        DataCategory.PATENT_DATA,
        AccessAction.READ,
        reason=f"retrieve_file:{file_path}",
    )
    candidate_path = Path(file_path).resolve()
    repo_root = _repo_root().resolve()
    if candidate_path != repo_root and repo_root not in candidate_path.parents:
        raise PermissionError(f"Candidate patent file must live under {repo_root}")
    if not candidate_path.exists() or not candidate_path.is_file():
        raise FileNotFoundError(f"Candidate patent file not found: {candidate_path}")

    raw_text = candidate_path.read_text(encoding="utf-8")
    clean_text = Sanitizer.sanitize(raw_text)
    candidate_id = patent_id or candidate_path.stem

    if run_id:
        TraceLogger.write_event(
            run_id=run_id,
            patent_id=candidate_id,
            agent_name="retriever",
            event_name="retrieved_patent",
            payload={
                "patent_id": candidate_id,
                "source": str(candidate_path.relative_to(repo_root)),
                "character_count": len(clean_text),
            },
            layer=Layer.PRODUCTION,
        )

    return clean_text
