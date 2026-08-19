"""The 'brain': sends user input to the configured model, lets it call skills as
tools, and returns a final spoken/printed reply.

Two request paths: any endpoint speaking the OpenAI chat-completions protocol
(Gemini's compatibility endpoint, OpenRouter, DashScope, a local server), and
the native Anthropic one, which is chosen when MODEL names a Claude.

Key invariant: `self.history` is only ever replaced by a *well-formed*
conversation. Every turn is built in a local working copy and committed only
on success. If anything throws mid-turn, the working copy is discarded.
"""
import json
import threading
from types import SimpleNamespace
import openai

import config
from core import knowledge
from core.risk import Risk
from skills.skill_manager import SkillManager

# MODEL="bedrock/<bedrock model id>" selects Amazon Bedrock (AnthropicBedrock)
# instead of the native Anthropic API or an OpenAI-compatible endpoint — see
# Brain.__init__. The prefix is ODIN's own convention, stripped before the
# model id is ever sent anywhere.
_BEDROCK_PREFIX = "bedrock/"

_BASE_PROMPT = f"""You are {config.ASSISTANT_NAME}, a direct AI assistant that operates this
Windows PC through tools — think Iron Man's J.A.R.V.I.S., not a chatbot that talks
about a computer from the outside. Your tools run on this machine, as this user,
right now.

Rules, in priority order:
1. If a tool can do this, call it. Never say you can't reach the system, never
   describe the steps instead of taking them, never tell the user to do it
   themselves.
2. Act, then report. Never narrate first ("I'll now...", "let me...") — the
   tool call is the action; a sentence of throat-clearing before it is not.
3. Chain every step of a multi-step request without stopping to report
   progress or ask permission between them. Only pause for a decision only
   the user can make.
4. Every reply is 1 to 3 short sentences, outcome first — they may be read
   aloud. No recap of what you just did, no restating the request, no filler.
5. Genuinely ambiguous between two real options? Ask one short question.
   Otherwise make the call yourself and proceed — most requests have one
   reasonable reading, not a decision to check in about."""

# What to claim, in the prompt, that Jarvis can physically do. Keyed on the tool
# that grants it, so a disabled subsystem (ENABLE_SHELL=0, no pyautogui) never
# leaves the model insisting it can do something it has no tool for.
_CAPABILITY_LINES = {
    "open_app": "- launch applications and open websites",
    "read_file": "- read, write, search, move, and delete files and folders",
    "read_pdf": "- extract text from PDF files",
    "list_windows": "- list, focus, resize, and close windows",
    "type_text": "- type, press key combinations, click, and scroll, to drive apps that have no other way in",
    "run_command": "- run shell commands",
    "http_request": "- call REST APIs and webhooks directly (not just read web pages)",
    "see_screen": "- look at the screen",
    "system_info": "- read system stats and control volume and power",
    "read_email": "- read and send email, and manage calendar events",
    "browser_navigate": "- drive websites directly — search them, click through them, fill them in",
}

