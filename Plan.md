# Jarvis — Review & Upgrade Plan

## Context

You built a Windows desktop AI assistant last night: speech in/out, a Claude-backed
"brain" that runs a tool-use loop, and a pluggable skill registry (12 skills across
system / web / utility). ~660 lines. The architecture is genuinely good — `BaseSkill`
→ `SkillManager` → `Brain` is the right decomposition, and adding a skill really is a
subclass plus one list entry.

The problems are in the details, and they fall into four buckets:

1. **A latent brain-killer.** `core/brain.py` has no error handling. If the API call
   throws mid-tool-loop (rate limit, dropped wifi, bad key), `self.history` is left
   holding an `assistant` message with a `tool_use` block and no matching
   `tool_result`. Every subsequent request then 400s — permanently, until `/reset`.
   The 5-iteration bailout at line 57 leaves the same corrupt state.
2. **Unconfirmed destructive actions.** `power_control` shuts the machine down with no
   prompt, driven by Google speech-to-text that mishears routinely. `close_app` matches
   process names by *substring*, so `close_app("s")` terminates everything with an "s"
   in its name.
3. **Outdated API usage.** Pinned to `claude-sonnet-4-6` with no thinking, no effort, no
   streaming, and no prompt caching — you re-pay full input price for the system prompt
   plus 12 tool schemas on every single turn. `web_search` opens a browser tab instead
   of returning results, so Jarvis can't actually *answer* from the web.
4. **Nothing survives a restart.** Conversation, notes, and reminders all live in memory
   or a flat text file; reminders are daemon `threading.Timer`s that vanish on exit.

Intended outcome: a Jarvis that is hands-free ("Hey Jarvis" wake word, local Whisper,
natural voice), can see your screen and search the live web, remembers across sessions,
asks before doing anything destructive, and cannot corrupt its own conversation state.

Delivered in **four stages**, each independently runnable so you can catch a bad mic or
a missing dependency before the next layer lands.

---

## Ground rules

- **Run from Windows Python, not WSL.** `C:\Python312\python.exe`. `os.startfile`,
  `pycaw`, and the audio devices don't exist under WSL. The repo lives on a `/mnt/c`
  path, which is fine — but launch it from a Windows shell.
- Core modules get `sys.platform` guards so they still *import* on Linux; that's what
  makes the test suite runnable in WSL. Windows-only skills degrade to a clear error
  message instead of an ImportError at startup.
- Model target is `claude-opus-5` throughout.

---

## Stage 1 — Core hardening + API modernization

The stage that makes everything else safe to build on. Files: `core/brain.py`,
`config.py`, `skills/base_skill.py`, `skills/skill_manager.py`, `main.py`.

### 1a. Transactional conversation history (`core/brain.py`)

This is the fix for the brain-killer. Restructure `ask()` so `self.history` is only
mutated on a clean turn:

- Snapshot `self.history` at entry; build the turn in a local `working` list.
- On success, `self.history = working`.
- On **any** exception, discard `working` — history is untouched and the next request
  still works.
- On the iteration cap (raise 5 → 10), close the turn properly by appending a synthetic
  assistant text message, so history never ends on a dangling `tool_use`.

Add the typed exception chain, most-specific first (`shared/error-codes.md` ordering):
`NotFoundError` → `RateLimitError` → `APIStatusError` → `APIConnectionError`. Each maps
to a short spoken message rather than a traceback.

### 1b. Model and inference config

In `config.py` and `.env.example`:

```
CLAUDE_MODEL=claude-opus-5
EFFORT=low
```

In the request:

- `thinking={"type": "adaptive"}` with `output_config={"effort": "low"}`. Low effort is
  the right default for a voice assistant — fewer, more consolidated tool calls, less
  preamble, terser confirmations.
