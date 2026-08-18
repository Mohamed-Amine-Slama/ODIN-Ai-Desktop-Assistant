"""Tests for skills/web_skills.py's WebFetchSkill, HttpRequestSkill, and
web_search(): connection handling, the bounded DNS timeout in
_is_public_url, and DuckDuckGo result parsing. No real network calls are
made."""
import socket

import pytest

from core.risk import Risk
from skills.web_skills import HttpRequestSkill, NewsSkill, WebFetchSkill, _is_public_url, web_search


class _FakeResponse:
    def __init__(self, status_code=200, headers=None, chunks=None, encoding="utf-8"):
        self.status_code = status_code
        self.headers = headers or {}
        self.encoding = encoding
        self._chunks = chunks or []
        self.closed = False

    def iter_content(self, chunk_size=16_384):  # noqa: ARG002
        yield from self._chunks

    def close(self):
        self.closed = True


@pytest.fixture
def public_url(monkeypatch):
    """These tests are about connection handling, not URL validation — stub
    out the public/private-address check so it never gets in the way."""
    monkeypatch.setattr("skills.web_skills._is_public_url", lambda url: True)  # noqa: ARG005


def _patch_get(monkeypatch, response):
    def fake_get(*args, **kwargs):  # noqa: ARG001
        return response

    monkeypatch.setattr("skills.web_skills.requests.get", fake_get)


def test_web_fetch_closes_connection_on_redirect(monkeypatch, public_url):
    resp = _FakeResponse(status_code=302)
    _patch_get(monkeypatch, resp)
    out = WebFetchSkill().run(url="http://example.com")
    assert "redirects" in out.lower()
    assert resp.closed


def test_web_fetch_closes_connection_on_non_200(monkeypatch, public_url):
    resp = _FakeResponse(status_code=500)
    _patch_get(monkeypatch, resp)
    out = WebFetchSkill().run(url="http://example.com")
    assert "500" in out
    assert resp.closed


def test_web_fetch_closes_connection_on_non_text_content_type(monkeypatch, public_url):
    resp = _FakeResponse(status_code=200, headers={"content-type": "application/octet-stream"})
    _patch_get(monkeypatch, resp)
    out = WebFetchSkill().run(url="http://example.com")
    assert "not readable text" in out
    assert resp.closed


def test_web_fetch_closes_connection_on_success(monkeypatch, public_url):
    resp = _FakeResponse(
        status_code=200,
        headers={"content-type": "text/plain"},
        chunks=[b"hello world"],
    )
    _patch_get(monkeypatch, resp)
    out = WebFetchSkill().run(url="http://example.com")
    assert "hello world" in out
    assert resp.closed


def test_web_fetch_decodes_unlabeled_utf8_correctly(monkeypatch, public_url):
    """requests defaults response.encoding to ISO-8859-1 for any text/* type
    that doesn't declare its own charset — which is what a real, modern,
    just-forgot-to-say-so UTF-8 page looks like. Blindly trusting that
    default turns non-ASCII content into mojibake."""
    body = "Café 😀".encode("utf-8")
    resp = _FakeResponse(
        status_code=200,
        headers={"content-type": "text/plain"},  # no charset param
        chunks=[body],
        encoding="ISO-8859-1",  # requests' own default for unlabeled text/*
    )
    _patch_get(monkeypatch, resp)
    out = WebFetchSkill().run(url="http://example.com")
    assert "Café 😀" in out


def test_web_fetch_respects_rate_limit(monkeypatch, public_url):
    monkeypatch.setattr("skills.web_skills.rate_limit.allow", lambda key: False)  # noqa: ARG005
    calls = []
    monkeypatch.setattr("skills.web_skills.requests.get", lambda *a, **k: calls.append(1))  # noqa: ARG005
    out = WebFetchSkill().run(url="http://example.com")
    assert "too many" in out.lower()
    assert calls == []


