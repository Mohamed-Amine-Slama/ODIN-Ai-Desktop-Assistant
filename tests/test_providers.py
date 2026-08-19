"""Tests for the dual-provider path (Anthropic vs OpenAI-compatible).

Two failure modes are easy to reintroduce here:
  1. Telling the model to use a tool the active provider wasn't given.
  2. Stringifying an image content block into the request as base64 text.
"""
from types import SimpleNamespace

import httpx

from core.brain import Brain, _split_tool_result, build_system_prompt
from conftest import FakeClient, FakeOpenAIClient, openai_chunk, response, text_block


# -- system prompt tracks the real tool set --------------------------------

def test_prompt_describes_web_search_when_present():
    prompt = build_system_prompt({"web_search", "see_screen", "clipboard"})
    assert "web_search when the answer depends" in prompt
    assert "You have no search tool" not in prompt


def test_prompt_omits_web_search_when_absent():
    """SERVER_TOOLS are Anthropic-only. On an OpenAI-compatible endpoint the
    model never receives web_search, so the prompt must not tell it to call
    one — and should say plainly that it can't check current information."""
    prompt = build_system_prompt({"see_screen", "clipboard", "calculate"})
    assert "web_search when the answer depends" not in prompt
    assert "You have no search tool" in prompt


def test_prompt_omits_vision_guidance_without_the_skill():
    prompt = build_system_prompt({"calculate"})
    assert "see_screen" not in prompt


def test_prompt_prefers_the_dom_path_when_the_browser_tools_are_present():
    prompt = build_system_prompt({"browser_navigate", "see_screen"})
    assert "browser_navigate, then browser_read" in prompt
    assert "drive websites directly" in prompt


def test_prompt_omits_browser_guidance_when_the_feature_is_off():
    """Off by default, and off again on a machine without playwright. Telling
    the model to prefer a tool it wasn't given just produces a hallucinated
    call."""
    prompt = build_system_prompt({"see_screen", "clipboard"})
    assert "browser_navigate" not in prompt


def test_prompt_is_stable_for_the_same_tool_set():
    """A prompt that varies between turns invalidates the whole cached prefix."""
    tools = {"web_search", "see_screen", "clipboard", "memory"}
    assert build_system_prompt(tools) == build_system_prompt(set(tools))


# -- tool_result conversion ------------------------------------------------

def test_split_plain_string():
    assert _split_tool_result("CPU is at 12%") == ("CPU is at 12%", [])


def test_split_text_blocks():
    text, images = _split_tool_result([{"type": "text", "text": "hello"}])
    assert text == "hello"
    assert images == []


def test_split_image_becomes_a_data_url_not_a_stringified_dict():
    """The bug: str() of an image block put a raw base64 literal into the
    request as plain text."""
    payload = [
        {
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": "QUJD"},
        },
        {"type": "text", "text": "Screenshot of the screen."},
    ]

    text, images = _split_tool_result(payload)

    assert "QUJD" not in text, "base64 leaked into the text field"
    assert text == "Screenshot of the screen."
    assert images == [
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,QUJD"}}
    ]


