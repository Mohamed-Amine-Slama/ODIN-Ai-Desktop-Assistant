# Jarvis — Personal AI Desktop Assistant (Windows)

A modular Jarvis-style assistant: talk or type to it, and it uses an LLM to
understand you and calls real "skills" to control your PC — opening apps,
checking system stats, reading your screen, searching the web, volume, power
controls, notes, reminders, durable memory, and deep research it remembers
permanently.

A single message can bundle several steps or several distinct asks — "open
Instagram in Opera GX, find so-and-so in my DMs, and message them" — and
Jarvis works through the whole chain with as many tool calls as it takes,
looking at the screen between steps rather than guessing.

It runs as a desktop HUD — an always-on-top arc reactor orb that sits on your
desktop, and a full-screen glass interface you summon from it — with a live
trace of every tool call as a multi-step task runs, plus panels for the
skills registry and the knowledge base — or as a plain terminal loop when
you'd rather not have a window.

Any endpoint speaking the OpenAI chat-completions protocol works (Gemini's
compatibility endpoint, OpenRouter, DashScope/Qwen, a local server), plus the
native Anthropic API when `MODEL` names a Claude.

## 1. Install

Requires **Python 3.10+** on Windows. Run from a Windows shell, not WSL —
`os.startfile`, `pycaw`, and the audio devices don't exist there.

```bat
cd Jarvis
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

That one file covers everything, including the heavier optional stacks below
— Jarvis still runs fine at runtime if any of them are missing, it just
degrades that one feature instead of failing to start. Notably:

- Hands-free voice pulls ~1GB of ML wheels (`faster-whisper`, ...).
- The `deep_learn` skill (research a topic in depth and remember it
  permanently) and semantic recall for the `memory` skill both need a local
  vector store and embedding model — `sentence-transformers` pulls in torch.
  Without it, `deep_learn` just says what's missing instead of failing, and
  `memory` recall falls back to plain-text search; nothing else in Jarvis is
  affected.
- Email and calendar (`read_email`, `send_email`, `list_events`,
  `create_event`, `delete_event`) still need one Google or Microsoft account
  connected before they do anything — see section 9 below.

`web_search` needs no setup at all — it runs on DuckDuckGo via the `ddgs`
package (already in `requirements.txt`), with no API key, signup, or billing.

## 2. Configure

```bat
copy .env.example .env
```

Edit `.env` and point it at a provider. Three keys do all the work:

```ini
API_KEY=your_key_here
BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/
MODEL=gemini-3.6-flash
```

Swap in OpenRouter (`https://openrouter.ai/api/v1`), DashScope, or anything
else that speaks the same protocol. Leave `BASE_URL` blank and set
`MODEL=claude-opus-5` to use Anthropic directly, which additionally gets
adaptive thinking, prompt caching, and server-side web search.

Reasoning depth is `EFFORT`. A model with no reasoning control rejects the
parameter; Jarvis notices on the first turn and drops it for the session, so
you don't have to know in advance which models support it.

## 3. Run

```bat
python app.py        REM desktop HUD
python main.py       REM terminal only
```

The HUD opens full-screen and leaves an orb on your desktop when you press
**Esc**. Click the orb, double-click the tray icon, or press **Ctrl+Alt+J** to
bring it back. The orb shows what Jarvis is doing: its particle swarm holds a
tight ring when idle and scatters while it's working or speaking.

Voice is the main way to talk to the HUD: on launch, Jarvis speaks a greeting
and listens right away for your first request — no wake phrase needed yet,
since it just finished talking. After each request it goes back to sleep;
say your assistant's name and **"wake up"** to bring it back for the next one.

- **Text mode**: type and hit Enter.
- **Voice mode**: `/mode voice`, then just say your assistant's name and
  **"wake up"** (e.g. **"ODIN, wake up"**) — no key press. Falls back to
  press-Enter-to-talk if the speech models aren't available.

Commands: `/undo`, `/mode voice`, `/mode text`, `/reset`, `/connect google`,
`/connect microsoft`, `/help`, `/quit`

In voice mode, talking over Jarvis mid-reply cuts it off and is heard as the
next thing you said — no need to wait for it to finish. This has no acoustic
echo cancellation, so it works best with headphones; over open speakers,
Jarvis's own voice coming back through the mic can occasionally trigger it
(raise `BARGE_IN_THRESHOLD` if that happens).

