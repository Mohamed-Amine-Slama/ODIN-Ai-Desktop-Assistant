"""Tests for the Telegram bridge's chat-id gating (core/telegram_channel.py)
— the actual security boundary for a channel that can read files, run shell
commands, and control the PC from a remote message."""
import pytest

from core.telegram_channel import TelegramChannel


@pytest.fixture(autouse=True)
def _token(monkeypatch):
    import config

    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "test-token", raising=False)


def _channel(on_message=None):
    return TelegramChannel(on_message or (lambda text: f"echo: {text}"))


def _update(update_id, text, chat_id):
    return {"update_id": update_id, "message": {"text": text, "chat": {"id": chat_id}}}


# -- linking flow -------------------------------------------------------------

def test_unset_chat_id_replies_with_the_chat_id_and_never_calls_on_message(monkeypatch):
    import config

    monkeypatch.setattr(config, "TELEGRAM_CHAT_ID", "", raising=False)
    calls = []
    sent = []
    channel = _channel(on_message=lambda t: calls.append(t) or "reply")
    monkeypatch.setattr(channel, "send", lambda chat_id, text: sent.append((chat_id, text)))

    channel._handle_update(_update(1, "hi", 555))

    assert calls == [], "an unlinked bot must never forward a message to the assistant"
    assert sent == [(555, "Set TELEGRAM_CHAT_ID=555 in .env and restart to link this chat.")]


# -- chat-id gating (the security boundary) ----------------------------------

def test_messages_from_an_unlinked_chat_are_silently_ignored(monkeypatch):
    import config

    monkeypatch.setattr(config, "TELEGRAM_CHAT_ID", "555", raising=False)
    calls = []
    sent = []
    channel = _channel(on_message=lambda t: calls.append(t) or "reply")
    monkeypatch.setattr(channel, "send", lambda chat_id, text: sent.append((chat_id, text)))

    channel._handle_update(_update(1, "hi", 999))

    assert calls == [], "a stranger's message must never reach the assistant"
    assert sent == [], "not even an acknowledgement — silence reveals nothing"


def test_messages_from_the_linked_chat_are_handled_and_replied_to(monkeypatch):
    import config

    monkeypatch.setattr(config, "TELEGRAM_CHAT_ID", "555", raising=False)
    sent = []
    channel = _channel(on_message=lambda t: f"you said: {t}")
    monkeypatch.setattr(channel, "send", lambda chat_id, text: sent.append((chat_id, text)))

    # Telegram sends chat ids as JSON integers; TELEGRAM_CHAT_ID from .env is
    # always a string — this also checks that comparison isn't broken by that.
    channel._handle_update(_update(1, "hello", 555))

    assert sent == [(555, "you said: hello")]


def test_a_failing_on_message_still_sends_a_reply(monkeypatch):
    import config

    monkeypatch.setattr(config, "TELEGRAM_CHAT_ID", "555", raising=False)

    def boom(text):
        raise RuntimeError("model error")

    sent = []
    channel = _channel(on_message=boom)
    monkeypatch.setattr(channel, "send", lambda chat_id, text: sent.append((chat_id, text)))

    channel._handle_update(_update(1, "hi", 555))

    assert len(sent) == 1
    assert "went wrong" in sent[0][1]


# -- update handling ----------------------------------------------------------

def test_offset_advances_past_every_handled_update(monkeypatch):
    channel = _channel()
    monkeypatch.setattr(channel, "send", lambda *a: None)

    channel._handle_update(_update(42, "", 1))

    assert channel._offset == 43


def test_updates_without_text_are_ignored(monkeypatch):
    channel = _channel()
    calls = []
    monkeypatch.setattr(channel, "send", lambda *a: calls.append(a))

    channel._handle_update({"update_id": 1, "message": {"chat": {"id": 1}}})

    assert calls == []


# -- start() gating -----------------------------------------------------------

def test_start_returns_false_without_a_bot_token(monkeypatch):
    import config

    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "", raising=False)
    assert _channel().start() is False


# -- send() --------------------------------------------------------------------

def test_send_truncates_long_messages(monkeypatch):
    captured = {}

    def fake_post(url, json=None, timeout=None):  # noqa: ARG001
        captured.update(json)

    monkeypatch.setattr("core.telegram_channel.requests.post", fake_post)

    _channel().send(1, "x" * 5000)

    assert captured["text"].endswith("[truncated]")
    assert len(captured["text"]) < 5000
