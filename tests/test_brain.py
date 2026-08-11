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


def test_moderate_action_notifies_without_confirming(make_brain, monkeypatch):
    """A MODERATE action runs immediately — no prompt — and is announced so
    the UI can offer undo."""
    from core.undo import UndoJournal, set_journal
    notified = []
    confirmed = []

    journal = UndoJournal(max_age_seconds=900)
    set_journal(journal)
    monkeypatch.setattr("skills.system_skills.IS_WINDOWS", True)
    monkeypatch.setattr("skills.system_skills.subprocess.run", lambda *a, **k: None)

    brain = make_brain(
        [
            response([tool_use_block("power_control", {"action": "lock"})], stop_reason="tool_use"),
            response([text_block("Locked.")]),
        ],
        confirm=lambda s, i: confirmed.append(i) or True,
        on_action=lambda skill, tool_input, outcome: notified.append(skill.name),
    )
    brain.ask("lock the pc")
    set_journal(None)

    assert confirmed == [], "lock is MODERATE and must not prompt"


def test_dangerous_action_still_confirms(make_brain, monkeypatch):
    ran = []
    monkeypatch.setattr("skills.system_skills.IS_WINDOWS", True)
    monkeypatch.setattr("skills.system_skills.subprocess.run", lambda *a, **k: ran.append(a[0]))

    brain = make_brain(
        [
            response([tool_use_block("power_control", {"action": "shutdown"})], stop_reason="tool_use"),
            response([text_block("Cancelled.")]),
        ],
        confirm=lambda skill, tool_input: False,
    )
    brain.ask("shut down")

    assert ran == []
    assert "declined" in brain.history[2]["content"][0]["content"]


def test_on_action_receives_the_undo_token(make_brain):
    """A skill that records an undo entry must surface its token so the UI can
    offer a working undo button."""
    from skills.base_skill import BaseSkill
    from core.risk import Risk
    from core.undo import UndoJournal, get_journal, set_journal

    journal = UndoJournal(max_age_seconds=900)
    set_journal(journal)

    class Reversible(BaseSkill):
        name = "reversible_thing"
        description = "test"
        input_schema = {"type": "object", "properties": {}, "required": []}
        risk = Risk.MODERATE

        def run(self):
            get_journal().record("Put it back", lambda: "Put back.")
            return "Did it."

    seen = []
    brain = make_brain(
        [
            response([tool_use_block("reversible_thing", {})], stop_reason="tool_use"),
            response([text_block("Done.")]),
        ],
        on_action=lambda skill, ti, outcome: seen.append(outcome.undo_token),
    )
    brain.skills.skills["reversible_thing"] = Reversible()
    brain.ask("do it")
    set_journal(None)

    assert len(seen) == 1 and seen[0], "on_action should carry a usable undo token"


# -- error mapping ---------------------------------------------------------


def _http_error(cls, status: int):
    """Build an SDK error without going through its normal HTTP plumbing."""
    exc = cls.__new__(cls)
    Exception.__init__(exc, f"simulated {status}")
    exc.status_code = status
    return exc


def test_friendly_error_maps_every_tier_without_raising():
    """Regression: the tiers were built by concatenating a tuple with a bare
    class, so anything past the auth check raised TypeError from inside the
    handler that was supposed to be reporting the problem."""
    import openai

    from core.brain import friendly_error

    cases = {
        openai.AuthenticationError: 401,
        openai.PermissionDeniedError: 403,
        openai.NotFoundError: 404,
        openai.RateLimitError: 429,
        openai.InternalServerError: 500,
    }
    for cls, status in cases.items():
        message = friendly_error(_http_error(cls, status))
        assert isinstance(message, str) and message

    assert "rejected" in friendly_error(_http_error(openai.AuthenticationError, 401))
    assert "not found" in friendly_error(_http_error(openai.NotFoundError, 404))
    assert "rate limited" in friendly_error(_http_error(openai.RateLimitError, 429))
    assert "trouble" in friendly_error(_http_error(openai.InternalServerError, 500))
    assert "Something went wrong" in friendly_error(ValueError("plain"))


def test_friendly_error_handles_anthropic_errors_when_the_sdk_is_present():
    """The Anthropic SDK is optional. When it is installed its exception types
    must be recognised too, and when it isn't, nothing may break."""
    from core.brain import _sdk_errors, friendly_error

    try:
        import anthropic
    except ImportError:
        assert _sdk_errors("NotFoundError")  # openai's is always there
        return

    assert anthropic.NotFoundError in _sdk_errors("NotFoundError")
    assert "not found" in friendly_error(_http_error(anthropic.NotFoundError, 404))
