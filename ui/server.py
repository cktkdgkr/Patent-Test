import argparse
import asyncio
import base64
import json
import mimetypes
import re
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
STATIC_ROOT = Path(__file__).resolve().parent / "static"
UPLOAD_ROOT = ROOT / "build_log" / "ui_uploads"
REPORT_ROOT = ROOT / "build_log" / "ui_reports"
MAX_UPLOAD_BYTES = 12 * 1024 * 1024
GRADE_ORDER = ("HIGH", "MEDIUM", "LOW", "SAFE")
REPORTS_LIST_LIMIT = 200
RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9_\-]+$")

import sys

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from production.ingestion import read_patent_candidates
from production.orchestrator import ProductSpec, screen_product_candidate_batch


class UIRequestError(Exception):
    pass


def run_screening_payload(payload: dict[str, Any]) -> dict[str, Any]:
    product = _product_from_payload(payload.get("product") or {})
    upload_path = _write_upload(payload.get("file") or {})
    return _run_screening(
        product=product,
        candidates_path=upload_path,
        allow_web_fetch=bool(payload.get("enable_web_fetch")),
        user_id=_optional_string(payload.get("user_id")),
    )


def run_example_payload(user_id: str | None = None) -> dict[str, Any]:
    product = ProductSpec(**json.loads((ROOT / "examples" / "product_alpha.json").read_text(encoding="utf-8")))
    return _run_screening(
        product=product,
        candidates_path=ROOT / "examples" / "patent_candidates_sequence.csv",
        allow_web_fetch=False,
        user_id=user_id,
    )


def _run_screening(
    product: ProductSpec,
    candidates_path: Path,
    allow_web_fetch: bool,
    user_id: str | None = None,
) -> dict[str, Any]:
    candidates = read_patent_candidates(str(candidates_path))
    report = asyncio.run(
        screen_product_candidate_batch(
            product=product,
            candidates=candidates,
            source_file=str(candidates_path),
            allow_web_fetch=allow_web_fetch,
        )
    )
    REPORT_ROOT.mkdir(parents=True, exist_ok=True)
    report_path = REPORT_ROOT / f"{report.run_id}.json"
    report_payload = json.loads(report.model_dump_json())
    generated_at = datetime.now(timezone.utc).isoformat()
    summary = report_payload.get("summary_by_grade") or {}
    wrapper = {
        "version": 2,
        "run_id": report.run_id,
        "user_id": user_id,
        "generated_at": generated_at,
        "product_id": product.product_id,
        "summary_by_grade": summary,
        "total_candidates": report_payload.get("total_candidates"),
        "screened_count": report_payload.get("screened_count"),
        "failed_count": len(report_payload.get("failed_candidates") or []),
        "top_grade": _top_grade(summary),
        "report": report_payload,
    }
    report_path.write_text(json.dumps(wrapper, indent=2, ensure_ascii=False), encoding="utf-8")
    return {
        "report": report_payload,
        "report_path": str(report_path.relative_to(ROOT)),
        "generated_at": generated_at,
        "user_id": user_id,
        "run_id": report.run_id,
    }


def list_reports_payload(query: dict[str, str]) -> dict[str, Any]:
    user_id = (query.get("user_id") or "").strip() or None
    product_id = (query.get("product_id") or "").strip().lower() or None
    grade = (query.get("grade") or "").strip().upper() or None
    from_date = (query.get("from_date") or "").strip() or None
    to_date = (query.get("to_date") or "").strip() or None
    if grade and grade not in GRADE_ORDER:
        raise UIRequestError("grade must be HIGH, MEDIUM, LOW, or SAFE")

    items: list[dict[str, Any]] = []
    if REPORT_ROOT.exists():
        for path in REPORT_ROOT.glob("*.json"):
            try:
                meta = _read_report_file(path, include_report=False)
            except Exception:
                continue
            if user_id and (meta.get("user_id") or "") != user_id:
                continue
            if product_id and product_id not in (meta.get("product_id") or "").lower():
                continue
            if grade:
                if not (meta.get("summary_by_grade") or {}).get(grade):
                    continue
            ga = meta.get("generated_at") or ""
            if from_date and ga[:10] < from_date:
                continue
            if to_date and ga[:10] > to_date:
                continue
            items.append(meta)

    items.sort(key=lambda x: x.get("generated_at") or "", reverse=True)
    truncated = len(items) > REPORTS_LIST_LIMIT
    return {
        "reports": items[:REPORTS_LIST_LIMIT],
        "total": len(items),
        "truncated": truncated,
        "limit": REPORTS_LIST_LIMIT,
    }