- **Do not set `thinking: {"type": "disabled"}`.** On Opus 5 that has two documented
  failure modes, and one is fatal here: the model occasionally writes a tool call into
  its *visible text* instead of emitting a `tool_use` block. The turn succeeds, the tool
  silently never runs, and no error is raised. For a tool-driven assistant that's a
  silent-failure generator. Adaptive thinking at `effort: low` is both cheaper and safer.
- Raise `max_tokens` from 1024 → 8192. Thinking is on by default on Opus 5 and
  `max_tokens` caps thinking *plus* response text together — 1024 will truncate replies
  mid-sentence. This is a ceiling, not a target; brevity comes from the system prompt.

### 1c. Streaming + sentence-chunked speech

Switch to `client.beta.messages.stream(...)` and read the final message with
`stream.get_final_message()`. Two wins:

- Timeout protection on long turns.
- Feed `stream.text_stream` into a sentence buffer and flush **complete sentences** to
  the speaker as they arrive, so Jarvis starts talking before the full reply lands. This
  is the single biggest perceived-latency improvement in the whole plan. The
  `SpeechOutput` interface grows a queue + playback thread (`core/speech_output.py`) that
  Stage 4 reuses unchanged.

### 1d. Prompt caching

Render order is `tools` → `system` → `messages`, so one breakpoint on the last system
block caches the tool schemas *and* the system prompt together. Opus 5's minimum
cacheable prefix is 512 tokens; 12 tool schemas plus the system prompt clears that
comfortably.

- `system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}]`
- Second breakpoint on the last content block of the newest turn, so multi-turn
  conversations accrue hits incrementally.
- **Keep the system prompt byte-frozen.** No `datetime.now()`, no session ID — anything
  volatile at the front invalidates the whole prefix.

For the volatile context an assistant genuinely needs (current time, active window,
recalled memories), append a `{"role": "system", "content": "..."}` message to
`messages[]` instead. Opus 5 supports mid-conversation system messages with no beta
header; it sits *after* the cached history so the prefix survives. Constraint to respect:
it must follow a user message and be the last entry, and can never be `messages[0]`.

Verify with `usage.cache_read_input_tokens` — log it behind a `DEBUG` flag. If it's zero
across repeated turns, something in the prefix is changing.

### 1e. Skill manager correctness (`skills/skill_manager.py`)

- Fix the double instantiation at line 36. `{cls().name: cls()}` builds two instances of
  every skill and throws one away:
  ```python
  self.skills = {}
  for cls in SKILL_CLASSES:
      skill = cls()
      self.skills[skill.name] = skill
  ```
- Return `is_error: True` on tool results when a skill raises, so the model can tell
  failure from success and adapt instead of reporting the error string as an answer.
  `execute()` returns `(content, is_error)`; `Brain` builds the `tool_result` block.
- Keep the existing correct behavior of returning **all** `tool_result` blocks in a
  **single** user message — splitting them across messages trains the model out of
  parallel tool calls. The current code already does this right.

### 1f. Confirmation gate for destructive actions

`skills/base_skill.py` gains:

```python
requires_confirmation: bool = False

def confirmation_prompt(self, **kwargs) -> str:
    """Human-readable description of what's about to happen."""
```

`Brain` takes an injected `confirm: Callable[[BaseSkill, dict], bool]` — confirmation
needs user I/O, so it belongs at the loop level, not inside `SkillManager`. `main.py`
supplies a console y/n prompt in text mode; Stage 4 swaps in a spoken
"Should I go ahead?" + yes/no listen. A declined call returns a normal `tool_result`
saying the user declined, so the model can respond gracefully rather than hanging.

Marked as requiring confirmation:
- `PowerControlSkill` for `shutdown` and `restart` only — `lock` and `sleep` are
  harmless and reversible, so gating them would just be annoying.
- `CloseAppSkill` always.

Two `CloseAppSkill` bug fixes ship alongside the gate (these are defects, not policy —
flagging since you selected the plain confirmation option):
- Replace the substring match with exact / `.exe`-stem matching, so `"s"` can't match
  every process on the machine.
