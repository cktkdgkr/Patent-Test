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

ROOT = Path(__file__).resolve().parents[1]
STATIC_ROOT = Path(__file__).resolve().parent / "static"
UPLOAD_ROOT = ROOT / "build_log" / "ui_uploads"
REPORT_ROOT = ROOT / "build_log" / "ui_reports"
MAX_UPLOAD_BYTES = 12 * 1024 * 1024

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
    )


def run_example_payload() -> dict[str, Any]:
    product = ProductSpec(**json.loads((ROOT / "examples" / "product_alpha.json").read_text(encoding="utf-8")))
    return _run_screening(
        product=product,
        candidates_path=ROOT / "examples" / "patent_candidates_web.csv",
        allow_web_fetch=True,
    )


def _run_screening(
    product: ProductSpec,
    candidates_path: Path,
    allow_web_fetch: bool,
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
    report_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    return {
        "report": json.loads(report.model_dump_json()),
        "report_path": str(report_path.relative_to(ROOT)),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def _product_from_payload(data: dict[str, Any]) -> ProductSpec:
    if not isinstance(data, dict):
        raise UIRequestError("product must be an object")
    cleaned = {
        "product_id": str(data.get("product_id") or "ui_product").strip() or "ui_product",
        "enzyme_name": _optional_string(data.get("enzyme_name")),
        "identity": _optional_float(data.get("identity"), "identity"),
        "ph": _optional_float(data.get("ph"), "ph"),
        "temperature_c": _optional_float(data.get("temperature_c"), "temperature_c"),
        "substrate": _optional_string(data.get("substrate")),
        "enzyme_class": _optional_string(data.get("enzyme_class")),
        "variant": _optional_string(data.get("variant")),
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


def _safe_filename(value: str) -> str:
    name = Path(value).name
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")
    return name or "patent_candidates.csv"


class PatentUIHandler(BaseHTTPRequestHandler):
    server_version = "PatentHarnessUI/0.1"

    def do_GET(self) -> None:
        if self.path == "/api/health":
            self._send_json({"ok": True})
            return
        self._serve_static()

    def do_POST(self) -> None:
        if self.path == "/api/screen":
            self._handle_json_action(run_screening_payload)
            return
        if self.path == "/api/example":
            self._handle_json_action(lambda _payload: run_example_payload())
            return
        self._send_json({"error": "Not found"}, status=HTTPStatus.NOT_FOUND)

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