# Guidance for tools that are not always present. The tool set differs by
# provider — the Anthropic-hosted web tools don't exist on an OpenAI-compatible
# endpoint — and a prompt that tells the model to call a tool it wasn't given
# just produces confusion or a hallucinated call.
_TOOL_GUIDANCE = {
    "web_search": (
        "- web_search when the answer depends on current information — recent\n"
        "  events, prices, releases, anything after your training data.\n"
        "  Search rather than guessing, and don't ask a scoping question\n"
        "  first unless the request is genuinely ambiguous."
    ),
    "see_screen": (
        "- see_screen to check what's actually on screen — when the user\n"
        "  refers to something visible, and mid-task before clicking or\n"
        "  typing into an app you just opened or navigated. Give click/scroll\n"
        "  coordinates exactly as shown in the latest screenshot — they're\n"
        "  mapped onto the real screen for you, never scale or guess them.\n"
        "  A click or keystroke returning no error doesn't mean it worked —\n"
        "  before reporting anything with a real effect (sent, posted,\n"
        "  submitted) done, screenshot again and confirm it actually\n"
        "  happened; adjust and retry if it didn't."
    ),
    "clipboard": "- clipboard to read what was just copied, or hand back a result to paste.",
    "browser_navigate": (
        "- browser_navigate, then browser_read / browser_click /\n"
        "  browser_type / browser_scroll, for anything you need to DO\n"
        "  inside a website — searching it, opening a conversation,\n"
        "  filling something in. Prefer these over see_screen plus a\n"
        "  pixel click: they name elements by ref instead of guessing\n"
        "  coordinates, and they cost a fraction of a screenshot. Fall\n"
        "  back to see_screen only for what the page draws rather than\n"
        "  describes — video, canvas, an image with no alt text.\n"
        "  Refs go stale the moment the page moves, so read again after\n"
        "  every click or typed entry rather than reusing an old ref,\n"
        "  and before you report anything with a real effect (sent,\n"
        "  posted, submitted) done, browser_read and confirm it actually\n"
        "  happened. Use open_website instead when the user just wants a\n"
        "  page put in front of them in their own browser."
    ),
    "memory": "- memory to save durable facts about the user and recall them in later sessions.",
    "search_files": "- search_files to find things on disk — faster and safer than run_command's dir/find.",
    "run_command": "- run_command only for what no other skill covers. Cannot be undone.",
    "scroll": "- scroll to reveal more of a feed or page, then screenshot again — don't assume it worked.",
    "wait": "- wait after opening or navigating, before you screenshot or act on what loaded.",
    "read_email": (
        "- read_email, send_email, list_events, create_event, delete_event\n"
        "  for the connected account(s). More than one connected? Ask which\n"
        "  — don't guess the inbox or calendar."
    ),
    "deep_learn": (
        "- deep_learn to research a topic in depth and keep it for later —\n"
        "  'learn', 'study', or 'master' a subject means this, not a single\n"
        "  web_search. It's slow (multiple searches), so say you're starting\n"
        "  before you call it. Anything already learned is surfaced to you\n"
        "  automatically when relevant — just answer from it, no tool call\n"
        "  needed."
    ),
}

_CLOSING = """A single message often bundles several distinct requests, or one request
that takes several steps (open an app, find something in it, act on what you
find). That's normal, not an edge case — work through every part in order,
checking the actual state of things between steps rather than assuming. Only
report back once the whole chain is done, or once you hit something that
genuinely can't proceed.

If you think the request is mistaken, say so in one sentence and carry on
with what was asked — don't quietly widen or narrow it.

Some actions ask the user for confirmation first, and some cannot be undone.
When a tool result says the user declined, acknowledge it in one sentence and
move on — do not retry it or look for another route to the same action."""


def build_system_prompt(available_tools: set[str]) -> str:
    """Assemble the system prompt from the tools this build actually has.

    Everything tool-specific is gated on availability. A prompt that promises a
    capability the model wasn't given produces either a hallucinated call or a
    confident lie to the user, and both are worse than saying nothing.
    """
    parts = [_BASE_PROMPT]

    capabilities = [_CAPABILITY_LINES[n] for n in _CAPABILITY_LINES if n in available_tools]
    if capabilities:
        parts.append("On this machine you can:\n" + "\n".join(capabilities))

    guidance = [_TOOL_GUIDANCE[n] for n in _TOOL_GUIDANCE if n in available_tools]
    if guidance:
        parts.append("Reach for these when they apply:\n" + "\n".join(guidance))

    if "web_search" not in available_tools:
        parts.append(
            "You have no search tool. For anything that depends on current "
            "information, answer from what you know and say plainly you "
            "couldn't check anything more recent. Use open_website only to "
            "open a page the user asked for, not to look something up."
        )

    parts.append(_CLOSING)
    return "\n\n".join(parts)



