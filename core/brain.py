"""The 'brain': sends user input to Claude, lets it call skills as tools,
and returns a final spoken/printed reply.

Key invariant: `self.history` is only ever replaced by a *well-formed*
conversation. Every turn is built in a local working copy and committed only
on success. If anything throws mid-turn, the working copy is discarded — so we
can never end up with an assistant `tool_use` block that has no matching
`tool_result`, which would 400 every subsequent request forever.
"""
import anthropic

import config
from skills.skill_manager import SkillManager

SYSTEM_PROMPT = f"""You are {config.ASSISTANT_NAME}, a helpful, witty, efficient AI assistant
running locally on the user's Windows PC, in the style of Iron Man's J.A.R.V.I.S.

Keep spoken replies concise — 1 to 3 sentences — since they may be read aloud.
Lead with the answer or the outcome, then any detail that changes what the user
would do next. Skip preamble and skip recapping what you just did.

Use the available tools to actually take actions on the PC (open apps, check
system info, control volume, set reminders, do math, etc.) whenever the user's
request calls for it, rather than just describing what you would do. If a
request doesn't need a tool, just answer directly.

Reach for these when they apply:
- web_search when the answer depends on current information — recent events,
  today's prices, release notes, anything that changed after your training data.
  Search rather than answering from memory, and don't ask a scoping question
  first unless the request is genuinely ambiguous.
- see_screen when the user refers to something on their display ("this error",
  "what am I looking at", "read this"). Look before you guess.
- clipboard to read what they just copied, or to hand back a result they want
  to paste somewhere.

Deliver what the user asked for at the scope they intended. Make routine
judgment calls yourself; check in only when different readings would lead to
materially different actions. If you think the request is mistaken, say so in
one sentence and carry on with what was asked — don't quietly widen it.

Some actions require the user's confirmation before they run. If a tool result
says the user declined, acknowledge it briefly and move on — do not retry."""


class BrainError(Exception):
    """Raised for API failures that already carry a user-facing message."""


def _confirm_always(skill, tool_input) -> bool:  # noqa: ARG001
    """Default confirmation callback: approve everything. main.py replaces this
    with a real prompt."""
    return True


