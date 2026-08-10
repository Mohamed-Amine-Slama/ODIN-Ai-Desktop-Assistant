# Jarvis — Personal AI Desktop Assistant (Windows)

A modular Jarvis-style assistant: talk or type to it, and it uses Claude to
understand you and calls real "skills" to control your PC — opening apps,
checking system stats, web search, weather, volume, power controls, notes,
reminders, and a calculator. Built to be extended indefinitely.

## 1. Install

Requires **Python 3.10+** on Windows.

```bat
cd jarvis
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

Notes on tricky Windows installs:
- **PyAudio**: if `pip install pyaudio` fails, install the prebuilt wheel:
  `pip install pipwin && pipwin install pyaudio`
- **pycaw / comtypes** (volume control): pure Python, should install cleanly.
- **pywin32**: after installing, you may need to run
  `python venv\Scripts\pywin32_postinstall.py -install` once.

## 2. Configure

```bat
copy .env.example .env
```

Edit `.env` and set `ANTHROPIC_API_KEY` to your key from
https://console.anthropic.com/. Optionally change `DEFAULT_MODE` to `text`
if you'd rather not use voice at first.

## 3. Run

```bat
python main.py
```

- **Text mode**: just type and hit Enter.
- **Voice mode**: press Enter, then speak when you see "Listening...".
  (True hands-free "Hey Jarvis" wake-word detection isn't included yet —
  see Roadmap below for how to add it.)

Commands you can type any time: `/mode voice`, `/mode text`, `/reset`, `/quit`

## 4. Project layout

```
jarvis/
  main.py               orchestrates the voice/text loop
  config.py              loads .env settings
  core/
    brain.py             talks to Claude, runs the tool-use loop
    speech_input.py       microphone -> text
    speech_output.py      text -> speech (offline, Windows SAPI voices)
  skills/
    base_skill.py         base class every skill implements
    skill_manager.py       registers skills, exposes them to Claude as tools
    system_skills.py       open/close apps, volume, power, system info
    web_skills.py           web search, open website, weather
    utility_skills.py       time/date, notes, reminders, calculator
```

## 5. Adding a new skill (this is the whole point — build it incrementally)

1. Open (or create) a file in `skills/`.
2. Subclass `BaseSkill`:

```python
from .base_skill import BaseSkill

class MyNewSkill(BaseSkill):
    name = "my_new_skill"
    description = "One sentence Claude uses to decide when to call this."
    input_schema = {
        "type": "object",
        "properties": {"param": {"type": "string"}},
        "required": ["param"],
    }

    def run(self, param: str) -> str:
        # do the thing
        return "Result to speak back to the user."
```

3. Register it in `skills/skill_manager.py` (import + add to `SKILL_CLASSES`).

That's it — no changes needed anywhere else. Claude will automatically start
calling it whenever a user's request matches the description.

## 6. Roadmap ideas (pick what you want next)

- **Wake word ("Hey Jarvis")**: add [Porcupine](https://github.com/Picovoice/porcupine)
  or `openwakeword` for always-listening hands-free activation instead of
  press-Enter-to-talk.
- **Better STT**: swap `speech_recognition`'s Google backend for local
  **Whisper** (`openai-whisper` or `faster-whisper`) for offline, more
  accurate recognition.
- **Nicer TTS**: swap `pyttsx3` for `edge-tts` (free, much more natural
  voices, still no API key) or ElevenLabs for premium voice cloning.
- **GUI / overlay**: wrap it in a simple system-tray app or a transparent
  always-on-top HUD (PyQt/customtkinter).
- **Smart home**: add skills that hit your Home Assistant / Philips Hue /
  Govee APIs.
- **Screen awareness**: add a skill that screenshots and sends the image to
  Claude so it can answer "what's on my screen?" or help debug an error.
- **Email/calendar**: add skills using Microsoft Graph API or Google APIs.
- **Persistent memory**: currently conversation history resets when you
  restart the app (`/reset` also clears it) — swap in a simple SQLite log if
  you want it to remember things across sessions.

## Notes on scope

"Every feature Jarvis has" (from Iron Man) isn't a real spec — real building
blocks are: speech in/out, an LLM brain, and a growing set of tool "skills."
This gives you that foundation working end-to-end today, structured so you
can bolt on anything above without touching the core loop.