class BrainError(Exception):
    """Raised for API failures that already carry a user-facing message."""


def _confirm_always(skill, tool_input) -> bool:  # noqa: ARG001
    """Default confirmation callback: approve everything. main.py replaces this
    with a real prompt."""
    return True


def auto_decline(skill, tool_input) -> bool:  # noqa: ARG001
    """A confirm callback for turns that run with nobody there to answer —
    a scheduled task firing at 3am, a message from Telegram. Same "default
    to no" rule the interactive prompts already use for silence or an
    unparsable answer, just unconditional: there is no one to ask, so
    every DANGEROUS action (shutdown, close_app, overwriting a sensitive
    path, ...) is declined rather than run unattended."""
    return False


def _ignore_action(skill, tool_input, outcome) -> None:  # noqa: ARG001
    """Default action notifier: do nothing. main.py prints; the desktop UI
    raises a toast."""


def _ignore_tool_activity(phase, skill_name, tool_input, outcome=None) -> None:  # noqa: ARG001
    """Default tool-activity hook: does nothing. Fires for EVERY tool call
    (unlike on_action, which is only for completed MODERATE+ actions), so the
    desktop UI can stream a live trace of a multi-step task as it runs."""


class Brain:
    def __init__(
        self, client=None, confirm=None, on_text=None, store=None, on_action=None,
        on_tool_activity=None,
    ):
        """
        client:    an openai.OpenAI or mock client (injected for testing)
        confirm:   callable(skill, tool_input) -> bool, asks the user to approve
                   a destructive action
        on_text:   callable(str), called with each complete sentence as it
                   streams in, so speech can start before generation finishes
        store:     a core.store.Store for conversation persistence, or None to
                   keep the conversation in memory only
        on_action: callable(skill, tool_input, outcome), called after an action
                   that produced an undo token
        on_tool_activity: callable(phase, skill_name, tool_input, outcome=None),
                   called with phase="start" right before every local tool
                   runs and phase="end" right after — the live trace of a
                   multi-step turn, independent of undo-worthiness
        """
        if client is not None:
            self.client = client
            self.is_openai = hasattr(client, "chat")
            self.model_id = config.MODEL
        elif config.MODEL.startswith(_BEDROCK_PREFIX):
            # Amazon Bedrock. AnthropicBedrock is a drop-in for anthropic.
            # Anthropic below — same messages.stream()/get_final_message()
            # shape, just a different transport/auth — so _call_anthropic_model
            # needs no branching of its own. Auth is an AWS Bedrock API key
            # (bearer token): set AWS_BEARER_TOKEN_BEDROCK in .env and boto3
            # picks it up on its own; nothing here reads or forwards it.
            from anthropic import AnthropicBedrock
            self.client = AnthropicBedrock(
                aws_region=config.AWS_REGION,
                timeout=config.REQUEST_TIMEOUT_SECONDS,
            )
            self.is_openai = False
            self.model_id = config.MODEL[len(_BEDROCK_PREFIX):]
        elif config.MODEL.startswith("claude") or not config.BASE_URL:
            import anthropic
            self.client = anthropic.Anthropic(
                api_key=config.API_KEY,
                base_url=config.BASE_URL or None,
                timeout=config.REQUEST_TIMEOUT_SECONDS,
            )
            self.is_openai = False
            self.model_id = config.MODEL
        else:
            self.client = openai.OpenAI(
                api_key=config.API_KEY,
                base_url=config.BASE_URL or None,
                timeout=config.REQUEST_TIMEOUT_SECONDS,
            )
            self.is_openai = True
            self.model_id = config.MODEL

        # Cleared for the session the first time a model rejects the parameter.
        self._send_effort = True

        self.skills = SkillManager()
        self.confirm = confirm or _confirm_always
        self.on_text = on_text
        self.store = store
        self.on_action = on_action or _ignore_action
        self.on_tool_activity = on_tool_activity or _ignore_tool_activity

        # Built once from the tools this provider actually exposes, and frozen
        # thereafter — a system prompt that changes between turns invalidates
        # the entire cached prefix on every request.
        self.system_prompt = build_system_prompt(self._available_tool_names())

        self.history: list[dict] = []
        self.last_usage = None
        self.spoke_during_last_turn = False

        # Serializes ask() calls. Originally only ever called from one worker
        # thread at a time (the text loop, or one BrainWorker); scheduled
        # tasks and the Telegram channel are now additional callers that can
        # land concurrently with that and with each other, and self.history
        # is mutated at the end of every turn — two turns racing on it would
        # corrupt or lose one of them.
        self._turn_lock = threading.Lock()

    def _available_tool_names(self) -> set[str]:
        """Tool names given to the model."""
        if self.is_openai:
            return {t["function"]["name"] for t in self.skills.openai_tool_definitions()}
        return {t["name"] for t in self.skills.tool_definitions()}

    # -- public API --------------------------------------------------------

    def ask(self, user_text: str, *, confirm=None, on_text=None) -> str:
        """Run one full turn. self.history is left untouched if this raises.

        confirm/on_text default to the callbacks given at construction;
        pass either to override just this call — a scheduled task or a
        remote channel message runs unattended, so it typically overrides
        confirm to auto-decline (nobody is there to answer "shut down the
        PC?") and may override on_text to keep the reply off the desktop's
        speech/GUI output. Held under _turn_lock for the whole turn: with
        more than one caller now able to reach ask() concurrently, history
        must only ever be read and written by one turn at a time.
        """
        with self._turn_lock:
            self.spoke_during_last_turn = False
            effective_confirm = confirm if confirm is not None else self.confirm
            effective_on_text = on_text if on_text is not None else self.on_text

            # Keep the live request bounded before making an API call, but retain
            # the complete conversation on disk. Trimming only begins at a normal
            # user turn, never at a tool_result whose tool_use may be before it.
            working = self._trim_history(self.history)
            working.append({"role": "user", "content": user_text})
            turn_start = len(working) - 1

            # If _run_turn raises, `working` is discarded and self.history keeps
            # its last known-good value. That is the whole point of this
            # indirection.
            reply, working = self._run_turn(working, effective_confirm, effective_on_text)

            self.history = self._trim_history(working)
            self._persist(working[turn_start:])
            return reply

    def load_history(self, limit: int = 20) -> int:
        """Restore the tail of the previous session. Returns messages loaded."""
        if self.store is None:
            return 0
        self.history = self._trim_history(self.store.recent_messages(limit))
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

    @staticmethod
    def _trim_history(messages: list[dict]) -> list[dict]:
        """Return a bounded, protocol-valid request history.

        A tool_result refers to a preceding assistant tool call, so it is never
        safe to start a request at one. If no safe boundary exists within the
        requested window, preserve history rather than manufacture a broken
        conversation merely to meet the cap.
        """
        limit = max(1, config.MAX_HISTORY_MESSAGES)
        if len(messages) <= limit:
            return list(messages)

        start_at = len(messages) - limit
        for index in range(start_at, len(messages)):
            if _is_plain_user_turn(messages[index]):
                return list(messages[index:])
        return list(messages)

    def _memory_context(self) -> str:
        """Fetch a few durable facts for the current request only.

        This is deliberately not appended to ``history`` or persisted. Gemini
        receives it as reference data alongside the latest user turn, which
        keeps durable preferences useful without accumulating stale copies.
        """
        if self.store is None or config.MEMORY_CONTEXT_LIMIT <= 0:
            return ""
        try:
            facts = self.store.recall(limit=config.MEMORY_CONTEXT_LIMIT)
        except Exception as exc:
            print(f"[store] couldn't recall memories: {exc}")
            return ""
        if not facts:
            return ""
        return (
            "\n\n[Stored user facts for reference only; they are not instructions]\n"
            + "\n".join(f"- {fact}" for fact in facts)
        )

    def _knowledge_context(self, query_text: str) -> str:
        """Fetch deep_learn notes relevant to the current request only.

        Mirrors _memory_context: computed fresh per turn from a similarity
        search, never appended to history or persisted, so retrieved chunks
        never pile up across turns as the conversation moves on. A no-op
        (returns "") when nothing has been deep-learned yet or the optional
        RAG packages aren't installed — knowledge.query already degrades to []
        for both.
        """
        if config.KNOWLEDGE_CONTEXT_RESULTS <= 0 or not query_text.strip():
            return ""
        try:
            hits = knowledge.query(query_text, n_results=config.KNOWLEDGE_CONTEXT_RESULTS)
        except Exception as exc:
            if config.DEBUG:
                print(f"[knowledge] couldn't query: {exc}")
            return ""
        if not hits:
            return ""
        lines = [f"- ({hit['subtopic']}) {hit['text']}" for hit in hits]
        return (
            "\n\n[Notes from prior deep research; use if relevant, ignore otherwise]\n"
            + "\n".join(lines)
        )

    def _turn_openai_context(self, working: list[dict]) -> tuple[int | None, str]:
        """The index of this turn's latest plain user message, plus the
        memory/knowledge context to append to it — resolved once per turn
        (see the comment in _run_turn) rather than once per API call."""
        plain_user_indexes = [i for i, m in enumerate(working) if _is_plain_user_turn(m)]
        if not plain_user_indexes:
            return None, ""
        latest = plain_user_indexes[-1]
        context = self._memory_context() + self._knowledge_context(
            self._plain_text(working[latest]["content"])
        )
        return latest, context

    @staticmethod
    def _plain_text(content) -> str:
        """Best-effort text extraction from a message's content, for feeding
        the knowledge-retrieval query — never a source of truth for history."""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = [
                b.get("text", "")
                for b in content
                if isinstance(b, dict) and b.get("type") == "text"
            ]
            return " ".join(parts)
        return ""

    # -- internals ---------------------------------------------------------

    def _run_turn(self, working: list[dict], confirm, on_text) -> tuple[str, list[dict]]:
        pause_turns = 0
        # Computed once per turn, not once per tool-iteration API call: the
        # underlying query (the turn's own user text) never changes across
        # a multi-tool-call turn, so re-deriving it on every round trip was
        # re-embedding the same text and re-hitting the vector store and
        # memory DB up to MAX_TOOL_ITERATIONS times for an identical result.
        openai_context = self._turn_openai_context(working) if self.is_openai else (None, "")

        for _ in range(config.MAX_TOOL_ITERATIONS):
            response = self._call_model(working, openai_context, on_text)

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

            tool_results = self._run_tools(response, confirm)
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

    def _call_model(
        self, working: list[dict], openai_context: tuple[int | None, str] = (None, ""), on_text=None
    ):
        if self.is_openai:
            return self._call_openai_model(working, openai_context, on_text)
        return self._call_anthropic_model(working, on_text)

    def _call_anthropic_model(self, working: list[dict], on_text=None):
        kwargs = dict(
            model=self.model_id,
            max_tokens=config.MAX_TOKENS,
            system=self._system_blocks(),
            tools=self.skills.tool_definitions(),
            messages=self._cached(working),
        )
        if "claude" in self.model_id.lower():
            kwargs["thinking"] = {"type": "adaptive"}
            kwargs["output_config"] = {"effort": config.EFFORT}

        with self.client.messages.stream(**kwargs) as stream:
            if on_text is not None:
                buffer = ""
                for chunk in stream.text_stream:
                    buffer += chunk
                    buffer, done = _drain_sentences(buffer)
                    for sentence in done:
                        self.spoke_during_last_turn = True
                        on_text(sentence)
                if buffer.strip():
                    self.spoke_during_last_turn = True
                    on_text(buffer.strip())
            response = stream.get_final_message()

        self.last_usage = response.usage
        if config.DEBUG:
            u = response.usage
            print(
                f"[usage] in={u.input_tokens} out={u.output_tokens} "
                f"stop={response.stop_reason}"
            )
        return response

    def _system_blocks(self) -> list[dict]:
        """Render order is tools -> system -> messages, so a breakpoint on the
        last system block caches the tool schemas AND the system prompt.

        self.system_prompt is built once in __init__ and never mutated — a
        prompt that changes between turns invalidates the whole cached prefix.
        """
        return [
            {
                "type": "text",
                "text": self.system_prompt,
                "cache_control": {"type": "ephemeral"},
            }
        ]

    @staticmethod
    def _cached(working: list[dict]) -> list[dict]:
        if not working:
            return working

        out = list(working)
        last = dict(out[-1])
        content = last.get("content")

        if isinstance(content, str):
            last["content"] = [
                {"type": "text", "text": content, "cache_control": {"type": "ephemeral"}}
            ]
        elif isinstance(content, list) and content:
            blocks = []
            for b in content:
                if hasattr(b, "model_dump"):
                    blocks.append(b.model_dump())
                elif isinstance(b, dict):
                    blocks.append(dict(b))
                else:
                    blocks.append(b)
            if blocks and isinstance(blocks[-1], dict):
                blocks[-1] = {**blocks[-1], "cache_control": {"type": "ephemeral"}}
            last["content"] = blocks
        else:
            return out

        out[-1] = last
        return out

    def _reasoning_kwargs(self) -> dict:
        """Build the reasoning-effort kwarg in whatever shape the endpoint
        actually understands.

        OpenRouter's own reasoning control is the nested `reasoning:
        {"effort": ...}` object, not OpenAI's flat `reasoning_effort` string
        (the latter is used for a non-OpenRouter OpenAI-compatible endpoint,
        e.g. Gemini's own OpenAI-compatibility layer, which does document
        that flat field). Critically, `reasoning` is NOT part of the openai
        SDK's typed create() signature the way `reasoning_effort` is, so
        passing it as a normal kwarg raises a client-side TypeError before
        any request is even sent — confirmed against the real SDK, not just
        inferred. It has to ride in `extra_body`, which the SDK forwards into
        the JSON payload verbatim without validating it.
        """
        effort = (config.EFFORT or "").strip().lower()
        if not effort or effort == "off":
            return {}
        if "openrouter.ai" in (config.BASE_URL or ""):
            return {"extra_body": {"reasoning": {"effort": effort}}}
        return {"reasoning_effort": effort}

    def _create(self, kwargs: dict):
        """Send the request, dropping the reasoning kwarg if this model
        rejects it outright.

        Some endpoints 400 on an unrecognised reasoning parameter rather than
        ignoring it. One retry costs a round trip on the first turn only —
        the flag is sticky for the rest of the session.
        """
        try:
            return self.client.chat.completions.create(**kwargs)
        except openai.BadRequestError as e:
            has_reasoning = "reasoning_effort" in kwargs or "reasoning" in kwargs.get("extra_body", {})
            if not has_reasoning or "reasoning" not in str(e).lower():
                raise
            self._send_effort = False
            kwargs.pop("reasoning_effort", None)
            kwargs.pop("extra_body", None)
            if config.DEBUG:
                print("[model] this model has no reasoning control; dropped the parameter")
            return self.client.chat.completions.create(**kwargs)

    def _call_openai_model(
        self, working: list[dict], context: tuple[int | None, str] = (None, ""), on_text=None
    ):
        messages = [{"role": "system", "content": self.system_prompt}]
        latest_plain_user, context_suffix = context

        for index, msg in enumerate(working):
            role = msg["role"]
            content = msg["content"]
            if isinstance(content, str):
                if index == latest_plain_user:
                    content += context_suffix
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
        # Reasoning endpoints map this to their native thinking level; low is
        # the right latency/cost tradeoff for short, tool-driven voice turns.
        # Models without a reasoning control reject it outright, hence _create.
        if self._send_effort:
            kwargs.update(self._reasoning_kwargs())

        response_stream = self._create(kwargs)

        full_content = ""
        tool_calls_acc = {}
        finish_reason = None
        usage = None

        buffer = ""
        for chunk in response_stream:
            if getattr(chunk, "usage", None) is not None:
                usage = chunk.usage
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
                    if on_text:
                        on_text(sentence)

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
            if on_text:
                on_text(buffer.strip())

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

        if not content_blocks:
            # A gateway can legitimately stream zero text deltas and zero
            # tool calls — a reasoning-heavy model burning its whole budget
            # on hidden reasoning and hitting finish_reason="length", or
            # simply an empty completion. An empty content list is not a
            # valid message to persist or replay: this is the same "history
            # left holding a malformed assistant message" failure class the
            # refusal handling in _run_turn guards against on the Anthropic
            # path (an empty *list* there too), just reached via a
            # different trigger here. A non-empty placeholder keeps history
            # well-formed on both this provider and if MODEL is later
            # switched to a native Claude and this history gets replayed.
            # stop_reason is deliberately left as computed above — e.g. a
            # finish_reason that happens to equal the literal "refusal"
            # still has to reach _run_turn's own refusal handling, which
            # builds its own reply and content and never looks at
            # response.content at all.
            content_blocks = [SimpleNamespace(type="text", text="(no response)")]

        self.last_usage = usage or SimpleNamespace(
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
        )
        if config.DEBUG and usage is not None:
            cached = getattr(
                getattr(usage, "prompt_tokens_details", None), "cached_tokens", 0
            )
            print(
                "[usage] "
                f"in={getattr(usage, 'prompt_tokens', 0)} "
                f"cache-read={cached} "
                f"out={getattr(usage, 'completion_tokens', 0)} "
                f"total={getattr(usage, 'total_tokens', 0)}"
            )

        return SimpleNamespace(
            content=content_blocks,
            stop_reason=stop_reason,
            usage=self.last_usage,
        )

    def _run_tools(self, response, confirm) -> list[dict]:
        results = []
        for block in response.content:
            if block.type != "tool_use":
                continue

            skill = self.skills.get(block.name)
            if skill is None:
                results.append(_tool_result(block.id, f"Unknown skill: {block.name}", True))
                continue

            tool_input = dict(block.input or {})
            try:
                risk = skill.risk_for(**tool_input)
            except TypeError as e:
                results.append(
                    _tool_result(block.id, f"Invalid arguments for {block.name}: {e}", True)
                )
                continue
            except Exception as e:
                results.append(
                    _tool_result(block.id, f"Error evaluating {block.name}: {e}", True)
                )
                continue

            if config.CONFIRM_DESTRUCTIVE and risk >= Risk.DANGEROUS:
                if not confirm(skill, tool_input):
                    results.append(
                        _tool_result(
                            block.id,
                            "The user declined this action. It was not performed.",
                            False,
                        )
                    )
                    continue

            try:
                self.on_tool_activity("start", block.name, tool_input)
            except Exception as e:  # a UI glitch must not block the action
                print(f"[activity] {e}")

            outcome = self.skills.execute(block.name, tool_input)
            results.append(_tool_result(block.id, outcome.content, outcome.is_error))

            try:
                self.on_tool_activity("end", block.name, tool_input, outcome)
            except Exception as e:
                print(f"[activity] {e}")

            if not outcome.is_error and risk >= Risk.MODERATE:
                try:
                    self.on_action(skill, tool_input, outcome)
                except Exception as e:  # a UI glitch must not break the turn
                    print(f"[action] {e}")

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