- A small denylist for critical Windows processes (`csrss`, `wininit`, `winlogon`,
  `services`, `lsass`, `smss`, `system`) that refuses rather than terminating them.

Also fix `_safe_eval` in `skills/utility_skills.py:99`: bound `ast.Pow` operands so
`2**999999999` can't hang the assistant.

### 1g. `main.py` and `config.py` cleanup

- Fix the `/mode voice` lie at `main.py:37`. Today it prints "Switched to voice mode"
  but if `listener` is `None` the loop silently keeps reading typed input. Make mode
  switching lazily initialize the listener and report honestly if the mic is
  unavailable. Replace the `globals()["MODE"]` write at line 64 with proper state.
- Unknown `/commands` currently do nothing silently — print the command list.
- Move `os.makedirs` out of `config.py` import time into an explicit `ensure_dirs()`
  called from `main()`. Same for the API-key warning print.
- Add `.gitignore`: `.env`, `data/`, `__pycache__/`, `venv/`, model caches.
- Trim `requirements.txt` — `keyboard` and `pywin32` are declared but never imported.

### 1h. System prompt tuning for Opus 5

Opus 5 writes longer responses by default and expands task scope more than earlier
models. Keep your "1-3 sentences" instruction (it's already right) and add a
scope-discipline line: deliver what was asked at the scope intended, flag concerns in a
sentence rather than silently widening the task. Do **not** add "double-check your work"
style instructions — Opus 5 self-verifies already, and telling it to causes
over-verification with no accuracy gain.

### 1i. Tests

New `tests/` with `pytest`. Chosen so they run in WSL against a mocked Anthropic client:

- History rollback: inject an exception mid-tool-loop, assert `brain.history` is
  byte-identical to its pre-turn state and that a following `ask()` succeeds.
- Iteration cap leaves history well-formed (no trailing `tool_use`).
- `SkillManager` builds exactly one instance per skill class.
- Tool errors surface with `is_error: True`.
- Confirmation gate: declining `power_control` does not invoke `os.system`.
- `_safe_eval` rejects oversized exponents and non-arithmetic expressions.

**Stage 1 is done when:** `python main.py` in text mode holds a multi-turn conversation,
survives a forced API error without corrupting itself, refuses to shut down without
confirmation, and logs a non-zero `cache_read_input_tokens` on turn 2.

---

## Stage 2 — New capabilities

### 2a. Real web access (`skills/web_skills.py`)

Replace the browser-opening `WebSearchSkill` with Anthropic's server-side tools, added
directly to the `tools` array in `SkillManager.tool_definitions()`:

```python
{"type": "web_search_20260209", "name": "web_search"}
{"type": "web_fetch_20260209",  "name": "web_fetch"}
```

These execute on Anthropic's side and return results inline — Jarvis actually *answers*
from the web instead of opening a tab. Three consequences to handle:

- **`SkillManager.execute()` must never see them.** Server tool results arrive as
  content blocks, not `tool_use` requests. Route by checking the tool name against the
  local skill registry.
- **`Brain` must handle `stop_reason == "pause_turn"`.** Server-tool loops hit an
  iteration limit and pause; you re-send the messages to resume (no "Continue." message
  — the API detects the trailing `server_tool_use` block). Cap resumes at 5.
- **Do not also declare `code_execution`.** The `_20260209` variants run dynamic
  filtering with code execution under the hood; a second execution environment confuses
  the model.

Keep `OpenWebsiteSkill` — "open YouTube" is still a legitimately useful local action.
Keep `WeatherSkill` (wttr.in works fine and is cheaper than a web search).

### 2b. Screen vision (`skills/vision_skills.py`)

A `see_screen` skill that captures the display and returns it to the model as an image.
This requires widening `BaseSkill.run()`'s return type from `str` to
`str | list[dict]` — `tool_result` content accepts image content blocks, which is the
clean way to hand a screenshot back:

```python
[{"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": ...}}]
```

- Capture with `mss` (fast, multi-monitor aware).
- Downscale to ~1280px on the long edge before encoding. Opus 5 accepts up to 2576px,
  but a full-resolution screenshot costs up to ~4784 tokens; 1280px is plenty for
  "what's this error on my screen?" and roughly a third the cost.
- Optional `region` parameter (`full` / `active_window`).

### 2c. Clipboard (`skills/utility_skills.py`)

`read_clipboard` / `write_clipboard` via `pyperclip`. Small, and it makes the screen
skill much more useful ("copy that error message").

**Stage 2 is done when:** "What's the latest on X?" returns a cited answer without
opening a browser, and "What's on my screen?" describes it correctly.

---

## Stage 3 — Persistence

New `core/store.py` wrapping SQLite at `data/jarvis.db`. Tables:

| Table | Purpose |
|---|---|
| `messages` | Conversation turns (`ts`, `role`, `content_json`) — survives restart |
| `notes` | Replaces `data/notes.txt`; migrate the file on first run if present |
| `reminders` | `fire_at`, `message`, `fired` — survives restart |
| `memories` | Durable facts Jarvis is told to remember |

- **Reminders become real.** `core/scheduler.py` runs a background thread polling
  `reminders` every ~15s, fires desktop notifications via `plyer`, and **fires overdue
  reminders on startup**. This replaces the daemon `threading.Timer` in
  `ReminderSkill.run()` (`skills/utility_skills.py:77`), which dies with the process and
  loses the reminder silently.
- **`NoteSkill` repoints** at the `notes` table. Keep the same tool schema so nothing
  else changes.
- **New `remember` / `recall` skills** over the `memories` table. Recall stays simple —
  most-recent-N plus a `LIKE` filter, no embeddings. The top few memories get injected
  via the mid-conversation system message from Stage 1d, so they cost nothing in cache
  invalidation.
- **Conversation restore.** On startup, load the last N turns from `messages` so
  "what were we talking about?" works across restarts. `/reset` clears both memory and
  the table.

### Context growth

With persistence, history grows without bound. Handle it server-side rather than
hand-rolling a trimmer that might split a `tool_use`/`tool_result` pair:

- `context_management={"edits": [{"type": "clear_tool_uses_20250919"}]}` with beta header
  `context-management-2025-06-27` on `client.beta.messages.*`. This clears stale tool
  results — exactly the bloat a home assistant accumulates from repeated `system_info` and
  `get_time_date` calls.
- Belt-and-braces cap on top: if the working history exceeds N messages, trim from the
  front **only at a safe boundary** — an index where the message is a plain-text `user`
  turn, never a `tool_result` carrier.

**Stage 3 is done when:** a reminder set before you kill the process still fires after
restart, and Jarvis recalls a fact from a previous session.

---

## Stage 4 — Voice pipeline

Heaviest install; isolated in `requirements-voice.txt` so the core still runs without the
ML stack. Files: `core/speech_input.py`, `core/speech_output.py`, new `core/wake.py`.

### 4a. Wake word — `core/wake.py`

`openwakeword` ships a **pre-trained "hey jarvis" model** (`hey_jarvis_v0.1.onnx`) —
free, ONNX, no API key, no Picovoice account. A capture thread feeds a ring buffer;
detection above threshold triggers the listen phase.

### 4b. Speech-to-text — `core/speech_input.py`

Replace `speech_recognition.recognize_google` (internet-dependent, mediocre, sends your
audio to Google) with `faster-whisper`:

- `base.en` default, `small.en` if your machine handles it. `compute_type="int8"` on CPU.
- Capture via `sounddevice` + `numpy` rather than `PyAudio` — dramatically less painful
  to install on Windows (no prebuilt-wheel dance, which your README currently warns
  about).
- Simple energy-based VAD to stop recording on silence, replacing the fixed
  `phrase_time_limit=15`.

### 4c. Text-to-speech — `core/speech_output.py`

Replace `pyttsx3` (robotic SAPI5, and `runAndWait()` blocks the whole loop) with
`edge-tts`: free, no key, genuinely natural voices. `en-GB-RyanNeural` gets you close to
the Iron Man butler register.

Slots into the queue + playback thread built in Stage 1c, so streamed sentences start
playing while the model is still generating. Playback via `pygame.mixer`.

### 4d. Loop restructure — `main.py`

Replace press-Enter-to-talk with a proper state machine:

```
IDLE ──wake word──► LISTENING ──silence──► THINKING ──stream──► SPEAKING ──► IDLE
```

- `/mode text` still works and stays the fallback when no mic is present.
- The confirmation gate from Stage 1f gets its spoken form here: Jarvis asks
  "Should I shut down the PC?" and listens for a yes/no.
- Graceful degradation: if wake-word or Whisper model loading fails, log clearly and
  fall back to text mode rather than crashing.

**Stage 4 is done when:** saying "Hey Jarvis, what's my CPU usage?" across the room gets
a spoken answer, and "Hey Jarvis, shut down the PC" gets a spoken confirmation request
first.

---

## Files touched

| File | Stage | Change |
|---|---|---|
| `core/brain.py` | 1, 2, 3 | Rewrite: transactional history, streaming, caching, errors, `pause_turn`, `refusal`, confirmation hook, context management |
| `config.py` | 1 | Opus 5, effort, voice/feature settings, no import-time side effects |
| `skills/base_skill.py` | 1, 2 | `requires_confirmation`, widened `run()` return type |
| `skills/skill_manager.py` | 1, 2 | Single instantiation, `is_error`, server-tool routing |
| `skills/system_skills.py` | 1 | Confirmation flags, exact process matching, denylist |
| `skills/utility_skills.py` | 1, 2, 3 | `_safe_eval` bound, clipboard, notes/reminders → SQLite |
| `skills/web_skills.py` | 2 | Server-side `web_search` / `web_fetch` |
| `skills/vision_skills.py` | 2 | **New** — screenshot |
| `core/store.py` | 3 | **New** — SQLite |
| `core/scheduler.py` | 3 | **New** — durable reminders |
| `core/speech_input.py` | 4 | faster-whisper + sounddevice |
| `core/speech_output.py` | 1, 4 | Queue/thread (S1), edge-tts (S4) |
| `core/wake.py` | 4 | **New** — openWakeWord |
| `main.py` | 1, 4 | Mode handling, confirmation UI, voice state machine |
| `tests/` | 1+ | **New** — pytest |
| `requirements*.txt`, `.env.example`, `.gitignore`, `README.md` | all | Updated |

---

## Verification

Run everything from **Windows** Python (`C:\Python312\python.exe`), except the test
suite which also runs in WSL.

**Stage 1**
```bat
python -m pytest tests/ -v
python main.py
```
- Multi-turn text conversation works.
- Pull the network mid-reply → clear spoken error, and the *next* message still works
  (this is the regression test for the history-corruption bug).
- "Shut down the PC" → confirmation prompt; answering no does nothing.
- "Close app s" → refuses / asks, does not mass-terminate.
- With `DEBUG=1`, turn 2 logs a non-zero `cache_read_input_tokens`.

**Stage 2**
- "What's the latest news about Anthropic?" → cited answer, no browser tab.
- "What's on my screen right now?" → accurate description.
- "Read my clipboard" → returns clipboard contents.

**Stage 3**
```bat
python main.py    REM "Remind me in 2 minutes to stretch", then Ctrl-C
python main.py    REM restart before the 2 minutes elapse
```
- Notification still fires. `data/jarvis.db` contains the reminder row.
- "Remember that my monitor is a Dell U2720Q" → new session → "What monitor do I have?"

**Stage 4**
```bat
pip install -r requirements-voice.txt
python main.py
```
- "Hey Jarvis" from across the room wakes it; the reply is spoken in a natural voice
  and starts before the full response is generated.
- "Hey Jarvis, shut down the PC" → spoken confirmation request first.
- Unplug the mic → falls back to text mode with a clear message instead of crashing.