def test_web_fetch_marks_its_result_untrusted_for_the_security_scan(monkeypatch, public_url):
    resp = _FakeResponse(
        status_code=200, headers={"content-type": "text/plain"}, chunks=[b"hello"]
    )
    _patch_get(monkeypatch, resp)
    captured = {}

    def fake_guard(text, *, source, untrusted=False):  # noqa: ARG001
        captured["untrusted"] = untrusted
        return text

    monkeypatch.setattr("skills.web_skills.guard", fake_guard)
    WebFetchSkill().run(url="http://example.com")
    assert captured["untrusted"] is True


def test_web_fetch_respects_an_explicitly_declared_charset(monkeypatch, public_url):
    """When the server actually declares a charset, that declaration must
    still be honored rather than always forcing UTF-8."""
    body = "Café".encode("iso-8859-1")
    resp = _FakeResponse(
        status_code=200,
        headers={"content-type": "text/plain; charset=iso-8859-1"},
        chunks=[body],
        encoding="ISO-8859-1",
    )
    _patch_get(monkeypatch, resp)
    out = WebFetchSkill().run(url="http://example.com")
    assert "Café" in out


# -- web_search (DuckDuckGo via ddgs) -----------------------------------------

class _FakeDDGS:
    """Stands in for ddgs.DDGS as a context manager."""

    def __init__(self, results=None, error=None):
        self._results = results if results is not None else []
        self._error = error

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def text(self, query, max_results=6):  # noqa: ARG002
        if self._error:
            raise self._error
        return iter(self._results)


def test_web_search_parses_results(monkeypatch):
    fake_results = [
        {"title": "A", "href": "https://a.example", "body": "desc a"},
        {"title": "B", "href": "https://b.example", "body": "desc b"},
    ]
    monkeypatch.setattr("ddgs.DDGS", lambda: _FakeDDGS(fake_results))

    results = web_search("query")

    assert results == [
        {"title": "A", "url": "https://a.example", "snippet": "desc a"},
        {"title": "B", "url": "https://b.example", "snippet": "desc b"},
    ]


def test_web_search_no_results_is_empty_list(monkeypatch):
    monkeypatch.setattr("ddgs.DDGS", lambda: _FakeDDGS([]))
    assert web_search("query") == []


def test_web_search_wraps_errors(monkeypatch):
    monkeypatch.setattr("ddgs.DDGS", lambda: _FakeDDGS(error=RuntimeError("network down")))
    with pytest.raises(RuntimeError, match="couldn't search the web"):
        web_search("query")


def test_web_search_wraps_a_malformed_result_shape_too(monkeypatch):
    """Result-shape processing must be covered by the same try/except as the
    search call itself — deep_learn's callers only catch RuntimeError to
    skip one subtopic, so any other exception type here would abort the
    whole run instead."""
    monkeypatch.setattr("ddgs.DDGS", lambda: _FakeDDGS(["not-a-dict"]))
    with pytest.raises(RuntimeError, match="couldn't search the web"):
        web_search("query")


def test_web_search_passes_the_query_and_count(monkeypatch):
    calls = []

    class RecordingDDGS(_FakeDDGS):
        def text(self, query, max_results=6):
            calls.append((query, max_results))
            return iter([])

    monkeypatch.setattr("ddgs.DDGS", lambda: RecordingDDGS())
    web_search("my query", count=3)

    assert calls == [("my query", 3)]


def test_is_public_url_bounds_dns_timeout(monkeypatch):
    """getaddrinfo has no timeout parameter of its own, so a slow/unresponsive
    resolver relies on a scoped socket.setdefaulttimeout() to stay bounded —
    verify it's actually set during resolution and restored afterward."""
    real_setdefaulttimeout = socket.setdefaulttimeout
    seen = []  # every value setdefaulttimeout was called with, in order

    def fake_setdefaulttimeout(value):
        seen.append(value)
        real_setdefaulttimeout(value)

    def fake_getaddrinfo(host, port):  # noqa: ARG001
        # At the moment resolution runs, the bounded timeout must already be
        # in effect — not the caller's original (here: unset) default.
        assert socket.getdefaulttimeout() == seen[-1]
        assert seen[-1] is not None
        raise socket.timeout("simulated slow resolver")

    monkeypatch.setattr(socket, "setdefaulttimeout", fake_setdefaulttimeout)
    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)

    assert _is_public_url("http://example.com") is False
    assert seen[0] is not None, "a bounded timeout must be set before resolving"
    assert socket.getdefaulttimeout() is None, "must restore the previous global timeout"