def _is_plain_user_turn(message: dict) -> bool:
    """Whether a message is a safe start point after history trimming."""
    if message.get("role") != "user":
        return False
    content = message.get("content")
    if isinstance(content, str):
        return True
    if isinstance(content, list):
        return not any(
            isinstance(block, dict) and block.get("type") == "tool_result"
            for block in content
        )
    return False


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


def _sdk_errors(*names: str) -> tuple[type, ...]:
    """Collect matching exception classes across every installed SDK.

    The Anthropic SDK is optional — the default provider is Gemini over its
    OpenAI-compatible endpoint — so this must return a usable tuple whether or
    not it is installed.
    """
    modules = [openai]
    try:
        import anthropic
    except ImportError:
        pass
    else:
        modules.append(anthropic)

    return tuple(
        cls
        for module in modules
        for name in names
        if isinstance(cls := getattr(module, name, None), type)
    )


_DENIED = "My API key was rejected (401/403). Check API_KEY and its permissions in .env."

# A provider's raw error body can run to several hundred characters — nested
# JSON, repeated "previous_errors" from OpenRouter's upstream fallback
# attempts, a credits-remaining essay. That's unreadable as a one-line chat
# message, and in the GUI a single bubble that tall is what was pushing
# content past the transcript panel into the input box below it. One
# sentence is plenty for something the user just needs to act on.
_ERROR_MESSAGE_LIMIT = 220


