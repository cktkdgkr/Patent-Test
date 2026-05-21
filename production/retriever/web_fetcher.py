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


def fetch_google_patents_claims(
    identifier: str,
    timeout_seconds: int = 20,
    search_aliases: tuple[str, ...] = (),
    country_hint: str | None = None,
) -> tuple[str, str]:
    """
    Fetches claims text from a public Google Patents page.
    This is a fast preview connector, not an official legal-record source.

    Resolution strategy:

    1. Direct fetch of ``identifier`` against ``/patent/<id>/en``.
    2. On 404 and when ``identifier`` lacks a kind-code suffix, retry with
       ``A`` / ``A1`` / ``B1`` / ``B2`` appended. Sequence designed for the
       typical KR/CN/JP/EP publication and granted forms.
    3. On 404 across every kind-code variant, fall back to Google Patents'
       internal search using each of ``search_aliases`` (and ``identifier``)
       as the query. The first patent ID returned whose country prefix
       matches ``country_hint`` (when provided) is fetched directly. This
       recovers cases where the supplied identifier is an application or
       internal number but the corresponding publication does live on
       Google Patents under a different ID.
    4. If steps 1-3 all yield nothing, raise ``WebPatentFetchError`` whose
       message lists every variant tried, so the caller can tell whether
       the patent is missing from Google Patents entirely or simply
       indexed under a less common form.
    """
    clean_identifier = Sanitizer.sanitize(identifier.strip())
    if not clean_identifier:
        raise WebPatentFetchError("empty patent identifier")

    direct_candidates = [clean_identifier]
    if not _GOOGLE_KIND_CODE_TAIL.search(clean_identifier):
        direct_candidates.extend(clean_identifier + suffix for suffix in _GOOGLE_KIND_CODE_RETRIES)

    last_error: Exception | None = None
    last_url: str | None = None
    for candidate in direct_candidates:
        url, html, error = _fetch_google_patents_html(candidate, timeout_seconds)
        last_url = url
        if error is None:
            return url, _extract_and_normalize(html)
        last_error = error
        if not _is_not_found_error(error):
            raise WebPatentFetchError(f"Could not fetch {url}: {error}") from error

    # Direct fetch exhausted. Try Google Patents search with each alias.
    search_queries: list[str] = []
    for raw in (clean_identifier, *search_aliases):
        if raw:
            cleaned = str(raw).strip()
            if cleaned and cleaned not in search_queries:
                search_queries.append(cleaned)

    searched_for: list[str] = []
    for query in search_queries:
        searched_for.append(query)
        try:
            found_id = _search_google_patents(query, country_hint, timeout_seconds)
        except WebPatentFetchError:
            continue
        if not found_id or found_id in direct_candidates:
            continue
        url, html, error = _fetch_google_patents_html(found_id, timeout_seconds)
        if error is None:
            logger.info("Recovered %s via Google Patents search -> %s", clean_identifier, found_id)
            return url, _extract_and_normalize(html)
        last_url = url
        last_error = error

    tried_direct = ", ".join(direct_candidates)
    tried_search = ", ".join(searched_for) if searched_for else "(none)"
    raise WebPatentFetchError(
        f"Google Patents has no entry for {clean_identifier}. "
        f"Tried direct IDs: {tried_direct}. Tried search queries: {tried_search}. "
        f"This is common for recent applications (filed within ~18 months, not yet "
        f"published) and for raw application/serial numbers that lack a public "
        f"publication on Google Patents. Workaround: add a 'claim_text' column to "
        f"the CSV with the claim text pasted directly."
    ) from last_error


_GOOGLE_KIND_CODE_RETRIES = ("A", "A1", "B1", "B2")
_GOOGLE_KIND_CODE_TAIL = re.compile(r"[A-Za-z]\d?$")

