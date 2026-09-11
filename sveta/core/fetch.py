"""Bounded HTTP GET with an SSRF guard, and title/summary extraction (FR-11, FR-12).

The bot fetches whatever URL the user (or a forwarded message) contains, so the
address is attacker-controlled by definition. Every hop is resolved and checked
before a socket is opened: loopback, private, link-local (the cloud metadata
address lives there), multicast and reserved ranges are refused without any
outbound request. Redirects are followed by hand, three at most, each hop checked
again. The body is capped at 1 MB and the whole thing at 10 seconds.

No model call: the summary is what the page says about itself (og:description,
meta description, or the first real paragraph).
"""
import ipaddress
import logging
import socket
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

logger = logging.getLogger(__name__)

TIMEOUT = 10.0
MAX_BYTES = 1_000_000
MAX_REDIRECTS = 3
USER_AGENT = "Mozilla/5.0 (compatible; Svetochka/1.0; +https://github.com/Dmitreyvladimirov)"


class UnsafeURL(Exception):
    """Refused before any request was made."""


@dataclass
class FetchResult:
    url: str
    final_url: str | None = None
    status: int | None = None
    title: str | None = None
    summary: str | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and self.status is not None and 200 <= self.status < 300


def _resolve(host: str) -> list[str]:
    """All addresses the host resolves to. Patched in tests."""
    return sorted({info[4][0] for info in socket.getaddrinfo(host, None)})


def check_url(url: str) -> None:
    """Raise UnsafeURL unless every address behind the URL is public."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise UnsafeURL(f"scheme {parts.scheme!r} is not allowed")
    host = parts.hostname
    if not host:
        raise UnsafeURL("no host")
    if host == "localhost" or host.endswith(".localhost") or host.endswith(".local"):
        raise UnsafeURL(f"{host} is local")
    try:
        addresses = _resolve(host)
    except OSError as e:
        raise UnsafeURL(f"cannot resolve {host}: {e}") from e
    if not addresses:
        raise UnsafeURL(f"{host} resolves to nothing")
    for address in addresses:
        ip = ipaddress.ip_address(address.split("%")[0])
        if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast
                or ip.is_reserved or ip.is_unspecified or not ip.is_global):
            raise UnsafeURL(f"{host} resolves to {address}, which is not public")


def _http_get(url: str, client=None) -> tuple[int, dict, bytes, str | None]:
    """(status, headers, body[:MAX_BYTES], redirect location). Patched in tests.
    TIMEOUT is httpx's per-operation timeout; the monotonic deadline below is the
    whole-request ceiling, so a slow-drip server cannot hold the thread."""
    import time
    import httpx
    deadline = time.monotonic() + TIMEOUT
    own = client is None
    client = client or httpx.Client(follow_redirects=False, timeout=TIMEOUT,
                                    headers={"User-Agent": USER_AGENT, "Accept-Language": "ru,en;q=0.8"})
    try:
        with client.stream("GET", url) as response:
            headers = {k.lower(): v for k, v in response.headers.items()}
            location = headers.get("location") if response.is_redirect else None
            body = b""
            if not location:
                for chunk in response.iter_bytes():
                    body += chunk
                    if len(body) >= MAX_BYTES:
                        body = body[:MAX_BYTES]
                        break
                    if time.monotonic() > deadline:
                        raise TimeoutError(f"fetch exceeded {TIMEOUT}s")
            return response.status_code, headers, body, location
    finally:
        if own:
            client.close()


def get(url: str) -> FetchResult:
    """Never raises: the outcome, good or bad, is a FetchResult the caller records."""
    result = FetchResult(url=url)
    current = url
    try:
        for _ in range(MAX_REDIRECTS + 1):
            check_url(current)
            status, headers, body, location = _http_get(current)
            result.final_url, result.status = current, status
            if location and 300 <= status < 400:
                current = urljoin(current, location)
                continue
            if 200 <= status < 300:
                content_type = headers.get("content-type", "")
                if "html" in content_type or "xml" in content_type or not content_type:
                    result.title, result.summary = extract(body, headers)
                else:
                    result.title = current.rsplit("/", 1)[-1] or current
                    result.summary = content_type.split(";")[0]
            return result
        result.error = "too many redirects"
    except UnsafeURL as e:
        result.error = f"refused: {e}"
    except Exception as e:  # noqa: BLE001 — timeouts, resets, TLS: all "could not fetch"
        result.error = f"{type(e).__name__}: {str(e)[:200]}"
    return result


def get_bytes(url: str) -> tuple[bytes | None, dict, str | None]:
    """(body, headers, error) for non-HTML consumers such as the RSS poller. Same
    guard, same redirect limit, same size cap; no extraction."""
    current = url
    try:
        for _ in range(MAX_REDIRECTS + 1):
            check_url(current)
            status, headers, body, location = _http_get(current)
            if location and 300 <= status < 400:
                current = urljoin(current, location)
                continue
            if 200 <= status < 300:
                return body, headers, None
            return None, headers, f"HTTP {status}"
        return None, {}, "too many redirects"
    except UnsafeURL as e:
        return None, {}, f"refused: {e}"
    except Exception as e:  # noqa: BLE001
        return None, {}, f"{type(e).__name__}: {str(e)[:200]}"


# --- Extraction --------------------------------------------------------------

class _Extractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.og_title = ""
        self.description = ""
        self.paragraphs: list[str] = []
        self._in_title = False
        self._p_depth = 0
        self._p_buf: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "title":
            self._in_title = True
        elif tag == "meta":
            key = (a.get("property") or a.get("name") or "").lower()
            content = (a.get("content") or "").strip()
            if key == "og:title" and content:
                self.og_title = content
            elif key in ("og:description", "description", "twitter:description") and content \
                    and (not self.description or key == "og:description"):
                self.description = content
        elif tag in ("script", "style", "noscript", "nav", "header", "footer"):
            self._skip += 1
        elif tag == "p" and not self._skip:
            self._p_depth += 1
            self._p_buf = []

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        elif tag in ("script", "style", "noscript", "nav", "header", "footer"):
            self._skip = max(0, self._skip - 1)
        elif tag == "p" and self._p_depth:
            self._p_depth -= 1
            text = " ".join("".join(self._p_buf).split())
            if len(text) >= 40:
                self.paragraphs.append(text)

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif self._p_depth and not self._skip:
            self._p_buf.append(data)


def extract(body: bytes, headers: dict | None = None) -> tuple[str | None, str | None]:
    """(title, summary) from an HTML body; both None-able. Never raises."""
    charset = "utf-8"
    content_type = (headers or {}).get("content-type", "")
    if "charset=" in content_type:
        charset = content_type.split("charset=", 1)[1].split(";")[0].strip(' "') or "utf-8"
    try:
        text = body.decode(charset, errors="replace")
    except LookupError:
        text = body.decode("utf-8", errors="replace")
    parser = _Extractor()
    try:
        parser.feed(text)
    except Exception:  # noqa: BLE001 — broken markup is the web's normal state
        pass
    title = " ".join((parser.og_title or parser.title).split())[:200] or None
    summary = parser.description or (parser.paragraphs[0] if parser.paragraphs else "")
    summary = " ".join(summary.split())[:400] or None
    return title, summary
