"""Test fakes for the Anthropic streaming client.

These let the whole brain loop run in-process (and on Linux/WSL) without a
network call or an API key.
"""
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class Block(SimpleNamespace):
    """Stands in for an SDK content block (TextBlock / ToolUseBlock)."""


def text_block(text: str) -> Block:
    return Block(type="text", text=text)


def tool_use_block(name: str, tool_input: dict, block_id: str = "toolu_1") -> Block:
    return Block(type="tool_use", name=name, input=tool_input, id=block_id)


def response(content, stop_reason="end_turn", stop_details=None):
    return SimpleNamespace(
        content=content,
        stop_reason=stop_reason,
        stop_details=stop_details,
        usage=SimpleNamespace(
            input_tokens=10,
            output_tokens=5,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=0,
        ),
    )


class _FakeStream:
    def __init__(self, message, raise_on_final=None):
        self._message = message
        self._raise_on_final = raise_on_final

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    @property
    def text_stream(self):
        for block in self._message.content:
            if getattr(block, "type", None) == "text":
                yield block.text

    def get_final_message(self):
        if self._raise_on_final is not None:
            raise self._raise_on_final
        return self._message


class FakeMessages:
    """Replays a scripted list of responses. An entry that is an Exception is
    raised instead of returned, which is how we simulate a mid-turn failure."""

    def __init__(self, script):
        self.script = list(script)
        self.calls: list[dict] = []

    def stream(self, **kwargs):
        self.calls.append(kwargs)
        if not self.script:
            raise AssertionError("FakeMessages ran out of scripted responses")
        item = self.script.pop(0)
        if isinstance(item, Exception):
            return _FakeStream(response([text_block("")]), raise_on_final=item)
        return _FakeStream(item)


class FakeClient:
    def __init__(self, script):
        self.messages = FakeMessages(script)


@pytest.fixture
def make_brain(monkeypatch):
    """Build a Brain wired to a scripted fake client."""
    import config

    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "test-key", raising=False)
    monkeypatch.setattr(config, "DEBUG", False, raising=False)

    from core.brain import Brain

    def _make(script, confirm=None, on_text=None):
        client = FakeClient(script)
        brain = Brain(client=client, confirm=confirm, on_text=on_text)
        brain.client = client
        return brain

    return _make