In the HUD, the **KNOWLEDGE** button opens a panel to browse what's been
deep-learned and kick off research on a new topic in the background; **SETTINGS**
shows every registered skill and the behaviour toggles that are safe to flip
without restarting (shell/input-control changes still need a restart). While a
turn is running, each tool call streams into the chat feed as it starts and
resolves — visible progress through a multi-step task instead of a silent
spinner — and the orb pulses a distinct color while it's actively driving the
machine versus just thinking.

## 4. Project layout

```
Jarvis/
  app.py                  desktop entry point: orb + HUD
  main.py                 terminal run loop, voice state machine, confirmations
  config.py               loads .env settings
  ui/
    orb.py                the arc reactor: rings, core, reactive particle swarm
    app_window.py         the full-screen HUD and the desktop orb window
    panels.py             settings/skills dialog and the knowledge browser
    workers.py            Qt <-> brain threading seam
  core/
    brain.py              talks to the LLM, runs the tool-use loop
    risk.py               SAFE / MODERATE / DANGEROUS, shell command classifier
    undo.py               undo journal and the trash behind file recovery
    store.py              SQLite: conversation, notes, reminders, memories, knowledge manifest
    knowledge.py           local vector store for deep_learn (chunk, embed, retrieve)
    embeddings.py          shared sentence-embedding model (knowledge.py + memory_index.py)
    memory_index.py        semantic search index behind the memory skill's recall
    research.py            deep_learn's agentic pipeline: decompose, research, self-check
    email_providers.py     Gmail/Calendar and Outlook/Calendar OAuth + API backends
    env_file.py            .env read/update helper for the settings panel
    scheduler.py          durable reminders (survive restarts)
    audio.py              shared 16 kHz microphone stream
    wake.py               "<name>, wake up" wake trigger (faster-whisper phrase match)
    barge_in.py            interrupt Jarvis mid-sentence by talking over it
    speech_input.py       microphone -> text (faster-whisper, local)
    speech_output.py      text -> speech (edge-tts, SAPI fallback)
  skills/
    base_skill.py         base class every skill implements
    skill_manager.py      registers skills, exposes them as tools
    system_skills.py      open/close apps, volume, power, system info
    file_skills.py        read, list, search, write, move, delete
    shell_skills.py       run_command (arbitrary shell)
    window_skills.py      list, focus, minimise/maximise, close windows
    input_skills.py       type text, press keys, click, scroll
    web_skills.py         open website (optionally in a specific browser), browser search, fetch a page, weather, web_search
    vision_skills.py      screenshot -> the model can see your screen
    knowledge_skills.py    deep_learn, list_learned_topics
    email_skills.py        read_email, send_email, list_events, create_event, delete_event
    utility_skills.py     time/date, notes, reminders, memory, clipboard, calculator, wait
  tests/                  pytest suite (runs without voice, RAG, or email deps, and on Linux)
```

## 5. Safety

Jarvis uses a three-tier risk model (`SAFE`, `MODERATE`, `DANGEROUS`):
- **SAFE** actions (reading files, listing windows, system info, weather) run silently.
- **MODERATE** actions (writing files, moving files, focusing windows, typing) run immediately and generate a single-use undo token (`/undo`).
- **DANGEROUS** actions (`power_control` shutdown/restart, `close_app`, `delete_file`, overwriting sensitive paths) ask for explicit user confirmation first. Spoken confirmation defaults to NO on ambiguity or silence.

Deleted and overwritten files are backed up to Jarvis's local trash first, making file operations recoverable via `/undo`.

`close_app` matches process names exactly and refuses to touch critical
Windows processes (`lsass`, `csrss`, `winlogon`, …).

## 6. Adding a new skill

1. Subclass `BaseSkill` in a file under `skills/`:

```python
from .base_skill import BaseSkill
from core.risk import Risk

class MyNewSkill(BaseSkill):
    name = "my_new_skill"
    description = "One sentence the model uses to decide when to call this."
    input_schema = {
        "type": "object",
        "properties": {"param": {"type": "string"}},
        "required": ["param"],
    }
    risk = Risk.SAFE

    def run(self, param: str) -> str:
        return "Result to speak back to the user."
```

2. Register it in `skills/skill_manager.py` (import + add to `SKILL_CLASSES`).

That's it. A skill can also return image content blocks instead of a string —
see `vision_skills.py` — and they're converted correctly for both providers.

## 7. Tests

```bat
python -m pytest tests/ -v
```

343 tests, no API key and no microphone needed. They also run on Linux/WSL,
which is useful because the voice stack won't. The HUD tests use Qt's offscreen
platform, so they need no display either.

## 8. Configuration reference

