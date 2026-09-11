"""The SSRF guard refuses before any request; extraction survives bad markup."""
import pytest

from sveta.core import fetch


def resolver(mapping):
    def _resolve(host):
        if host not in mapping:
            raise OSError("no such host")
        return mapping[host]
    return _resolve


def test_metadata_and_private_hosts_are_refused_without_a_request(monkeypatch):
    calls = []
    monkeypatch.setattr(fetch, "_http_get", lambda url: calls.append(url) or (200, {}, b"", None))
    monkeypatch.setattr(fetch, "_resolve", resolver({
        "metadata.internal": ["169.254.169.254"],
        "db.corp": ["10.0.0.5"],
        "home": ["192.168.1.1"],
        "v6local": ["::1"],
        "mixed.example": ["93.184.216.34", "127.0.0.1"],   # one private address poisons the lot
    }))
    for url in ("http://169.254.169.254/latest/meta-data/", "http://metadata.internal/",
                "http://db.corp/", "http://home/", "http://localhost:8000/", "http://[::1]/",
                "http://v6local/", "http://mixed.example/", "ftp://example.com/x",
                "file:///etc/passwd", "http://127.0.0.1/"):
        r = fetch.get(url)
        assert r.error and r.error.startswith("refused"), url
        assert r.status is None
    assert calls == []


def test_redirect_to_private_is_refused_after_the_first_hop(monkeypatch):
    monkeypatch.setattr(fetch, "_resolve", resolver({"public.example": ["93.184.216.34"],
                                                    "internal.example": ["10.1.1.1"]}))
    hops = []

    def _get(url):
        hops.append(url)
        if url.startswith("http://public.example"):
            return 302, {"location": "http://internal.example/secret"}, b"", "http://internal.example/secret"
        raise AssertionError("must not be reached")
    monkeypatch.setattr(fetch, "_http_get", _get)
    r = fetch.get("http://public.example/go")
    assert hops == ["http://public.example/go"]
    assert r.error.startswith("refused")


def test_public_page_is_fetched_and_summarised(monkeypatch):
    monkeypatch.setattr(fetch, "_resolve", resolver({"linear.app": ["76.76.21.21"]}))
    html = b"""<html><head><title> How we plan  </title>
    <meta property="og:description" content="Planning at Linear, explained."></head>
    <body><nav><p>menu menu menu menu menu menu menu menu menu menu</p></nav>
    <p>Short.</p><p>This is the first real paragraph of the article, long enough to count.</p>
    </body></html>"""
    monkeypatch.setattr(fetch, "_http_get", lambda url: (200, {"content-type": "text/html; charset=utf-8"}, html, None))
    r = fetch.get("https://linear.app/blog/how-we-plan")
    assert r.ok and r.title == "How we plan" and r.summary == "Planning at Linear, explained."


def test_first_paragraph_is_the_fallback_summary_and_nav_is_skipped():
    html = b"<html><body><nav><p>menu menu menu menu menu menu menu menu menu</p></nav>" \
           b"<p>tiny</p><p>A paragraph with enough words in it to serve as a summary of the page.</p></body></html>"
    title, summary = fetch.extract(html)
    assert title is None
    assert summary.startswith("A paragraph with enough words")


def test_404_is_recorded_not_summarised(monkeypatch):
    monkeypatch.setattr(fetch, "_resolve", resolver({"example.com": ["93.184.216.34"]}))
    monkeypatch.setattr(fetch, "_http_get", lambda url: (404, {"content-type": "text/html"}, b"<title>Not found</title>", None))
    r = fetch.get("https://example.com/gone")
    assert r.status == 404 and not r.ok and r.title is None and r.error is None


def test_network_errors_become_a_result_not_an_exception(monkeypatch):
    monkeypatch.setattr(fetch, "_resolve", resolver({"example.com": ["93.184.216.34"]}))

    def boom(url):
        raise TimeoutError("read timed out")
    monkeypatch.setattr(fetch, "_http_get", boom)
    r = fetch.get("https://example.com/slow")
    assert r.error.startswith("TimeoutError") and r.status is None


def test_too_many_redirects(monkeypatch):
    monkeypatch.setattr(fetch, "_resolve", resolver({"example.com": ["93.184.216.34"]}))
    monkeypatch.setattr(fetch, "_http_get", lambda url: (301, {"location": url + "/x"}, b"", url + "/x"))
    r = fetch.get("https://example.com/a")
    assert r.error == "too many redirects"


def test_broken_markup_and_odd_charset_do_not_raise():
    title, summary = fetch.extract(b"\xff\xfe<title>\xd0\x9f\xd1\x80\xd0\xb8\xd0\xb2\xd0\xb5\xd1\x82<p><b>", {"content-type": "text/html; charset=nonsense"})
    assert title == "Привет"


def test_non_html_content_is_named_by_its_type(monkeypatch):
    monkeypatch.setattr(fetch, "_resolve", resolver({"example.com": ["93.184.216.34"]}))
    monkeypatch.setattr(fetch, "_http_get", lambda url: (200, {"content-type": "application/pdf"}, b"%PDF", None))
    r = fetch.get("https://example.com/paper.pdf")
    assert r.ok and r.title == "paper.pdf" and r.summary == "application/pdf"


@pytest.mark.parametrize("address", ["8.8.8.8", "2606:4700::1111", "93.184.216.34"])
def test_public_addresses_pass(monkeypatch, address):
    monkeypatch.setattr(fetch, "_resolve", lambda host: [address])
    fetch.check_url("https://example.com/")