# Patterns for extracting patent identifiers out of Google Patents
# responses. Multiple shapes are needed because the HTML, XHR JSON-ish
# payload, and SPA shell each surface IDs differently.
_PATENT_ID_PATTERNS = (
    re.compile(r'/patent/([A-Z]{2}[A-Z0-9]+)/(?:en|ko|zh|ja|de|fr)\b'),
    re.compile(r'"publication_number"\s*:\s*"([A-Z]{2}[A-Z0-9]+)"'),
    re.compile(r'<meta\b[^>]*content\s*=\s*"([A-Z]{2}\d[A-Z0-9]+)"'),
    re.compile(r'rel\s*=\s*"canonical"\s+href\s*=\s*"[^"]*?/patent/([A-Z]{2}[A-Z0-9]+)/'),
    re.compile(r'data-result\s*=\s*"([A-Z]{2}[A-Z0-9]+)"'),
    re.compile(r"\bpn=([A-Z]{2}[A-Z0-9]+)\b"),
    re.compile(r"\bdocid\s*[:=]\s*['\"]?([A-Z]{2}[A-Z0-9]+)"),
)
_GOOGLE_REDIRECT_PATENT_PATH = re.compile(r"/patent/([A-Z]{2}[A-Z0-9]+)/")
_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def _fetch_google_patents_html(
    identifier: str, timeout_seconds: int
) -> tuple[str, str | None, Exception | None]:
    url = f"https://patents.google.com/patent/{urllib.parse.quote(identifier)}/en"
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": _BROWSER_USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9,ko;q=0.8",
        },
    )
    context = _build_ssl_context()
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds, context=context) as response:
            return url, response.read().decode("utf-8", errors="replace"), None
    except urllib.error.URLError as exc:
        return url, None, exc


def _search_google_patents(
    query: str, country_hint: str | None, timeout_seconds: int
) -> str | None:
    """Resolve a free-text query to a Google Patents publication ID.

    Tries Google Patents' internal XHR endpoint first (the same one the UI
    calls; returns server-rendered HTML with the result IDs embedded), then
    falls back to the regular search URL in case the XHR endpoint shape
    changes. Honors ``country_hint`` to prefer same-office hits, which
    matters when a query like ``CN202510536859`` happens to also match an
    unrelated WO/US application.
    """
    for builder in (_xhr_search_url, _ui_search_url):
        url = builder(query)
        ident = _fetch_and_match_patent_id(url, query, country_hint, timeout_seconds)
        if ident:
            return ident
    return None


def _xhr_search_url(query: str) -> str:
    inner = urllib.parse.urlencode({"q": query})
    return "https://patents.google.com/xhr/query?" + urllib.parse.urlencode(
        {"url": inner, "exp": ""}
    )


def _ui_search_url(query: str) -> str:
    return "https://patents.google.com/?" + urllib.parse.urlencode({"q": query})


def _fetch_and_match_patent_id(
    url: str, query: str, country_hint: str | None, timeout_seconds: int
) -> str | None:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": _BROWSER_USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9,ko;q=0.8",
            "Referer": "https://patents.google.com/",
            "X-Requested-With": "XMLHttpRequest",
        },
    )
    context = _build_ssl_context()
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds, context=context) as response:
            final_url = response.geturl()
            body = response.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as exc:
        logger.debug("Google Patents search %s failed: %s", url, exc)
        return None

    redirect_match = _GOOGLE_REDIRECT_PATENT_PATH.search(final_url)
    if redirect_match:
        return redirect_match.group(1)

    candidates: list[str] = []
    for pattern in _PATENT_ID_PATTERNS:
        for match in pattern.findall(body):
            if match and match not in candidates:
                candidates.append(match)
    if not candidates:
        logger.info(
            "Google Patents search for %r against %s returned no parseable "
            "patent IDs (final url=%s, body length=%d). The page may be a "
            "JS-rendered shell.",
            query, url, final_url, len(body),
        )
        return None

    query_upper = re.sub(r"[^A-Za-z0-9]", "", query).upper()
    if country_hint:
        cc = country_hint.strip().upper()
        if cc:
            for candidate in candidates:
                if candidate.startswith(cc) and candidate.upper() != query_upper:
                    return candidate
    for candidate in candidates:
        if candidate.upper() != query_upper:
            return candidate
    return candidates[0]


def _is_not_found_error(error: Exception) -> bool:
    if isinstance(error, urllib.error.HTTPError):
        return error.code == 404
    return False


def _extract_and_normalize(html: str) -> str:
    return extract_claims_from_google_patents_html(html)


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
