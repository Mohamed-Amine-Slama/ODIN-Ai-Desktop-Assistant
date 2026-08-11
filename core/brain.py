"""The 'brain': sends user input to Google Gemini API (via OpenAI-compatible endpoint),
lets it call skills as tools, and returns a final spoken/printed reply.

Key invariant: `self.history` is only ever replaced by a *well-formed*
conversation. Every turn is built in a local working copy and committed only
on success. If anything throws mid-turn, the working copy is discarded.
"""
import json
from types import SimpleNamespace
import openai

import config
from skills.skill_manager import SkillManager

_BASE_PROMPT = f"""You are {config.ASSISTANT_NAME}, a helpful, witty, efficient AI assistant
running locally on the user's Windows PC, in the style of Iron Man's J.A.R.V.I.S.

Keep spoken replies concise — 1 to 3 sentences — since they may be read aloud.
Lead with the answer or the outcome, then any detail that changes what the user
would do next. Skip preamble and skip recapping what you just did.

Use the available tools to actually take actions on the PC (open apps, check
system info, control volume, set reminders, do math, etc.) whenever the user's
request calls for it, rather than just describing what you would do. If a
request doesn't need a tool, just answer directly."""

# Guidance for tools that are not always present. The tool set differs by
# provider — the Anthropic-hosted web tools don't exist on an OpenAI-compatible
# endpoint — and a prompt that tells the model to call a tool it wasn't given
# just produces confusion or a hallucinated call.
_TOOL_GUIDANCE = {
    "web_search": (
        "- web_search when the answer depends on current information — recent\n"
        "  events, today's prices, release notes, anything that changed after\n"
        "  your training data. Search rather than answering from memory, and\n"
        "  don't ask a scoping question first unless the request is genuinely\n"
        "  ambiguous."
    ),
    "see_screen": (
        "- see_screen when the user refers to something on their display\n"
        '  ("this error", "what am I looking at", "read this"). Look before\n'
        "  you guess."
    ),
    "clipboard": (
        "- clipboard to read what they just copied, or to hand back a result\n"
        "  they want to paste somewhere."
    ),
    "memory": (
        "- memory to remember durable facts the user tells you (preferences,\n"
        "  hardware, names) and to recall them in later sessions."
    ),
}

_CLOSING = """Deliver what the user asked for at the scope they intended. Make routine
judgment calls yourself; check in only when different readings would lead to
materially different actions. If you think the request is mistaken, say so in
one sentence and carry on with what was asked — don't quietly widen it.

Some actions require the user's confirmation before they run. If a tool result
says the user declined, acknowledge it briefly and move on — do not retry."""


def build_system_prompt(available_tools: set[str]) -> str:
    """Assemble the system prompt from the tools this provider actually has."""
    parts = [_BASE_PROMPT]

    guidance = [_TOOL_GUIDANCE[n] for n in _TOOL_GUIDANCE if n in available_tools]
    if guidance:
        parts.append("Reach for these when they apply:\n" + "\n".join(guidance))

    if "web_search" not in available_tools:
        parts.append(
            "You have no web access. When a question depends on current "
            "information, answer from what you know and say plainly that you "
            "couldn't check for anything more recent."
        )

    parts.append(_CLOSING)
    return "\n\n".join(parts)


class BrainError(Exception):
    """Raised for API failures that already carry a user-facing message."""


def _confirm_always(skill, tool_input) -> bool:  # noqa: ARG001
    """Default confirmation callback: approve everything. main.py replaces this
    with a real prompt."""
    return True