def get_report_payload(run_id: str) -> dict[str, Any]:
    if not RUN_ID_PATTERN.match(run_id):
        raise UIRequestError("Invalid run_id")
    path = REPORT_ROOT / f"{run_id}.json"
    if not path.exists():
        raise UIRequestError("Report not found")
    wrapper = _read_report_file(path, include_report=True)
    return {
        "report": wrapper.get("report") or {},
        "run_id": wrapper.get("run_id") or run_id,
        "user_id": wrapper.get("user_id"),
        "generated_at": wrapper.get("generated_at"),
        "product_id": wrapper.get("product_id"),
    }


def _read_report_file(path: Path, include_report: bool) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict) and raw.get("version") == 2 and "report" in raw:
        meta = {
            "run_id": raw.get("run_id") or path.stem,
            "user_id": raw.get("user_id"),
            "generated_at": raw.get("generated_at"),
            "product_id": raw.get("product_id"),
            "summary_by_grade": raw.get("summary_by_grade") or {},
            "total_candidates": raw.get("total_candidates"),
            "screened_count": raw.get("screened_count"),
            "failed_count": raw.get("failed_count"),
            "top_grade": raw.get("top_grade"),
        }
        if include_report:
            meta["report"] = raw.get("report") or {}
        return meta

    # Legacy format: the file IS the raw report.
    summary = (raw.get("summary_by_grade") if isinstance(raw, dict) else None) or {}
    meta = {
        "run_id": (raw.get("run_id") if isinstance(raw, dict) else None) or path.stem,
        "user_id": None,
        "generated_at": _legacy_generated_at(path, raw),
        "product_id": _legacy_product_id(raw),
        "summary_by_grade": summary,
        "total_candidates": raw.get("total_candidates") if isinstance(raw, dict) else None,
        "screened_count": raw.get("screened_count") if isinstance(raw, dict) else None,
        "failed_count": len((raw.get("failed_candidates") or []) if isinstance(raw, dict) else []),
        "top_grade": _top_grade(summary),
    }
    if include_report:
        meta["report"] = raw if isinstance(raw, dict) else {}
    return meta


def _legacy_generated_at(path: Path, raw: Any) -> str:
    if isinstance(raw, dict):
        value = raw.get("generated_at") or raw.get("created_at")
        if isinstance(value, str) and value:
            return value
    mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    return mtime.isoformat()


def _legacy_product_id(raw: Any) -> str | None:
    if not isinstance(raw, dict):
        return None
    product = raw.get("product")
    if isinstance(product, dict):
        value = product.get("product_id")
        if isinstance(value, str):
            return value
    return None


def _top_grade(summary: dict[str, Any] | None) -> str | None:
    summary = summary or {}
    for grade in GRADE_ORDER:
        if summary.get(grade):
            return grade
    return None


def _product_from_payload(data: dict[str, Any]) -> ProductSpec:
    if not isinstance(data, dict):
        raise UIRequestError("product must be an object")
    cleaned = {
        "product_id": str(data.get("product_id") or "ui_product").strip() or "ui_product",
        "enzyme_name": _optional_string(data.get("enzyme_name")),
        "amino_acid_sequence": _optional_string(data.get("amino_acid_sequence")),
        "fasta_text": _optional_string(data.get("fasta_text")),
        "reference_sequence_id": _optional_string(data.get("reference_sequence_id")),
        "alignment_backend": _alignment_backend(data.get("alignment_backend")),
        "identity": _optional_float(data.get("identity"), "identity"),
        "ph": _optional_float(data.get("ph"), "ph"),
        "temperature_c": _optional_float(data.get("temperature_c"), "temperature_c"),
        "substrate": _optional_string(data.get("substrate")),
        "enzyme_class": _optional_string(data.get("enzyme_class")),
        "variant": _optional_string(data.get("variant")),
        "activity": _optional_string(data.get("activity")),
        "organism": _optional_string(data.get("organism")),
        "use_case": _optional_string(data.get("use_case")),
        "jurisdiction": _optional_string(data.get("jurisdiction")),
        "launch_date": _optional_string(data.get("launch_date")),
        "metadata": {"source": "ui"},
    }
    return ProductSpec(**cleaned)