class Brain:
    def __init__(self, client=None, confirm=None, on_text=None):
        """
        client:  an anthropic.Anthropic (injected for testing)
        confirm: callable(skill, tool_input) -> bool, asks the user to approve
                 a destructive action
        on_text: callable(str), called with each complete sentence as it
                 streams in, so speech can start before generation finishes
        """
        self.client = client or anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
        self.skills = SkillManager()
        self.confirm = confirm or _confirm_always
        self.on_text = on_text
        self.history: list[dict] = []
        self.last_usage = None
        self.spoke_during_last_turn = False

    # -- public API --------------------------------------------------------

    def ask(self, user_text: str) -> str:
        """Run one full turn. self.history is left untouched if this raises."""
        self.spoke_during_last_turn = False

        working = list(self.history)
        working.append({"role": "user", "content": user_text})

        # If _run_turn raises, `working` is discarded and self.history keeps its
        # last known-good value. That is the whole point of this indirection.
        reply, working = self._run_turn(working)

        self.history = working
        return reply

    def reset(self) -> None:
        self.history = []

    # -- internals ---------------------------------------------------------

    def _run_turn(self, working: list[dict]) -> tuple[str, list[dict]]:
        pause_turns = 0

        for _ in range(config.MAX_TOOL_ITERATIONS):
            response = self._call_model(working)

            if response.stop_reason == "refusal":
                # content may be empty on a pre-output refusal, and an empty
                # content list is an invalid message. Append our own text so
                # history stays well-formed and alternating.
                detail = getattr(response, "stop_details", None)
                category = getattr(detail, "category", None) if detail else None
                suffix = f" ({category})" if category else ""
                msg = f"I can't help with that one{suffix}."
                working.append({"role": "assistant", "content": [{"type": "text", "text": msg}]})
                return msg, working

            working.append({"role": "assistant", "content": response.content})

            if response.stop_reason == "pause_turn":
                # A server-side tool hit its internal iteration limit. Re-send
                # as-is to resume — do NOT append a "continue" message; the API
                # detects the trailing server_tool_use block itself.
                pause_turns += 1
                if pause_turns > 5:
                    return "That search ran longer than expected — try narrowing it?", working
                continue

            if response.stop_reason != "tool_use":
                return self._final_text(response), working

            tool_results = self._run_tools(response)
            if not tool_results:
                # stop_reason said tool_use but no tool_use blocks came back.
                # Appending an empty content list would make the next request
                # invalid, so end the turn on what we have.
                return self._final_text(response), working
            working.append({"role": "user", "content": tool_results})

        # Iteration cap. Close the turn cleanly so history stays valid — an
        # assistant text message is always a legal way to end.
        fallback = "That took more steps than expected — could you rephrase your request?"
        working.append({"role": "assistant", "content": [{"type": "text", "text": fallback}]})
        return fallback, working

    def _call_model(self, working: list[dict]):
        """One streamed request. Streaming gives us timeout protection on long
        turns and lets us speak sentences as they arrive."""
        kwargs = dict(
            model=config.CLAUDE_MODEL,
            max_tokens=config.MAX_TOKENS,
            # Adaptive thinking stays ON deliberately. Disabling it on Opus 5
            # makes the model occasionally write a tool call into its visible
            # text instead of emitting a tool_use block — the turn "succeeds",
            # the tool silently never runs, and nothing raises. Low effort is
            # the cheap lever; disabled thinking is not.
            thinking={"type": "adaptive"},
            output_config={"effort": config.EFFORT},
            system=self._system_blocks(),
            tools=self.skills.tool_definitions(),
            messages=self._cached(working),
        )

        with self.client.messages.stream(**kwargs) as stream:
            if self.on_text is not None:
                buffer = ""
                for chunk in stream.text_stream:
                    buffer += chunk
                    buffer, done = _drain_sentences(buffer)
                    for sentence in done:
                        self.spoke_during_last_turn = True
                        self.on_text(sentence)
                if buffer.strip():
                    self.spoke_during_last_turn = True
                    self.on_text(buffer.strip())
            response = stream.get_final_message()

        self.last_usage = response.usage
        if config.DEBUG:
            u = response.usage
            print(
                f"[usage] in={u.input_tokens} out={u.output_tokens} "
                f"cache_read={getattr(u, 'cache_read_input_tokens', 0)} "
                f"cache_write={getattr(u, 'cache_creation_input_tokens', 0)} "
                f"stop={response.stop_reason}"
            )
        return response

    @staticmethod
    def _system_blocks() -> list[dict]:
        """Render order is tools -> system -> messages, so a breakpoint on the
        last system block caches the tool schemas AND the system prompt.

        SYSTEM_PROMPT must stay byte-frozen — no timestamps, no session IDs.
        Anything volatile here invalidates the whole cached prefix every turn.
        """
        return [
            {
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ]

    @staticmethod
    def _cached(working: list[dict]) -> list[dict]:
        """Add a second cache breakpoint on the newest turn so multi-turn
        conversations accrue cache hits incrementally instead of only ever
        reading the tools+system prefix."""
        if not working:
            return working

        out = list(working)
        last = dict(out[-1])
        content = last.get("content")

        if isinstance(content, str):
            last["content"] = [
                {"type": "text", "text": content, "cache_control": {"type": "ephemeral"}}
            ]
        elif isinstance(content, list) and content and isinstance(content[-1], dict):
            blocks = [dict(b) if isinstance(b, dict) else b for b in content]
            blocks[-1] = {**blocks[-1], "cache_control": {"type": "ephemeral"}}
            last["content"] = blocks
        else:
            # SDK content objects (from a previous response) — leave alone.
            return out

        out[-1] = last
        return out

    def _run_tools(self, response) -> list[dict]:
        """Execute every tool_use block in this response and return ALL results
        in one list. They must go back as a single user message — splitting them
        across messages trains the model out of making parallel tool calls."""
        results = []
        for block in response.content:
            if block.type != "tool_use":
                continue

            skill = self.skills.get(block.name)
            if skill is None:
                results.append(_tool_result(block.id, f"Unknown skill: {block.name}", True))
                continue

            tool_input = dict(block.input or {})

            if config.CONFIRM_DESTRUCTIVE and skill.needs_confirmation(**tool_input):
                if not self.confirm(skill, tool_input):
                    results.append(
                        _tool_result(
                            block.id,
                            "The user declined this action. It was not performed.",
                            False,
                        )
                    )
                    continue

            content, is_error = self.skills.execute(block.name, tool_input)
            results.append(_tool_result(block.id, content, is_error))

        return results

    @staticmethod
    def _final_text(response) -> str:
        text = "".join(
            block.text for block in response.content if block.type == "text"
        ).strip()
        return text or "Done."


def _tool_result(tool_use_id: str, content, is_error: bool) -> dict:
    block = {"type": "tool_result", "tool_use_id": tool_use_id, "content": content}
    if is_error:
        block["is_error"] = True
    return block


_SENTENCE_ENDS = ".!?\n"


def _drain_sentences(buffer: str) -> tuple[str, list[str]]:
    """Split a streaming buffer into complete sentences plus the remainder.

    Returns (remaining_buffer, [complete sentences]). Used so speech can start
    before the model finishes generating.
    """
    out = []
    start = 0
    for i, ch in enumerate(buffer):
        if ch not in _SENTENCE_ENDS:
            continue
        # Don't split "3.14" or "e.g." mid-number/abbreviation.
        if ch == "." and i + 1 < len(buffer) and buffer[i + 1].isdigit():
            continue
        if i + 1 < len(buffer) and buffer[i + 1] not in " \n\t":
            continue
        sentence = buffer[start : i + 1].strip()
        if sentence:
            out.append(sentence)
        start = i + 1
    return buffer[start:], out


def friendly_error(exc: Exception) -> str:
    """Map an SDK exception to something worth saying out loud.

    Ordered most-specific first — a single broad `except APIStatusError` would
    lose the retryable/non-retryable distinction.
    """
    if isinstance(exc, anthropic.AuthenticationError):
        return "My API key was rejected. Check ANTHROPIC_API_KEY in your .env file."
    if isinstance(exc, anthropic.NotFoundError):
        return f"The model '{config.CLAUDE_MODEL}' wasn't found. Check CLAUDE_MODEL in .env."
    if isinstance(exc, anthropic.RateLimitError):
        return "I'm being rate limited. Give me a moment and try again."
    if isinstance(exc, anthropic.APIStatusError):
        if exc.status_code >= 500:
            return "Anthropic's API is having trouble. Try again in a moment."
        return f"The API rejected that request: {exc.message}"
    if isinstance(exc, anthropic.APIConnectionError):
        return "I can't reach the network right now."
    return f"Something went wrong: {exc}"