class Brain:
    def __init__(self, client=None, confirm=None, on_text=None, store=None):
        """
        client:  an openai.OpenAI or mock client (injected for testing)
        confirm: callable(skill, tool_input) -> bool, asks the user to approve
                 a destructive action
        on_text: callable(str), called with each complete sentence as it
                 streams in, so speech can start before generation finishes
        store:   a core.store.Store for conversation persistence, or None to
                 keep the conversation in memory only
        """
        if client is not None:
            self.client = client
        else:
            self.client = openai.OpenAI(
                api_key=config.API_KEY,
                base_url=config.BASE_URL or None,
            )
        self.is_openai = True

        self.skills = SkillManager()
        self.confirm = confirm or _confirm_always
        self.on_text = on_text
        self.store = store

        # Built once from the tools this provider actually exposes, and frozen
        # thereafter — a system prompt that changes between turns invalidates
        # the entire cached prefix on every request.
        self.system_prompt = build_system_prompt(self._available_tool_names())

        self.history: list[dict] = []
        self.last_usage = None
        self.spoke_during_last_turn = False

    def _available_tool_names(self) -> set[str]:
        """Tool names given to the model."""
        return {t["function"]["name"] for t in self.skills.openai_tool_definitions()}

    # -- public API --------------------------------------------------------

    def ask(self, user_text: str) -> str:
        """Run one full turn. self.history is left untouched if this raises."""
        self.spoke_during_last_turn = False

        before = len(self.history)
        working = list(self.history)
        working.append({"role": "user", "content": user_text})

        # If _run_turn raises, `working` is discarded and self.history keeps its
        # last known-good value. That is the whole point of this indirection.
        reply, working = self._run_turn(working)

        self.history = working
        self._persist(working[before:])
        return reply

    def load_history(self, limit: int = 20) -> int:
        """Restore the tail of the previous session. Returns messages loaded."""
        if self.store is None:
            return 0
        self.history = self.store.recent_messages(limit)
        return len(self.history)

    def reset(self) -> None:
        self.history = []
        if self.store is not None:
            self.store.clear_messages()

    # -- persistence -------------------------------------------------------

    def _persist(self, messages: list[dict]) -> None:
        """Write a completed turn to disk. Only called after the turn committed,
        so what lands on disk is always a well-formed conversation."""
        if self.store is None:
            return
        try:
            for msg in messages:
                self.store.append_message(msg["role"], msg["content"])
        except Exception as e:  # persistence must never break the assistant
            print(f"[store] couldn't save conversation: {e}")

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
        return self._call_openai_model(working)

    def _call_openai_model(self, working: list[dict]):
        messages = [{"role": "system", "content": self.system_prompt}]
        for msg in working:
            role = msg["role"]
            content = msg["content"]
            if isinstance(content, str):
                messages.append({"role": role, "content": content})
            elif isinstance(content, list):
                text_parts = []
                tool_calls_objs = []
                tool_results_objs = []
                for b in content:
                    if isinstance(b, dict):
                        b_type = b.get("type")
                        if b_type == "text":
                            text_parts.append(b.get("text", ""))
                        elif b_type == "tool_use":
                            tool_calls_objs.append({
                                "id": b.get("id", "call_1"),
                                "type": "function",
                                "function": {
                                    "name": b.get("name"),
                                    "arguments": json.dumps(b.get("input", {}))
                                }
                            })
                        elif b_type == "tool_result":
                            tool_results_objs.append(b)
                    elif getattr(b, "type", None) == "text":
                        text_parts.append(getattr(b, "text", ""))
                    elif getattr(b, "type", None) == "tool_use":
                        tool_calls_objs.append({
                            "id": getattr(b, "id", "call_1"),
                            "type": "function",
                            "function": {
                                "name": getattr(b, "name"),
                                "arguments": json.dumps(getattr(b, "input", {}))
                            }
                        })

                if tool_results_objs:
                    for tr in tool_results_objs:
                        text, images = _split_tool_result(tr.get("content", ""))
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tr.get("tool_use_id", ""),
                            "content": text
                        })
                        # The OpenAI schema has no place for an image inside a
                        # tool message, so images ride in a following user turn
                        # using the multimodal content-part format.
                        if images:
                            messages.append({"role": "user", "content": images})
                elif tool_calls_objs:
                    messages.append({
                        "role": "assistant",
                        "content": "\n".join(text_parts) if text_parts else None,
                        "tool_calls": tool_calls_objs
                    })
                elif text_parts:
                    messages.append({"role": role, "content": "\n".join(text_parts)})

        tools = self.skills.openai_tool_definitions()

        kwargs = {
            "model": config.MODEL,
            "messages": messages,
            "stream": True,
            "max_tokens": config.MAX_TOKENS,
        }
        if tools:
            kwargs["tools"] = tools

        response_stream = self.client.chat.completions.create(**kwargs)

        full_content = ""
        tool_calls_acc = {}
        finish_reason = None

        buffer = ""
        for chunk in response_stream:
            if not chunk.choices:
                continue
            choice = chunk.choices[0]
            if choice.finish_reason:
                finish_reason = choice.finish_reason
            delta = choice.delta
            if delta.content:
                full_content += delta.content
                buffer += delta.content
                buffer, done = _drain_sentences(buffer)
                for sentence in done:
                    self.spoke_during_last_turn = True
                    if self.on_text:
                        self.on_text(sentence)

            if delta.tool_calls:
                for tc in delta.tool_calls:
                    idx = tc.index
                    if idx not in tool_calls_acc:
                        tool_calls_acc[idx] = {"id": tc.id or f"call_{idx}", "name": "", "arguments": ""}
                    if tc.id:
                        tool_calls_acc[idx]["id"] = tc.id
                    if tc.function:
                        if tc.function.name:
                            tool_calls_acc[idx]["name"] += tc.function.name
                        if tc.function.arguments:
                            tool_calls_acc[idx]["arguments"] += tc.function.arguments

        if buffer.strip():
            self.spoke_during_last_turn = True
            if self.on_text:
                self.on_text(buffer.strip())

        content_blocks = []
        if full_content:
            content_blocks.append(SimpleNamespace(type="text", text=full_content))

        if tool_calls_acc:
            for idx in sorted(tool_calls_acc.keys()):
                tc = tool_calls_acc[idx]
                try:
                    args = json.loads(tc["arguments"]) if tc["arguments"] else {}
                except Exception:
                    args = {}
                content_blocks.append(SimpleNamespace(
                    type="tool_use",
                    id=tc["id"],
                    name=tc["name"],
                    input=args
                ))

        stop_reason = "tool_use" if tool_calls_acc else ("end_turn" if finish_reason in ("stop", None) else finish_reason)

        return SimpleNamespace(
            content=content_blocks,
            stop_reason=stop_reason,
            usage=SimpleNamespace(input_tokens=0, output_tokens=0)
        )

    def _run_tools(self, response) -> list[dict]:
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


