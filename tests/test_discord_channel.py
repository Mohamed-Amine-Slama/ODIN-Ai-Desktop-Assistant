"""Tests for the Discord bridge's channel-id gating (core/discord_channel.py)
— the actual security boundary for a channel that can read files, run shell
commands, and control the PC from a remote message. _route() is the pure,
synchronous decision logic the async on_message handler delegates to (see
that module's docstring), so it's tested directly here without a live
Gateway connection — discord.py doesn't even need to be installed."""
import pytest

from core.discord_channel import DiscordChannel


@pytest.fixture(autouse=True)
def _token(monkeypatch):
    import config

    monkeypatch.setattr(config, "DISCORD_BOT_TOKEN", "test-token", raising=False)


def _channel(on_message=None):
    return DiscordChannel(on_message or (lambda text: f"echo: {text}"))


# -- linking flow -------------------------------------------------------------

def test_unset_channel_id_replies_with_the_channel_id_and_never_calls_on_message(monkeypatch):
    import config

    monkeypatch.setattr(config, "DISCORD_CHANNEL_ID", "", raising=False)
    calls = []
    channel = _channel(on_message=lambda t: calls.append(t) or "reply")

    reply = channel._route("555", "hi")

    assert calls == [], "an unlinked bot must never forward a message to the assistant"
    assert reply == "Set DISCORD_CHANNEL_ID=555 in .env and restart to link this channel."


# -- channel-id gating (the security boundary) ---------------------------------

def test_messages_from_an_unlinked_channel_are_silently_ignored(monkeypatch):
    import config

    monkeypatch.setattr(config, "DISCORD_CHANNEL_ID", "555", raising=False)
    calls = []
    channel = _channel(on_message=lambda t: calls.append(t) or "reply")

    reply = channel._route("999", "hi")

    assert calls == [], "a stranger's message must never reach the assistant"
    assert reply is None, "not even an acknowledgement — silence reveals nothing"


def test_messages_from_the_linked_channel_are_handled_and_replied_to(monkeypatch):
    import config

    monkeypatch.setattr(config, "DISCORD_CHANNEL_ID", "555", raising=False)
    channel = _channel(on_message=lambda t: f"you said: {t}")

    # Discord channel ids arrive as ints from message.channel.id;
    # DISCORD_CHANNEL_ID from .env is always a string — check that
    # comparison isn't broken by that, same as the Telegram bridge's test.
    reply = channel._route("555", "hello")

    assert reply == "you said: hello"


def test_a_failing_on_message_still_returns_a_reply(monkeypatch):
    import config

    monkeypatch.setattr(config, "DISCORD_CHANNEL_ID", "555", raising=False)

    def boom(text):
        raise RuntimeError("model error")

    channel = _channel(on_message=boom)

    reply = channel._route("555", "hi")

    assert "went wrong" in reply


def test_a_falsy_reply_becomes_done(monkeypatch):
    import config

    monkeypatch.setattr(config, "DISCORD_CHANNEL_ID", "555", raising=False)
    channel = _channel(on_message=lambda t: "")  # noqa: ARG005

    assert channel._route("555", "hi") == "Done."


# -- message content ------------------------------------------------------------

def test_empty_content_is_ignored(monkeypatch):
    import config

    monkeypatch.setattr(config, "DISCORD_CHANNEL_ID", "555", raising=False)
    calls = []
    channel = _channel(on_message=lambda t: calls.append(t))

    assert channel._route("555", "") is None
    assert calls == []


# -- start() gating -------------------------------------------------------------

def test_start_returns_false_without_a_bot_token(monkeypatch):
    import config

    monkeypatch.setattr(config, "DISCORD_BOT_TOKEN", "", raising=False)
    assert _channel().start() is False


def test_start_returns_false_without_the_optional_discord_package():
    """start() must degrade gracefully instead of raising when 'discord.py'
    isn't installed, same as any other optional dependency in this project.
    Skipped (not failed) if the package happens to be installed here, since
    requirements.txt lists it as optional and this test is only meaningful
    when it's actually absent."""
    try:
        import discord  # noqa: F401
    except ImportError:
        pass
    else:
        pytest.skip("discord.py is installed; nothing to degrade")
    assert _channel().start() is False