def _write_upload(file_payload: dict[str, Any]) -> Path:
    if not isinstance(file_payload, dict):
        raise UIRequestError("file must be an object")
    filename = _safe_filename(str(file_payload.get("name") or "patent_candidates.csv"))
    suffix = Path(filename).suffix.lower()
    if suffix not in {".csv", ".xlsx"}:
        raise UIRequestError("Only .csv and .xlsx files are supported")
    encoded = file_payload.get("content_base64")
    if not encoded:
        raise UIRequestError("Upload a candidate patent CSV or XLSX file")
    try:
        raw = base64.b64decode(str(encoded), validate=True)
    except ValueError as exc:
        raise UIRequestError("Uploaded file content is not valid base64") from exc
    if len(raw) > MAX_UPLOAD_BYTES:
        raise UIRequestError("Uploaded file is larger than 12 MB")

    UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = UPLOAD_ROOT / f"{timestamp}_{filename}"
    path.write_bytes(raw)
    return path


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_float(value: Any, field_name: str) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise UIRequestError(f"{field_name} must be a number") from exc


def _alignment_backend(value: Any) -> str:
    backend = str(value or "auto").strip().lower()
    if backend not in {"auto", "needleman_wunsch", "blastp", "mmseqs"}:
        raise UIRequestError("alignment_backend must be auto, needleman_wunsch, blastp, or mmseqs")
    return backend


def _safe_filename(value: str) -> str:
    name = Path(value).name
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")
    return name or "patent_candidates.csv"


class PatentUIHandler(BaseHTTPRequestHandler):
    server_version = "PatentHarnessUI/0.1"

    def do_GET(self) -> None:
        parsed = urlsplit(self.path)
        path = parsed.path
        if path == "/api/health":
            self._send_json({"ok": True})
            return
        if path == "/api/reports":
            self._handle_list_reports(parsed.query)
            return
        match = re.match(r"^/api/reports/([A-Za-z0-9_\-]+)$", path)
        if match:
            self._handle_get_report(match.group(1))
            return
        self._serve_static()

    def do_POST(self) -> None:
        path = urlsplit(self.path).path
        if path == "/api/screen":
            self._handle_json_action(run_screening_payload)
            return
        if path == "/api/example":
            self._handle_json_action(
                lambda payload: run_example_payload(user_id=_optional_string((payload or {}).get("user_id")))
            )
            return
        self._send_json({"error": "Not found"}, status=HTTPStatus.NOT_FOUND)

    def _handle_list_reports(self, query_string: str) -> None:
        parsed = parse_qs(query_string, keep_blank_values=False)
        query = {key: values[0] for key, values in parsed.items() if values}
        try:
            self._send_json(list_reports_payload(query))
        except UIRequestError as exc:
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
        except Exception as exc:  # pragma: no cover - surfaced to client
            self._send_json({"error": str(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def _handle_get_report(self, run_id: str) -> None:
        try:
            self._send_json(get_report_payload(run_id))
        except UIRequestError as exc:
            status = (
                HTTPStatus.NOT_FOUND
                if str(exc).lower().endswith("not found")
                else HTTPStatus.BAD_REQUEST
            )
            self._send_json({"error": str(exc)}, status=status)
        except Exception as exc:  # pragma: no cover - surfaced to client
            self._send_json({"error": str(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def log_message(self, format: str, *args: Any) -> None:
        print(f"[ui] {self.address_string()} - {format % args}")

    def _handle_json_action(self, action) -> None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length)
            payload = json.loads(raw.decode("utf-8") or "{}")
            self._send_json(action(payload))
        except UIRequestError as exc:
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            self._send_json({"error": str(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def _serve_static(self) -> None:
        request_path = self.path.split("?", 1)[0]
        if request_path in {"", "/"}:
            request_path = "/index.html"
        relative = request_path.lstrip("/")
        path = (STATIC_ROOT / relative).resolve()
        try:
            path.relative_to(STATIC_ROOT.resolve())
        except ValueError:
            self._send_json({"error": "Invalid path"}, status=HTTPStatus.BAD_REQUEST)
            return
        if not path.exists() or not path.is_file():
            self._send_json({"error": "Not found"}, status=HTTPStatus.NOT_FOUND)
            return
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(path.stat().st_size))
        self.end_headers()
        self.wfile.write(path.read_bytes())

    def _send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the enzyme patent harness UI.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    httpd = ThreadingHTTPServer((args.host, args.port), PatentUIHandler)
    print(f"Patent harness UI running at http://{args.host}:{args.port}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping patent harness UI.")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