# -- http_request -------------------------------------------------------------

class _FakeHttpResponse:
    def __init__(self, status_code=200, text="", headers=None):
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}


def _patch_request(monkeypatch, response):
    calls = []

    def fake_request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        return response

    monkeypatch.setattr("skills.web_skills.requests.request", fake_request)
    return calls


def test_http_request_get_and_head_are_safe_tier():
    assert HttpRequestSkill().risk_for() == Risk.SAFE  # default method is GET
    assert HttpRequestSkill().risk_for(method="get") == Risk.SAFE
    assert HttpRequestSkill().risk_for(method="HEAD") == Risk.SAFE


def test_http_request_mutating_methods_are_moderate_tier():
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        assert HttpRequestSkill().risk_for(method=method) == Risk.MODERATE


def test_http_request_rejects_private_addresses(monkeypatch):
    monkeypatch.setattr("skills.web_skills._is_public_url", lambda url: False)  # noqa: ARG005
    out = HttpRequestSkill().run(url="http://localhost/admin")
    assert "won't call" in out


def test_http_request_rejects_an_invalid_url(public_url):
    out = HttpRequestSkill().run(url="javascript:alert(1)")
    assert "valid public" in out


def test_http_request_rejects_an_unsupported_method(public_url):
    out = HttpRequestSkill().run(url="http://example.com", method="TRACE")
    assert "Unsupported method" in out


def test_http_request_sends_method_body_and_headers(monkeypatch, public_url):
    calls = _patch_request(monkeypatch, _FakeHttpResponse(status_code=200, text="ok"))

    out = HttpRequestSkill().run(
        url="http://example.com/hook",
        method="post",
        body="payload",
        headers={"X-Test": "1"},
    )

    assert "HTTP 200" in out and "ok" in out
    method, url, kwargs = calls[0]
    assert method == "POST"
    assert url == "http://example.com/hook"
    assert kwargs["data"] == "payload"
    assert kwargs["headers"] == {"X-Test": "1"}
    assert kwargs["allow_redirects"] is False


def test_http_request_does_not_follow_redirects(monkeypatch, public_url):
    _patch_request(monkeypatch, _FakeHttpResponse(status_code=302))
    out = HttpRequestSkill().run(url="http://example.com")
    assert "redirect" in out.lower()


def test_http_request_reports_timeout(monkeypatch, public_url):
    import requests

    def boom(method, url, **kwargs):  # noqa: ARG001
        raise requests.Timeout()

    monkeypatch.setattr("skills.web_skills.requests.request", boom)
    assert "did not respond in time" in HttpRequestSkill().run(url="http://example.com")


def test_http_request_reports_connection_failures(monkeypatch, public_url):
    import requests

    def boom(method, url, **kwargs):  # noqa: ARG001
        raise requests.ConnectionError("no route to host")

    monkeypatch.setattr("skills.web_skills.requests.request", boom)
    assert "failed" in HttpRequestSkill().run(url="http://example.com").lower()


def test_http_request_truncates_a_long_response(monkeypatch, public_url):
    _patch_request(monkeypatch, _FakeHttpResponse(status_code=200, text="x" * 30_000))
    out = HttpRequestSkill().run(url="http://example.com")
    assert "truncated" in out
    assert len(out) < 25_000


def test_http_request_reports_an_empty_body(monkeypatch, public_url):
    _patch_request(monkeypatch, _FakeHttpResponse(status_code=204, text=""))
    assert "empty response" in HttpRequestSkill().run(url="http://example.com")


def test_http_request_respects_rate_limit(monkeypatch, public_url):
    monkeypatch.setattr("skills.web_skills.rate_limit.allow", lambda key: False)  # noqa: ARG005
    calls = []
    monkeypatch.setattr("skills.web_skills.requests.request", lambda *a, **k: calls.append(1))  # noqa: ARG005
    out = HttpRequestSkill().run(url="http://example.com")
    assert "too many" in out.lower()
    assert calls == []