| Variable | Default | What it does |
|---|---|---|
| `API_KEY` / `BASE_URL` / `MODEL` | — | The provider. Provider-prefixed aliases (`GEMINI_*`, `OPENROUTER_*`, …) also work |
| `EFFORT` | `low` | Reasoning depth, or `off`. Dropped automatically if the model rejects it |
| `MAX_TOKENS` | `8192` | Ceiling on thinking + reply, not a target length |
| `MAX_TOOL_ITERATIONS` | `25` | Tool round-trips before giving up on a turn — a compound, multi-step request can easily need a dozen-plus |
| `ENABLE_SHELL` | `1` | Master switch for `run_command` |
| `ENABLE_INPUT_CONTROL` | `1` | Master switch for typing/clicking |
| `MAX_HISTORY_MESSAGES` | `80` | Live request context cap. Full history stays in SQLite |
| `MEMORY_CONTEXT_LIMIT` | `5` | Durable facts injected alongside the newest turn |
| `KNOWLEDGE_CONTEXT_RESULTS` | `4` | deep_learn notes chunks injected per turn when relevant. `0` disables retrieval |
| `HUD_HOTKEY` | `ctrl+alt+j` | Global summon key (needs the `keyboard` package). `off` to disable |
| `CONFIRM_TIMEOUT_SECONDS` | `120` | HUD only: an unanswered confirmation counts as no |
| `MS_OAUTH_CLIENT_ID` | — | Enables the Microsoft-backed email/calendar skills once connected. See section 9 |
| `MS_OAUTH_TENANT_ID` | `common` | Azure tenant for the Microsoft OAuth app; `common` covers both personal and work/school accounts |
| `UNDO_WINDOW_SECONDS` | `900` | How long an action stays undoable |
| `TRASH_MAX_ENTRIES` | `200` | Deleted-file backups kept, by count |
| `TRASH_MAX_AGE_DAYS` | `7` | Deleted-file backups kept, by age |
| `WAKE_WORD` | `on` | Say `ASSISTANT_NAME` + "wake up" to wake Jarvis. Set `off` for push-to-talk |
| `STT_MODEL` | `base.en` | Whisper size. `small.en` is better if your CPU allows |
| `TTS_ENGINE` | `auto` | `edge` (natural, needs net), `sapi` (offline), `off` |
| `TTS_VOICE` | `en-GB-RyanNeural` | Any edge-tts voice |
| `VAD_SILENCE_SECONDS` | `0.8` | Silence before Jarvis stops recording |
| `BARGE_IN_THRESHOLD` | `0.05` | RMS level that counts as "the user is talking over Jarvis." No echo cancellation — raise this if playback through open speakers triggers it |
| `DEBUG` | `0` | Print token usage and cache hit rates |

## 9. Email and calendar setup

`read_email`, `send_email`, `list_events`, `create_event`, and `delete_event`
need one connected account (the packages are already in `requirements.txt`).
Neither provider is registered as a skill at all until it's at least set up
enough to attempt a connection — see `skill_manager.py`'s gating.

**Google** (Gmail + Calendar):
1. In [console.cloud.google.com](https://console.cloud.google.com), create an
   OAuth client ID of type **Desktop app**, and enable the Gmail and Calendar
   APIs for the project.
2. Download the client JSON and save it as `data/oauth/google_credentials.json`.
3. Run Jarvis and type `/connect google` — a browser window opens for consent.
4. Restart Jarvis. The Google-backed skills are now available.

**Microsoft** (Outlook + Calendar, via Graph):
1. In [portal.azure.com](https://portal.azure.com), register an app as a
   **public client** (no secret), and add the `Mail.Read`, `Mail.Send`, and
   `Calendars.ReadWrite` delegated permissions.
2. Set `MS_OAUTH_CLIENT_ID` in `.env` to the app's Application (client) ID.
3. Run Jarvis and type `/connect microsoft` — it prints a URL and a code to
   enter in a browser (device-code flow, so no redirect URI to configure).
4. Restart Jarvis. The Microsoft-backed skills are now available.

Both accounts can be connected at once — Jarvis asks which one to use for a
given request only when it's genuinely ambiguous. Tokens live under
`data/oauth/`, which is already covered by `.gitignore`.

## 10. Roadmap ideas

- **Smart home**: skills hitting Home Assistant / Hue / Govee APIs.
- **Scheduled re-learning**: periodically re-run `deep_learn` on stored
  topics so fast-moving subjects don't go stale.
- **Scheduled re-learning**: periodically re-run `deep_learn` on stored
  topics so fast-moving subjects don't go stale.
