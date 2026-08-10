"""Tests for the dual-provider path (Anthropic vs OpenAI-compatible).

Two failure modes are easy to reintroduce here:
  1. Telling the model to use a tool the active provider wasn't given.
  2. Stringifying an image content block into the request as base64 text.
"""
from core.brain import _split_tool_result, build_system_prompt


# -- system prompt tracks the real tool set --------------------------------

def test_prompt_describes_web_search_when_present():
    prompt = build_system_prompt({"web_search", "see_screen", "clipboard"})
    assert "web_search when the answer depends" in prompt
    assert "no web access" not in prompt


def test_prompt_omits_web_search_when_absent():
    """SERVER_TOOLS are Anthropic-only. On an OpenAI-compatible endpoint the
    model never receives web_search, so the prompt must not tell it to call
    one — and should say plainly that it can't check current information."""
    prompt = build_system_prompt({"see_screen", "clipboard", "calculate"})
    assert "web_search when the answer depends" not in prompt
    assert "no web access" in prompt


def test_prompt_omits_vision_guidance_without_the_skill():
    prompt = build_system_prompt({"calculate"})
    assert "see_screen" not in prompt


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
    from conftest import FakeOpenAIClient
    from core.brain import Brain

    assert Brain(client=FakeOpenAIClient([[]])).is_openai is True


def test_openai_prompt_has_no_web_search():
    """The integration of the two fixes: on the OpenAI path the tool list has
    no web_search, so the prompt must not advertise one."""
    from conftest import FakeOpenAIClient
    from core.brain import Brain

    brain = Brain(client=FakeOpenAIClient([[]]))
    assert "no web access" in brain.system_prompt
    assert "web_search when the answer depends" not in brain.system_prompt
