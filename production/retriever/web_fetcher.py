import logging
import os
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

from core.sanitizer import Sanitizer

try:
    import truststore  # type: ignore[import-not-found]

    _HAS_TRUSTSTORE = True
except Exception:  # pragma: no cover - optional dependency
    truststore = None  # type: ignore[assignment]
    _HAS_TRUSTSTORE = False


logger = logging.getLogger(__name__)


def _build_ssl_context() -> ssl.SSLContext:
    """Build the SSL context used for outbound HTTPS calls.

    Resolution order:
    1. ``PATENT_HARNESS_INSECURE_SSL=1`` → certificate verification is
       disabled. Use only as a temporary workaround (logged at WARNING).
    2. ``truststore`` installed → SSL context backed by the operating
       system's certificate store. On Windows this picks up corporate CAs
       installed via group policy, which is what unblocks fetches behind a
       Zscaler / Cisco Umbrella / Bluecoat style SSL inspection proxy.
    3. Fallback → ``ssl.create_default_context()`` using the bundled
       ``certifi`` roots (works on the open internet).
    """
    if os.getenv("PATENT_HARNESS_INSECURE_SSL", "").strip().lower() in {"1", "true", "yes"}:
        logger.warning(
            "PATENT_HARNESS_INSECURE_SSL is set; outbound HTTPS calls will skip "
            "certificate verification. Fix corporate trust roots and unset this "
            "as soon as possible."
        )
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
    if _HAS_TRUSTSTORE:
        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    return ssl.create_default_context()


class WebPatentFetchError(Exception):
    """Raised when a public web patent source cannot return usable claims."""


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        text = " ".join(data.split())
        if text:
            self.parts.append(text)

    def text(self) -> str:
        return "\n".join(self.parts)


class _ClaimSectionExtractor(HTMLParser):
    _VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
    _BREAK_TAGS = {"br", "div", "h2", "li", "p", "section"}

    def __init__(self) -> None:
        super().__init__()
        self.in_claims = False
        self.depth = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if not self.in_claims and tag == "section" and attributes.get("itemprop") == "claims":
            self.in_claims = True
            self.depth = 1
            return
        if self.in_claims:
            if tag in self._BREAK_TAGS:
                self.parts.append("\n")
            if tag not in self._VOID_TAGS:
                self.depth += 1

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.in_claims and tag in self._BREAK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if not self.in_claims:
            return
        if tag in self._BREAK_TAGS:
            self.parts.append("\n")
        if tag not in self._VOID_TAGS:
            self.depth -= 1
        if self.depth <= 0:
            self.in_claims = False

    def handle_data(self, data: str) -> None:
        if not self.in_claims:
            return
        text = " ".join(data.split())
        if text:
            self.parts.append(text)

    def text(self) -> str:
        return "\n".join(self.parts)


def fetch_google_patents_claims(identifier: str, timeout_seconds: int = 20) -> tuple[str, str]:
    """
    Fetches claims text from a public Google Patents page.
    This is a fast preview connector, not an official legal-record source.
    """
    clean_identifier = Sanitizer.sanitize(identifier.strip())
    url = f"https://patents.google.com/patent/{urllib.parse.quote(clean_identifier)}/en"
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "enzyme-patent-harness/0.1 (+local screening research)",
        },
    )
    context = _build_ssl_context()
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds, context=context) as response:
            html = response.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as exc:
        raise WebPatentFetchError(f"Could not fetch {url}: {exc}") from exc

    claims_text = extract_claims_from_google_patents_html(html)
    return url, claims_text


def extract_claims_from_google_patents_html(html: str) -> str:
    section_parser = _ClaimSectionExtractor()
    section_parser.feed(html)
    claims = _normalize_claim_text(section_parser.text())
    claims = re.sub(r"^Claims\s*\(\s*\d+\s*\)\s*", "", claims, flags=re.IGNORECASE)
    if re.search(r"^\s*1\.", claims, flags=re.MULTILINE):
        return Sanitizer.sanitize(claims)

    parser = _TextExtractor()
    parser.feed(html)
    text = parser.text()

    match = re.search(
        r"Claims\s*\(\s*\d+\s*\)\s*(?P<claims>.*?)(?:Priority Applications|Applications Claiming Priority|Patent Citations|Family|Similar Documents)",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not match:
        raise WebPatentFetchError("Could not locate a Claims section in the patent page")

    claims = _normalize_claim_text(match.group("claims"))
    if not re.search(r"^\s*1\.", claims, flags=re.MULTILINE):
        raise WebPatentFetchError("Fetched page did not contain numbered claims")
    return Sanitizer.sanitize(claims)


def write_web_cache(identifier: str, claims_text: str) -> Path:
    path = _repo_root() / "data" / "patent_cache" / "web" / f"{_safe_identifier(identifier)}.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(claims_text, encoding="utf-8")
    return path


def _normalize_claim_text(text: str) -> str:
    text = re.sub(r"\n+", "\n", text)
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"(?<=\d)\s+\.\s*", ". ", text)
    text = re.sub(r"\s+([,;:])", r"\1", text)
    text = re.sub(r"\s(?=\d+\.\s)", "\n", text)
    return text.strip()


def _safe_identifier(identifier: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "", identifier).upper()


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]
