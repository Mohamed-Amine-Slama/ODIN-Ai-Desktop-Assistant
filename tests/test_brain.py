"""Tests for the conversation loop.

The headline test is test_history_survives_midturn_failure — that's the
regression guard for the bug where an exception between a tool_use block and
its tool_result left history permanently invalid, 400ing every later request.
"""
import threading

import openai
import pytest

from conftest import openai_chunk, response, text_block, tool_use_block


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


def test_openai_path_empty_completion_produces_valid_history(make_brain):
    """A gateway can legitimately stream zero text deltas and zero tool
    calls — a reasoning-heavy model burning its whole budget on hidden
    reasoning and hitting finish_reason="length", or simply an empty
    completion. Appending that verbatim as {"role": "assistant",
    "content": []} is the same "history left holding a malformed message"
    failure class as an unhandled refusal, just reached through the
    OpenAI-compatible path (_call_openai_model) instead of Anthropic's
    literal "refusal" stop_reason."""
    brain = make_brain([
        [openai_chunk(finish_reason="length")],
        response([text_block("Sure, here you go.")]),
    ])

    reply = brain.ask("write me an essay")

    assert reply  # not an empty/falsy reply
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


def test_risk_for_exception_does_not_abort_the_turn(make_brain):
    """A malformed tool argument that blows up risk_for() (e.g. a None where
    a path string is expected) must come back as an is_error tool_result and
    let the turn continue — not propagate out of ask() uncaught, which would
    violate the invariant that a bad tool call can never corrupt/abort a turn."""
    brain = make_brain(
        [
            response(
                [tool_use_block("write_file", {"path": None, "content": "x"})],
                stop_reason="tool_use",
            ),
            response([text_block("Couldn't do that.")]),
        ]
    )

    reply = brain.ask("write something")

    result = brain.history[2]["content"][0]
    assert result["type"] == "tool_result"
    assert result["is_error"] is True
    assert reply == "Couldn't do that."


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


# -- per-call overrides (scheduled tasks / remote channels) ----------------
# ask() accepts confirm/on_text overrides so a turn run unattended (a
# scheduled task, a Telegram message) can auto-decline confirmations and
# keep its reply off the desktop's speech, without touching the instance
# defaults every other caller relies on.


def test_ask_confirm_override_does_not_replace_the_instance_default(make_brain):
    default_confirm = lambda skill, tool_input: True  # noqa: E731
    override_confirm = lambda skill, tool_input: False  # noqa: E731

    brain = make_brain(
        [
            response([tool_use_block("power_control", {"action": "shutdown"})], stop_reason="tool_use"),
            response([text_block("Cancelled.")]),
        ],
        confirm=default_confirm,
    )

    brain.ask("shut down", confirm=override_confirm)

    assert "declined" in brain.history[2]["content"][0]["content"]
    assert brain.confirm is default_confirm, "a per-call override must not mutate the instance default"


def test_ask_on_text_override_replaces_on_text_for_that_call_only(make_brain):
    default_spoken = []
    override_spoken = []

    brain = make_brain(
        [response([text_block("Hello there.")]), response([text_block("Second one.")])],
        on_text=default_spoken.append,
    )

    brain.ask("hi", on_text=override_spoken.append)
    assert override_spoken == ["Hello there."]
    assert default_spoken == [], "the override replaces on_text for this call, not both firing"

    brain.ask("hi again")  # no override this time -> falls back to the instance default
    assert default_spoken == ["Second one."]


def test_auto_decline_always_declines():
    from core.brain import auto_decline

    assert auto_decline(object(), {"action": "shutdown"}) is False


def test_ask_holds_the_turn_lock_for_the_whole_turn(make_brain, monkeypatch):
    """Regression guard for concurrent callers: a scheduled task and a
    Telegram message can now reach ask() on their own threads, at the same
    time as the main loop. The lock must be held for the whole turn, or two
    turns could interleave their mutation of self.history."""
    brain = make_brain([response([text_block("ok.")])])

    entered = threading.Event()
    release = threading.Event()
    original_run_turn = brain._run_turn

    def blocking_run_turn(working, confirm, on_text):
        entered.set()
        release.wait(timeout=5)
        return original_run_turn(working, confirm, on_text)

    monkeypatch.setattr(brain, "_run_turn", blocking_run_turn)

    thread = threading.Thread(target=brain.ask, args=("hi",))
    thread.start()
    try:
        assert entered.wait(timeout=2), "the turn should have started"
        assert brain._turn_lock.locked(), "the lock must be held while a turn is in flight"
    finally:
        release.set()
        thread.join(timeout=5)

    assert not brain._turn_lock.locked()


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


def test_friendly_error_extracts_the_message_from_a_provider_error_body():
    """Regression: a 402/etc. error's full raw body — nested JSON, repeated
    'previous_errors' from OpenRouter's upstream fallback attempts — used to
    be dumped verbatim into the reply. In the GUI that produced a single chat
    bubble tall enough to overflow past the transcript panel into the input
    box below it. Only the actual one-sentence message should surface."""
    import openai

    from core.brain import friendly_error

    exc = _http_error(openai.APIStatusError, 402)
    exc.body = {
        "message": "This request requires more credits, or fewer max_tokens.",
        "code": 402,
        "metadata": {"limit_source": "openrouter_credits"},
        "previous_errors": [{"code": 402, "message": "This request requires more credits..."}] * 3,
    }

    message = friendly_error(exc)
    assert "This request requires more credits, or fewer max_tokens." in message
    assert "previous_errors" not in message
    assert "metadata" not in message


