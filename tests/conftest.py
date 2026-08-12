"""Test fakes for the Anthropic streaming client.

These let the whole brain loop run in-process (and on Linux/WSL) without a
network call or an API key.
"""
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(autouse=True)
def _reset_screen_state():
    """screen_state's last-screenshot mapping is module-global; without this a
    mapping recorded by one test could leak into an unrelated test's click/
    scroll coordinates."""
    from skills import screen_state

    screen_state.clear()
    yield
    screen_state.clear()


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


# -- OpenAI-compatible fakes (DashScope / Qwen path) -----------------------


def openai_chunk(content: str | None = None, tool_call=None, finish_reason=None):
    """Build one streamed chat.completions chunk.

    tool_call is (name, json_arguments) and is emitted as a single delta.
    """
    tool_calls = None
    if tool_call is not None:
        name, arguments = tool_call
        tool_calls = [
            SimpleNamespace(
                index=0,
                id="call_1",
                function=SimpleNamespace(name=name, arguments=arguments),
            )
        ]
        finish_reason = finish_reason or "tool_calls"

    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                finish_reason=finish_reason or ("stop" if content is not None else None),
                delta=SimpleNamespace(content=content, tool_calls=tool_calls),
            )
        ]
    )


import json


class _FakeCompletions:
    def __init__(self, script):
        self.script = list(script)
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if not self.script:
            raise AssertionError("FakeOpenAIClient ran out of scripted responses")
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, list):
            return iter(item)

        chunks = []
        stop_reason = getattr(item, "stop_reason", "end_turn")
        finish_reason = "stop" if stop_reason == "end_turn" else stop_reason

        content = getattr(item, "content", [])
        if not content:
            chunks.append(openai_chunk(finish_reason=finish_reason))
        else:
            for b in content:
                b_type = getattr(b, "type", None)
                if b_type == "text":
                    chunks.append(openai_chunk(content=getattr(b, "text", ""), finish_reason=finish_reason))
                elif b_type == "tool_use":
                    name = getattr(b, "name", "")
                    tool_input = getattr(b, "input", {})
                    b_id = getattr(b, "id", "call_1")
                    tc = SimpleNamespace(
                        index=0,
                        id=b_id,
                        function=SimpleNamespace(name=name, arguments=json.dumps(tool_input))
                    )
                    chunks.append(SimpleNamespace(
                        choices=[
                            SimpleNamespace(
                                finish_reason="tool_calls",
                                delta=SimpleNamespace(content=None, tool_calls=[tc])
                            )
                        ]
                    ))
        return iter(chunks)


class FakeOpenAIClient:
    """Mimics openai.OpenAI closely enough for Brain to take the OpenAI path."""

    def __init__(self, script):
        self.chat = SimpleNamespace(completions=_FakeCompletions(script))


@pytest.fixture
def make_brain(monkeypatch):
    """Build a Brain wired to a scripted fake client."""
    import config

    monkeypatch.setattr(config, "API_KEY", "test-key", raising=False)
    monkeypatch.setattr(config, "DEBUG", False, raising=False)

    from core.brain import Brain

    def _make(script, confirm=None, on_text=None, store=None, on_action=None):
        client = FakeOpenAIClient(script)
        brain = Brain(client=client, confirm=confirm, on_text=on_text, store=store, on_action=on_action)
        return brain

    return _make
