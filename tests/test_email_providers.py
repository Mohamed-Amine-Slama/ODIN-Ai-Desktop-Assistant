"""Tests for core/email_providers.py's gating helpers and graceful
degradation without the optional google-auth-oauthlib/msal packages
installed (the normal state in this test environment)."""
import pytest

from core import email_providers
from core.email_providers import GoogleProvider, MicrosoftProvider, ProviderError, get_provider


@pytest.fixture
def oauth_dir(tmp_path, monkeypatch):
    import config

    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path), raising=False)
    monkeypatch.setattr(email_providers, "_providers", {})
    monkeypatch.delenv("MS_OAUTH_CLIENT_ID", raising=False)
    return tmp_path


def test_nothing_configured_by_default(oauth_dir):  # noqa: ARG001
    assert email_providers.google_configured() is False
    assert email_providers.google_ready() is False
    assert email_providers.microsoft_configured() is False
    assert email_providers.microsoft_ready() is False
    assert email_providers.default_account() is None


def test_google_configured_with_credentials_file_but_not_yet_ready(oauth_dir):
    (oauth_dir / "oauth").mkdir()
    (oauth_dir / "oauth" / "google_credentials.json").write_text("{}", encoding="utf-8")

    assert email_providers.google_configured() is True
    assert email_providers.google_ready() is False


def test_google_ready_once_a_token_exists(oauth_dir):
    (oauth_dir / "oauth").mkdir()
    (oauth_dir / "oauth" / "google_token.json").write_text("{}", encoding="utf-8")

    assert email_providers.google_configured() is True
    assert email_providers.google_ready() is True


def test_microsoft_configured_via_client_id_env_var(oauth_dir, monkeypatch):  # noqa: ARG001
    monkeypatch.setenv("MS_OAUTH_CLIENT_ID", "abc123")
    assert email_providers.microsoft_configured() is True
    assert email_providers.microsoft_ready() is False


def test_microsoft_ready_once_a_token_cache_exists(oauth_dir):
    (oauth_dir / "oauth").mkdir()
    (oauth_dir / "oauth" / "ms_token_cache.json").write_text("{}", encoding="utf-8")

    assert email_providers.microsoft_configured() is True
    assert email_providers.microsoft_ready() is True


def test_default_account_is_none_when_neither_is_ready(oauth_dir):  # noqa: ARG001
    assert email_providers.default_account() is None


def test_default_account_picks_the_only_ready_one(oauth_dir):
    (oauth_dir / "oauth").mkdir()
    (oauth_dir / "oauth" / "google_token.json").write_text("{}", encoding="utf-8")
    assert email_providers.default_account() == "google"


def test_default_account_is_none_when_both_are_ready(oauth_dir):
    """Ambiguous either way — the skill layer asks rather than guessing
    (see test_email_skills.test_ambiguous_accounts_asks_rather_than_guessing)."""
    (oauth_dir / "oauth").mkdir()
    (oauth_dir / "oauth" / "google_token.json").write_text("{}", encoding="utf-8")
    (oauth_dir / "oauth" / "ms_token_cache.json").write_text("{}", encoding="utf-8")
    assert email_providers.default_account() is None


def test_get_provider_returns_the_right_type_and_caches(oauth_dir):  # noqa: ARG001
    google = get_provider("google")
    assert isinstance(google, GoogleProvider)
    assert get_provider("google") is google

    ms = get_provider("microsoft")
    assert isinstance(ms, MicrosoftProvider)
    assert get_provider("microsoft") is ms
    assert ms is not google


def test_google_provider_reports_missing_package_not_a_crash(oauth_dir):  # noqa: ARG001
    provider = GoogleProvider()
    with pytest.raises(ProviderError):
        provider.connect()
    with pytest.raises(ProviderError):
        provider.list_recent_mail(5)


def test_microsoft_provider_reports_missing_package_not_a_crash(oauth_dir):  # noqa: ARG001
    provider = MicrosoftProvider()
    with pytest.raises(ProviderError):
        provider.list_recent_mail(5)


def test_google_connect_without_a_credentials_file_names_where_to_put_it(oauth_dir, monkeypatch):
    """Distinguishes 'package missing' from 'package present but not set
    up' — this only exercises the latter by faking the package present."""
    import sys
    import types

    fake_module = types.ModuleType("google_auth_oauthlib.flow")
    fake_module.InstalledAppFlow = object()
    monkeypatch.setitem(sys.modules, "google_auth_oauthlib", types.ModuleType("google_auth_oauthlib"))
    monkeypatch.setitem(sys.modules, "google_auth_oauthlib.flow", fake_module)

    provider = GoogleProvider()
    with pytest.raises(ProviderError, match="console.cloud.google.com"):
        provider.connect()
