# Jarvis — Personal AI Desktop Assistant (Windows)

A modular Jarvis-style assistant: talk or type to it, and it uses an LLM to
understand you and calls real "skills" to control your PC — opening apps,
checking system stats, reading your screen, searching the web, volume, power
controls, notes, reminders, and durable memory.

Works with **Anthropic (Claude)** or any **OpenAI-compatible endpoint**
(Alibaba Cloud DashScope / Qwen, OpenRouter, OpenAI itself).

## 1. Install

Requires **Python 3.10+** on Windows. Run from a Windows shell, not WSL —
`os.startfile`, `pycaw`, and the audio devices don't exist there.

```bat
cd Jarvis
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

Hands-free voice is optional and pulls ~1GB of ML wheels:

```bat
pip install -r requirements-voice.txt
```

## 2. Configure

```bat
copy .env.example .env
```

Edit `.env` and set your provider. The two common setups:

```ini
# Alibaba Cloud DashScope / Qwen
DASHSCOPE_API_KEY=sk-...
DASHSCOPE_BASE_URL=https://dashscope-intl.aliyuncs.com/compatible-mode/v1
DASHSCOPE_MODEL=qwen-max
LLM_PROVIDER=openai
```

```ini
# Anthropic
ANTHROPIC_API_KEY=sk-ant-...
CLAUDE_MODEL=claude-opus-5
LLM_PROVIDER=anthropic
```

**Provider differences worth knowing:** web search and web fetch run on
Anthropic's servers, so they're only available on the Anthropic path. On an
OpenAI-compatible endpoint Jarvis knows it has no web access and says so
instead of guessing. Prompt caching and adaptive thinking are likewise
Claude-only. Everything else — all local skills, vision, memory, voice — works
identically on both.

## 3. Run

```bat
python main.py
```

- **Text mode**: type and hit Enter.
- **Voice mode**: `/mode voice`, then just say **"Hey Jarvis"** — no key press.
  Falls back to press-Enter-to-talk if the wake-word model isn't available.

Commands: `/mode voice`, `/mode text`, `/reset`, `/help`, `/quit`

## 4. Project layout

```
Jarvis/
  main.py                 run loop, voice state machine, confirmation prompts
  config.py               loads .env settings
  core/
    brain.py              talks to the LLM, runs the tool-use loop
    store.py              SQLite: conversation, notes, reminders, memories
    scheduler.py          durable reminders (survive restarts)
    audio.py              shared 16 kHz microphone stream
    wake.py               "Hey Jarvis" wake word (openWakeWord)
    speech_input.py       microphone -> text (faster-whisper, local)
    speech_output.py      text -> speech (edge-tts, SAPI fallback)
  skills/
    base_skill.py         base class every skill implements
    skill_manager.py      registers skills, exposes them as tools
    system_skills.py      open/close apps, volume, power, system info
    web_skills.py         open website, browser search, weather
    vision_skills.py      screenshot -> the model can see your screen
    utility_skills.py     time/date, notes, reminders, memory, clipboard, calculator
  tests/                  pytest suite (runs without voice deps, and on Linux)
```

## 5. Safety

Destructive actions ask first. `power_control` (shutdown/restart) and
`close_app` require confirmation before they run — spoken in voice mode,
y/N at the prompt in text mode. **Anything other than a clear yes is treated
as no**, because speech-to-text mishears and a misrecognised "shut down"
should not power off your machine. `lock` and `sleep` aren't gated; they're
reversible.

`close_app` matches process names exactly and refuses to touch critical
Windows processes (`lsass`, `csrss`, `winlogon`, …). It used to substring-match,
which meant `close_app("s")` would terminate every process with an "s" in its
name.

Set `CONFIRM_DESTRUCTIVE=0` in `.env` to disable the gate. Don't.

## 6. Adding a new skill

1. Subclass `BaseSkill` in a file under `skills/`:

```python
from .base_skill import BaseSkill

class MyNewSkill(BaseSkill):
    name = "my_new_skill"
    description = "One sentence the model uses to decide when to call this."
    input_schema = {
        "type": "object",
        "properties": {"param": {"type": "string"}},
        "required": ["param"],
    }

    # Optional: gate it behind a confirmation prompt
    requires_confirmation = False

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

104 tests, no API key and no microphone needed. They also run on Linux/WSL,
which is useful because the voice stack won't.

## 8. Configuration reference

| Variable | Default | What it does |
|---|---|---|
| `EFFORT` | `low` | Reasoning depth (Claude only). `low` keeps voice snappy |
| `MAX_TOKENS` | `8192` | Ceiling on thinking + reply, not a target length |
| `MAX_TOOL_ITERATIONS` | `10` | Tool round-trips before giving up on a turn |
| `CONFIRM_DESTRUCTIVE` | `1` | Ask before shutdown / restart / killing processes |
| `WAKE_WORD` | `hey_jarvis` | Set `off` for push-to-talk |
| `WAKE_THRESHOLD` | `0.5` | Lower = more sensitive, more false wakes |
| `STT_MODEL` | `base.en` | Whisper size. `small.en` is better if your CPU allows |
| `TTS_ENGINE` | `auto` | `edge` (natural, needs net), `sapi` (offline), `off` |
| `TTS_VOICE` | `en-GB-RyanNeural` | Any edge-tts voice |
| `VAD_SILENCE_SECONDS` | `0.8` | Silence before Jarvis stops recording |
| `DEBUG` | `0` | Print token usage and cache hit rates |

## 9. Roadmap ideas

- **GUI / overlay**: system-tray app or a transparent always-on-top HUD.
- **Smart home**: skills hitting Home Assistant / Hue / Govee APIs.
- **Email/calendar**: Microsoft Graph or Google APIs.
- **Barge-in**: interrupt Jarvis mid-sentence by speaking over it.
- **Semantic memory**: swap the `LIKE` recall for embeddings once the
  memories table gets large.
