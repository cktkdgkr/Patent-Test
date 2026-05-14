import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from core.access_control import AccessAction, DataCategory, Layer, assert_access
from core.sanitizer import Sanitizer


SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9_.-]+")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _safe_name(value: str) -> str:
    cleaned = SAFE_NAME_RE.sub("_", value.strip())
    return cleaned[:120] or "unknown"


class TraceLogger:
    """Writes raw, sanitized execution traces in the Layer 3 experience layout."""

    @staticmethod
    def start_run(prefix: str = "run") -> str:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        return f"{_safe_name(prefix)}_{timestamp}_{uuid4().hex[:8]}"

    @classmethod
    def write_event(
        cls,
        run_id: str,
        patent_id: str,
        agent_name: str,
        event_name: str,
        payload: Mapping[str, Any],
        layer: Layer,
    ) -> Path:
        assert_access(
            layer,
            DataCategory.RAW_TRACE_LOGS,
            AccessAction.WRITE,
            reason=f"trace:{agent_name}:{event_name}",
        )
        clean_payload = Sanitizer.sanitize_payload(dict(payload))
        base = (
            _repo_root()
            / "meta_harness_workspace"
            / "experience"
            / _safe_name(run_id)
            / _safe_name(patent_id)
            / _safe_name(agent_name)
        )
        base.mkdir(parents=True, exist_ok=True)
        path = base / f"{_safe_name(event_name)}.json"
        envelope = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "run_id": run_id,
            "patent_id": patent_id,
            "agent_name": agent_name,
            "event_name": event_name,
            "payload": clean_payload,
        }
        with path.open("w", encoding="utf-8") as handle:
            json.dump(envelope, handle, ensure_ascii=True, indent=2, sort_keys=True)
        return path