def test_split_image_only_gets_a_placeholder():
    text, images = _split_tool_result(
        [{"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "X"}}]
    )
    assert text.strip() != ""
    assert len(images) == 1


def test_openai_request_carries_image_as_multimodal_part(monkeypatch):
    """End-to-end: a see_screen result must reach an OpenAI-shaped request as
    an image_url part in a user turn, not as text."""
    import config
    from conftest import FakeOpenAIClient, openai_chunk
    from core.brain import Brain

    monkeypatch.setattr("skills.vision_skills._grab", lambda monitor: b"PNG")

    client = FakeOpenAIClient(
        [
            [openai_chunk(tool_call=("see_screen", "{}"))],
            [openai_chunk(content="I see a terminal.")],
        ]
    )
    brain = Brain(client=client)

    brain.ask("what's on my screen")

    sent = client.chat.completions.calls[1]["messages"]
    tool_msg = next(m for m in sent if m["role"] == "tool")
    assert isinstance(tool_msg["content"], str)
    assert "base64" not in tool_msg["content"]

    image_msg = next(m for m in sent if isinstance(m.get("content"), list))
    assert image_msg["role"] == "user"
    assert image_msg["content"][0]["type"] == "image_url"
    assert image_msg["content"][0]["image_url"]["url"].startswith("data:image/png;base64,")


def test_openai_path_is_detected_from_the_client():
    assert Brain(client=FakeOpenAIClient([[]])).is_openai is True


def test_openai_prompt_advertises_web_search_when_available():
    """web_search is a local DuckDuckGo skill (ddgs ships as a core
    dependency, no key/gating needed), so — unlike Anthropic's server-side
    web_search, which SERVER_TOOLS keeps Anthropic-only — it's available on
    the OpenAI-compatible path too, and the prompt must say so."""
    brain = Brain(client=FakeOpenAIClient([[]]))
    assert "web_search when the answer depends" in brain.system_prompt
    assert "You have no search tool" not in brain.system_prompt


def test_anthropic_request_uses_native_streaming_thinking_and_caching(monkeypatch):
    """Claude gets its native API features, while Gemini keeps its OpenAI path."""
    import config

    monkeypatch.setattr(config, "MODEL", "claude-opus-5")
    monkeypatch.setattr(config, "EFFORT", "low")
    client = FakeClient([response([text_block("Ready.")])])
    brain = Brain(client=client)

    assert brain.is_openai is False
    assert "web_search when the answer depends" in brain.system_prompt
    assert brain.ask("hello") == "Ready."

    request = client.messages.calls[0]
    assert request["thinking"] == {"type": "adaptive"}
    assert request["output_config"] == {"effort": "low"}
    assert request["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert request["messages"][-1]["content"][-1]["cache_control"] == {"type": "ephemeral"}
    assert {tool["name"] for tool in request["tools"]} >= {"web_search", "web_fetch"}


def test_cache_breakpoint_handles_sdk_content_blocks():
    class SdkBlock:
        def model_dump(self):
            return {"type": "text", "text": "previous reply"}

    cached = Brain._cached(
        [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": [SdkBlock()]},
        ]
    )

    assert cached[-1]["content"][-1] == {
        "type": "text",
        "text": "previous reply",
        "cache_control": {"type": "ephemeral"},
    }


# -- reasoning_effort negotiation ------------------------------------------


class _RejectsEffort:
    """An endpoint that 400s on a reasoning parameter, the way a model with no
    reasoning control does. Records every request it was sent."""

    def __init__(self, script):
        self.calls: list[dict] = []
        self._script = list(script)
        self.chat = SimpleNamespace(completions=self)

    def create(self, **kwargs):
        import openai

        self.calls.append(kwargs)
        has_reasoning = "reasoning_effort" in kwargs or "reasoning" in kwargs.get("extra_body", {})
        if has_reasoning:
            raise openai.BadRequestError(
                "Unrecognized request argument supplied: reasoning",
                response=httpx.Response(400, request=httpx.Request("POST", "http://x")),
                body=None,
            )
        return iter([openai_chunk(content="Hi.", finish_reason="stop")])


def test_effort_is_dropped_and_retried_when_the_model_rejects_it(monkeypatch):
    """A non-OpenRouter OpenAI-compatible endpoint (e.g. Gemini's own) uses the
    flat `reasoning_effort` field. A model with no reasoning control must not
    turn every single turn into a hard failure."""
    import config
    from core.brain import Brain

    monkeypatch.setattr(config, "EFFORT", "low", raising=False)
    monkeypatch.setattr(config, "BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai/", raising=False)
    client = _RejectsEffort([])
    brain = Brain(client=client)

    assert brain.ask("hello") == "Hi."
    assert "reasoning_effort" in client.calls[0]
    assert "reasoning_effort" not in client.calls[1]
    assert brain._send_effort is False


def test_effort_off_is_never_sent(monkeypatch):
    import config
    from core.brain import Brain

    monkeypatch.setattr(config, "EFFORT", "off", raising=False)
    monkeypatch.setattr(config, "BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai/", raising=False)
    client = _RejectsEffort([])
    brain = Brain(client=client)

    assert brain.ask("hello") == "Hi."
    assert len(client.calls) == 1, "no retry should have been needed"
    assert "reasoning_effort" not in client.calls[0]


def test_openrouter_uses_the_nested_reasoning_object_via_extra_body(monkeypatch):
    """Regression, in two layers:

    1. OpenRouter's actual reasoning control is the nested `reasoning:
       {"effort": ...}` object, not OpenAI's flat `reasoning_effort` string —
       sending the flat field gets silently dropped rather than rejected, so
       a reasoning-capable model (e.g. Gemini 2.5 Flash) ran at its own
       default thinking depth on every call instead of config.EFFORT, with no
       error to signal it.
    2. `reasoning` is not part of the real openai SDK's typed create()
       signature the way `reasoning_effort` is, so it has to travel inside
       `extra_body` — passing it as a bare kwarg raises a client-side
       TypeError before any request reaches the network (this fake, being a
       plain function, wouldn't itself catch that mistake; it was only caught
       by testing against the real SDK directly).
    """
    import config
    from core.brain import Brain

    monkeypatch.setattr(config, "EFFORT", "low", raising=False)
    monkeypatch.setattr(config, "BASE_URL", "https://openrouter.ai/api/v1", raising=False)
    client = _RejectsEffort([])
    brain = Brain(client=client)

    assert brain.ask("hello") == "Hi."
    assert client.calls[0]["extra_body"] == {"reasoning": {"effort": "low"}}
    assert "reasoning_effort" not in client.calls[0]


def test_openrouter_effort_is_dropped_and_retried_when_rejected(monkeypatch):
    import config
    from core.brain import Brain

    monkeypatch.setattr(config, "EFFORT", "low", raising=False)
    monkeypatch.setattr(config, "BASE_URL", "https://openrouter.ai/api/v1", raising=False)
    client = _RejectsEffort([])
    brain = Brain(client=client)

    assert brain.ask("hello") == "Hi."
    assert "extra_body" in client.calls[0]
    assert "extra_body" not in client.calls[1]
    assert brain._send_effort is False
