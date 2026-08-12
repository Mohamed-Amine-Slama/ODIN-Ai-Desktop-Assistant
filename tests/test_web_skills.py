"""Tests for skills/web_skills.py's WebFetchSkill and web_search(): connection
handling, the bounded DNS timeout in _is_public_url, and DuckDuckGo result
parsing. No real network calls are made."""
import socket

import pytest

from skills.web_skills import WebFetchSkill, _is_public_url, web_search


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