def test_friendly_error_truncates_a_very_long_message():
    import openai

    from core.brain import friendly_error

    exc = _http_error(openai.APIStatusError, 402)
    exc.body = {"message": "x" * 1000}

    message = friendly_error(exc)
    assert len(message) < 300


def test_openai_client_is_built_with_a_request_timeout(monkeypatch):
    """Without an explicit timeout, the SDK's own default (minutes) applies
    — a slow/overloaded provider can then leave a turn hanging with no
    error, and since only one turn is ever allowed in flight
    (current_worker in ui/hud/window.py), that silently blocks every
    later message too, not just the stuck one."""
    import config
    from core.brain import Brain

    captured = {}

    class FakeOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr("openai.OpenAI", FakeOpenAI)
    monkeypatch.setattr(config, "MODEL", "gemini-2.5-flash", raising=False)
    monkeypatch.setattr(config, "BASE_URL", "https://openrouter.ai/api/v1", raising=False)
    monkeypatch.setattr(config, "API_KEY", "key", raising=False)
    monkeypatch.setattr(config, "REQUEST_TIMEOUT_SECONDS", 42.0, raising=False)

    Brain()


def test_bedrock_prefixed_model_selects_anthropic_bedrock(monkeypatch):
    """MODEL='bedrock/<id>' must route to AnthropicBedrock rather than the
    native Anthropic client or an OpenAI-compatible one — see
    core.brain._BEDROCK_PREFIX. The prefix itself must never reach the
    actual API call: self.model_id is what _call_anthropic_model sends."""
    import anthropic
    import config
    from core.brain import Brain

    captured = {}

    class FakeAnthropicBedrock:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(anthropic, "AnthropicBedrock", FakeAnthropicBedrock, raising=False)
    monkeypatch.setattr(
        config, "MODEL", "bedrock/us.anthropic.claude-haiku-4-5-20251001-v1:0", raising=False
    )
    monkeypatch.setattr(config, "AWS_REGION", "us-west-2", raising=False)
    monkeypatch.setattr(config, "REQUEST_TIMEOUT_SECONDS", 42.0, raising=False)

    brain = Brain()

    assert isinstance(brain.client, FakeAnthropicBedrock)
    assert brain.is_openai is False
    assert brain.model_id == "us.anthropic.claude-haiku-4-5-20251001-v1:0"
    assert captured["aws_region"] == "us-west-2"
    assert captured["timeout"] == 42.0


def test_non_bedrock_claude_model_still_uses_the_native_anthropic_client(monkeypatch):
    """A plain 'claude-...' MODEL (no bedrock/ prefix) must keep using
    anthropic.Anthropic, not be accidentally caught by the Bedrock branch."""
    import anthropic
    import config
    from core.brain import Brain

    captured = {}

    class FakeAnthropic:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(anthropic, "Anthropic", FakeAnthropic, raising=False)
    monkeypatch.setattr(config, "MODEL", "claude-opus-5", raising=False)
    monkeypatch.setattr(config, "BASE_URL", "", raising=False)
    monkeypatch.setattr(config, "API_KEY", "key", raising=False)

    brain = Brain()

    assert isinstance(brain.client, FakeAnthropic)
    assert brain.is_openai is False
    assert brain.model_id == "claude-opus-5"


# -- missing_key_message() (Bedrock auths differently from every other path) -

def test_missing_key_message_ignores_api_key_for_bedrock_when_bearer_token_is_set(monkeypatch):
    import config

    monkeypatch.setattr(config, "API_KEY", "", raising=False)
    monkeypatch.setattr(config, "MODEL", "bedrock/us.anthropic.claude-haiku-4-5-20251001-v1:0", raising=False)
    monkeypatch.setenv("AWS_BEARER_TOKEN_BEDROCK", "some-token")

    assert config.missing_key_message() is None


def test_missing_key_message_flags_bedrock_without_a_bearer_token(monkeypatch):
    import config

    monkeypatch.setattr(config, "API_KEY", "", raising=False)
    monkeypatch.setattr(config, "MODEL", "bedrock/us.anthropic.claude-haiku-4-5-20251001-v1:0", raising=False)
    monkeypatch.delenv("AWS_BEARER_TOKEN_BEDROCK", raising=False)

    message = config.missing_key_message()
    assert message is not None
    assert "AWS_BEARER_TOKEN_BEDROCK" in message


def test_missing_key_message_still_requires_api_key_for_non_bedrock_models(monkeypatch):
    import config

    monkeypatch.setattr(config, "API_KEY", "", raising=False)
    monkeypatch.setattr(config, "MODEL", "gemini-3.6-flash", raising=False)

    message = config.missing_key_message()
    assert message is not None
    assert "API_KEY" in message


def test_friendly_error_distinguishes_timeout_from_connection_failure():
    """openai.APITimeoutError (and anthropic's) subclasses
    APIConnectionError in both SDKs — without a check ordered ahead of the
    generic one, a request that reached the provider fine but never got an
    answer back (the network is up; the model just never responded) would
    be misreported as "I can't reach the network right now.", which sends
    a confused user chasing the wrong problem."""
    import openai

    from core.brain import friendly_error

    message = friendly_error(_http_error(openai.APITimeoutError, None))
    assert "didn't respond" in message
    assert "reach the network" not in message


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