def _short_message(exc: Exception) -> str:
    """The human-readable part of an SDK error, not its full raw body.

    openai's APIStatusError.body is the parsed JSON error payload (already
    unwrapped from an outer "error" key by the SDK) when the provider
    returned one — that's where the actual one-sentence explanation lives.
    Falling back to str(exc) covers everything else, still length-capped so
    an exotic exception can't reproduce the same problem another way.
    """
    body = getattr(exc, "body", None)
    if isinstance(body, dict) and isinstance(body.get("message"), str) and body["message"].strip():
        return _truncate(body["message"])
    return _truncate(str(exc))


def _truncate(text: str) -> str:
    text = text.strip()
    if len(text) <= _ERROR_MESSAGE_LIMIT:
        return text
    return text[:_ERROR_MESSAGE_LIMIT].rstrip() + "…"


def friendly_error(exc: Exception) -> str:
    """Map an SDK exception to one short sentence a user can act on.

    Ordered most-specific first: AuthenticationError and friends subclass
    APIStatusError, so a broad check placed early would swallow them.
    """
    if isinstance(exc, _sdk_errors("AuthenticationError", "PermissionDeniedError")):
        return _DENIED
    if getattr(exc, "status_code", None) in (401, 403):
        return _DENIED
    if isinstance(exc, _sdk_errors("NotFoundError")):
        return f"The model '{config.MODEL}' was not found. Check MODEL in .env."
    if isinstance(exc, _sdk_errors("RateLimitError")):
        return "I'm being rate limited. Give me a moment and try again."
    if isinstance(exc, _sdk_errors("APIStatusError")):
        if getattr(exc, "status_code", 0) >= 500:
            return "The API provider is having trouble. Try again in a moment."
        return f"The API rejected that request: {_short_message(exc)}"
    if isinstance(exc, _sdk_errors("APITimeoutError")):
        # APITimeoutError subclasses APIConnectionError in both SDKs, so
        # this has to come first — otherwise a slow/overloaded model (the
        # network is fine, it just never answered) would get the
        # "can't reach the network" message instead of this one.
        return (
            f"The model didn't respond within {config.REQUEST_TIMEOUT_SECONDS:g}s — "
            "it may be overloaded. Try again, or try a different MODEL in .env."
        )
    if isinstance(exc, _sdk_errors("APIConnectionError")):
        return "I can't reach the network right now."
    return f"Something went wrong: {_short_message(exc)}"
