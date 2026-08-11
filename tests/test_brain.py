"""Tests for the conversation loop.

The headline test is test_history_survives_midturn_failure — that's the
regression guard for the bug where an exception between a tool_use block and
its tool_result left history permanently invalid, 400ing every later request.
"""
import openai
import pytest

from conftest import response, text_block, tool_use_block


def test_simple_turn_commits_history(make_brain):
    brain = make_brain([response([text_block("Hello there.")])])

    reply = brain.ask("hi")

    assert reply == "Hello there."
    assert len(brain.history) == 2
    assert brain.history[0] == {"role": "user", "content": "hi"}
    assert brain.history[1]["role"] == "assistant"


def test_tool_call_roundtrip(make_brain):
    brain = make_brain(
        [
            response([tool_use_block("get_time_date", {})], stop_reason="tool_use"),
            response([text_block("It's just past nine.")]),
        ]
    )

    reply = brain.ask("what time is it")

    assert reply == "It's just past nine."
    # user -> assistant(tool_use) -> user(tool_result) -> assistant(text)
    assert [m["role"] for m in brain.history] == ["user", "assistant", "user", "assistant"]
    tool_results = brain.history[2]["content"]
    assert tool_results[0]["type"] == "tool_result"
    assert tool_results[0]["tool_use_id"] == "toolu_1"


def test_history_survives_midturn_failure(make_brain):
    """The regression test for the original brain-killer.

    A failure between an assistant tool_use block and its tool_result must not
    leave that dangling block in history — every later request would 400.
    """
    brain = make_brain(
        [
            response([tool_use_block("get_time_date", {})], stop_reason="tool_use"),
            openai.APIConnectionError(request=None),
            response([text_block("Recovered fine.")]),
        ]
    )

    brain.history = [
        {"role": "user", "content": "earlier"},
        {"role": "assistant", "content": [text_block("earlier reply")]},
    ]
    snapshot = list(brain.history)

    with pytest.raises(openai.APIConnectionError):
        brain.ask("what time is it")

    # History is byte-identical to before the failed turn.
    assert brain.history == snapshot

    # And the next turn works rather than 400ing forever.
    assert brain.ask("try again") == "Recovered fine."


def test_history_never_ends_on_dangling_tool_use(make_brain):
    """Even on the iteration cap, the committed history must be well-formed."""
    import config

    script = [
        response([tool_use_block("get_time_date", {}, f"toolu_{i}")], stop_reason="tool_use")
        for i in range(config.MAX_TOOL_ITERATIONS)
    ]
    brain = make_brain(script)

    brain.ask("loop forever")

    assert brain.history[-1]["role"] == "assistant"
    last_blocks = brain.history[-1]["content"]
    assert all(b.get("type") != "tool_use" for b in last_blocks if isinstance(b, dict))


def test_refusal_produces_valid_history(make_brain):
    """A pre-output refusal has empty content; appending it verbatim would make
    the next request invalid."""
    brain = make_brain(
        [
            response(
                [],
                stop_reason="refusal",
                stop_details=type("D", (), {"category": "cyber"})(),
            ),
            response([text_block("Sure, here you go.")]),
        ]
    )

    reply = brain.ask("do something disallowed")

    assert "can't help" in reply
    assert brain.history[-1]["content"], "assistant message must not be empty"
    assert brain.ask("something benign") == "Sure, here you go."


def test_tool_error_is_flagged(make_brain):
    """A failing skill must come back with is_error so the model can adapt."""
    brain = make_brain(
        [
            response(
                [tool_use_block("calculate", {"expression": "2 ** 999999999"})],
                stop_reason="tool_use",
            ),
            response([text_block("That number is too big.")]),
        ]
    )

    brain.ask("compute 2 to the billion")

    result = brain.history[2]["content"][0]
    assert result["type"] == "tool_result"
    assert "couldn't evaluate" in result["content"]


def test_unknown_tool_is_flagged(make_brain):
    brain = make_brain(
        [
            response([tool_use_block("no_such_skill", {})], stop_reason="tool_use"),
            response([text_block("I don't have that.")]),
        ]
    )

    brain.ask("do the thing")

    result = brain.history[2]["content"][0]
    assert result["is_error"] is True
    assert "Unknown skill" in result["content"]


def test_pause_turn_resumes_without_extra_message(make_brain):
    """Server-side tools pause; resuming must re-send as-is, with no synthetic
    'continue' message appended."""
    brain = make_brain(
        [
            response([text_block("searching")], stop_reason="pause_turn"),
            response([text_block("Found it.")]),
        ]
    )

    assert brain.ask("search the web") == "Found it."
    second_call = brain.client.chat.completions.calls[1]
    assert second_call["messages"][-1]["role"] == "assistant"


def test_streaming_emits_sentences(make_brain):
    spoken = []
    brain = make_brain(
        [response([text_block("First sentence. Second one! Third?")])],
        on_text=spoken.append,
    )

    brain.ask("talk")

    assert spoken == ["First sentence.", "Second one!", "Third?"]
    assert brain.spoke_during_last_turn is True


def test_decimals_do_not_split_sentences(make_brain):
    spoken = []
    brain = make_brain([response([text_block("Pi is 3.14 roughly.")])], on_text=spoken.append)

    brain.ask("pi")

    assert spoken == ["Pi is 3.14 roughly."]
