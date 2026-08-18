"""Tests for the token-bucket throttle guarding web_fetch/http_request/
get_news (core/rate_limit.py)."""
import pytest

from core import rate_limit


@pytest.fixture(autouse=True)
def _clean_buckets():
    rate_limit.reset()
    yield
    rate_limit.reset()


def test_allows_calls_within_the_burst(monkeypatch):
    import config

    monkeypatch.setattr(config, "RATE_LIMIT_PER_MINUTE", 60, raising=False)
    monkeypatch.setattr(config, "RATE_LIMIT_BURST", 3, raising=False)

    assert rate_limit.allow("k") is True
    assert rate_limit.allow("k") is True
    assert rate_limit.allow("k") is True


def test_denies_once_the_burst_is_exhausted(monkeypatch):
    import config

    monkeypatch.setattr(config, "RATE_LIMIT_PER_MINUTE", 60, raising=False)
    monkeypatch.setattr(config, "RATE_LIMIT_BURST", 2, raising=False)

    assert rate_limit.allow("k") is True
    assert rate_limit.allow("k") is True
    assert rate_limit.allow("k") is False


def test_refills_over_time(monkeypatch):
    import config

    monkeypatch.setattr(config, "RATE_LIMIT_PER_MINUTE", 60, raising=False)  # 1 token/sec
    monkeypatch.setattr(config, "RATE_LIMIT_BURST", 1, raising=False)

    clock = [1000.0]
    monkeypatch.setattr(rate_limit.time, "monotonic", lambda: clock[0])

    assert rate_limit.allow("k") is True
    assert rate_limit.allow("k") is False

    clock[0] += 1.0  # one full second later, one token/sec refill
    assert rate_limit.allow("k") is True


def test_disabled_when_per_minute_is_zero(monkeypatch):
    import config

    monkeypatch.setattr(config, "RATE_LIMIT_PER_MINUTE", 0, raising=False)
    monkeypatch.setattr(config, "RATE_LIMIT_BURST", 1, raising=False)

    for _ in range(50):
        assert rate_limit.allow("k") is True


def test_keys_are_independent(monkeypatch):
    import config

    monkeypatch.setattr(config, "RATE_LIMIT_PER_MINUTE", 60, raising=False)
    monkeypatch.setattr(config, "RATE_LIMIT_BURST", 1, raising=False)

    assert rate_limit.allow("a") is True
    assert rate_limit.allow("a") is False
    assert rate_limit.allow("b") is True, "a different key must have its own bucket"