def _split_tool_result(content) -> tuple[str, list[dict]]:
    """Convert an Anthropic tool_result payload for an OpenAI-shaped request.

    Returns (text for the tool message, image content-parts for a follow-up
    user message). Without this, a skill returning image blocks — see_screen —
    gets str()'d into a multi-hundred-KB base64 literal that is both useless to
    the model and ruinous to the context window.
    """
    if isinstance(content, str):
        return content, []
    if not isinstance(content, list):
        return str(content), []

    texts: list[str] = []
    images: list[dict] = []
    for block in content:
        if not isinstance(block, dict):
            texts.append(str(block))
            continue
        if block.get("type") == "text":
            texts.append(block.get("text", ""))
        elif block.get("type") == "image":
            source = block.get("source", {})
            if source.get("type") == "base64":
                media = source.get("media_type", "image/png")
                images.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{media};base64,{source.get('data', '')}"},
                    }
                )
            elif source.get("type") == "url":
                images.append({"type": "image_url", "image_url": {"url": source.get("url", "")}})
        else:
            texts.append(str(block))

    if images and not texts:
        texts.append("(image attached below)")
    return "\n".join(t for t in texts if t), images


def _tool_result(tool_use_id: str, content, is_error: bool) -> dict:
    block = {"type": "tool_result", "tool_use_id": tool_use_id, "content": content}
    if is_error:
        block["is_error"] = True
    return block


_SENTENCE_ENDS = ".!?\n"


def _drain_sentences(buffer: str) -> tuple[str, list[str]]:
    out = []
    start = 0
    for i, ch in enumerate(buffer):
        if ch not in _SENTENCE_ENDS:
            continue
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
    if isinstance(exc, openai.AuthenticationError):
        return "My API key was rejected. Please check your API key in the .env file."
    if isinstance(exc, openai.NotFoundError):
        return f"The model '{config.MODEL}' was not found. Check MODEL in .env."
    if isinstance(exc, openai.RateLimitError):
        return "I'm being rate limited. Give me a moment and try again."
    if isinstance(exc, openai.APIStatusError):
        if getattr(exc, "status_code", 0) >= 500:
            return "The API provider is having trouble. Try again in a moment."
        return f"The API rejected that request: {exc}"
    if isinstance(exc, openai.APIConnectionError):
        return "I can't reach the network right now."
    return f"Something went wrong: {exc}"