def test_http_request_marks_its_result_untrusted_for_the_security_scan(monkeypatch, public_url):
    _patch_request(monkeypatch, _FakeHttpResponse(status_code=200, text="ok"))
    captured = {}

    def fake_guard(text, *, source, untrusted=False):  # noqa: ARG001
        captured["untrusted"] = untrusted
        return text

    monkeypatch.setattr("skills.web_skills.guard", fake_guard)
    HttpRequestSkill().run(url="http://example.com")
    assert captured["untrusted"] is True


# -- get_news -------------------------------------------------------------------

_RSS_XML = """<?xml version="1.0"?>
<rss version="2.0"><channel>
<title>Example Feed</title>
<item><title>First Story</title><link>https://example.com/1</link></item>
<item><title>Second Story</title><link>https://example.com/2</link></item>
</channel></rss>"""

_ATOM_XML = """<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
<title>Atom Feed</title>
<entry><title>Atom Story</title><link href="https://example.com/atom1"/></entry>
</feed>"""


class _FakeNewsResponse:
    def __init__(self, content: bytes):
        self.content = content

    def raise_for_status(self):
        pass


def test_get_news_resolves_a_known_feed_name_and_parses_rss(monkeypatch):
    monkeypatch.setattr(
        "skills.web_skills.requests.get", lambda *a, **k: _FakeNewsResponse(_RSS_XML.encode())  # noqa: ARG005
    )
    out = NewsSkill().run(feed="hacker news")
    assert "First Story" in out and "https://example.com/1" in out
    assert "Second Story" in out


def test_get_news_parses_atom_feeds_too(monkeypatch):
    monkeypatch.setattr(
        "skills.web_skills.requests.get", lambda *a, **k: _FakeNewsResponse(_ATOM_XML.encode())  # noqa: ARG005
    )
    out = NewsSkill().run(feed="https://example.com/atom.xml")
    assert "Atom Story" in out and "https://example.com/atom1" in out


def test_get_news_uses_configured_default_feeds_when_none_is_named(monkeypatch):
    import config

    monkeypatch.setattr(config, "RSS_FEEDS", ["https://example.com/feed.xml"], raising=False)
    monkeypatch.setattr(
        "skills.web_skills.requests.get", lambda *a, **k: _FakeNewsResponse(_RSS_XML.encode())  # noqa: ARG005
    )
    out = NewsSkill().run()
    assert "First Story" in out


def test_get_news_with_no_feed_named_and_no_default_configured(monkeypatch):
    import config

    monkeypatch.setattr(config, "RSS_FEEDS", [], raising=False)
    out = NewsSkill().run()
    assert "no feed" in out.lower()


def test_get_news_rejects_an_unresolvable_feed(monkeypatch):
    monkeypatch.setattr("skills.web_skills._is_public_url", lambda url: False)  # noqa: ARG005
    out = NewsSkill().run(feed="some-made-up-host")
    assert "no feed" in out.lower()


def test_get_news_respects_rate_limit(monkeypatch):
    monkeypatch.setattr("skills.web_skills.rate_limit.allow", lambda key: False)  # noqa: ARG005
    out = NewsSkill().run(feed="hacker news")
    assert "too many" in out.lower()


def test_get_news_reports_fetch_errors_without_failing_the_whole_call(monkeypatch):
    import requests

    def boom(*a, **k):  # noqa: ARG001
        raise requests.ConnectionError("down")

    monkeypatch.setattr("skills.web_skills.requests.get", boom)
    out = NewsSkill().run(feed="hacker news")
    assert "couldn't get" in out.lower()


def test_get_news_respects_max_items(monkeypatch):
    monkeypatch.setattr(
        "skills.web_skills.requests.get", lambda *a, **k: _FakeNewsResponse(_RSS_XML.encode())  # noqa: ARG005
    )
    out = NewsSkill().run(feed="hacker news", max_items=1)
    assert len(out.strip().splitlines()) == 1
