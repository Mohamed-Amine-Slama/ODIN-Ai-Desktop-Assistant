"""Central configuration, loaded from .env

Importing this module has no side effects beyond reading .env — call
`ensure_dirs()` explicitly before touching any path defined here.
"""
import os
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")

# --- LLM Provider Settings (Gemini API) -----------------------------------
API_KEY = (
    os.getenv("GEMINI_API_KEY")
    or os.getenv("API_KEY", "")
)

BASE_URL = (
    os.getenv("GEMINI_BASE_URL")
    or os.getenv("BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai/")
)

MODEL = (
    os.getenv("GEMINI_MODEL")
    or os.getenv("MODEL", "gemini-3.6-flash")
)


# low | medium | high | xhigh | max. "low" keeps a voice assistant snappy:
# fewer, more consolidated tool calls and terser replies.
EFFORT = os.getenv("EFFORT", "low")

# Ceiling on thinking + response text combined, NOT a target length.
# Brevity comes from the system prompt.
MAX_TOKENS = int(os.getenv("MAX_TOKENS", "8192"))

# How many tool-use round trips before we give up on a single turn.
MAX_TOOL_ITERATIONS = int(os.getenv("MAX_TOOL_ITERATIONS", "10"))

# --- Behaviour ------------------------------------------------------------
ASSISTANT_NAME = os.getenv("ASSISTANT_NAME", "Jarvis")
DEFAULT_MODE = os.getenv("DEFAULT_MODE", "text")  # "voice" or "text"
CONFIRM_DESTRUCTIVE = os.getenv("CONFIRM_DESTRUCTIVE", "1") not in ("0", "false", "False")
DEBUG = os.getenv("DEBUG", "0") not in ("0", "false", "False")

# --- Voice ----------------------------------------------------------------
# Wake word. openWakeWord ships a pretrained "hey_jarvis" model, so this works
# with no API key and no training. Set WAKE_WORD=off for push-to-talk.
WAKE_WORD = os.getenv("WAKE_WORD", "hey_jarvis")
WAKE_THRESHOLD = float(os.getenv("WAKE_THRESHOLD", "0.5"))

# Speech-to-text: faster-whisper model size. base.en is a good CPU default;
# small.en is more accurate if your machine can take it.
STT_MODEL = os.getenv("STT_MODEL", "base.en")
STT_COMPUTE = os.getenv("STT_COMPUTE", "int8")

# Text-to-speech: "auto" prefers edge-tts (natural, free, no key) and falls
# back to the offline Windows SAPI voices. Use "sapi" to force offline, "off"
# to run silent.
TTS_ENGINE = os.getenv("TTS_ENGINE", "auto")
TTS_VOICE = os.getenv("TTS_VOICE", "en-GB-RyanNeural")
TTS_RATE = int(os.getenv("TTS_RATE", "180"))  # SAPI only

# Recording: stop after this much silence, and never record longer than max.
VAD_SILENCE_SECONDS = float(os.getenv("VAD_SILENCE_SECONDS", "0.8"))
VAD_MAX_SECONDS = float(os.getenv("VAD_MAX_SECONDS", "20"))

# --- Paths ----------------------------------------------------------------
NOTES_FILE = os.path.join(DATA_DIR, "notes.txt")


def ensure_dirs() -> None:
    """Create the directories Jarvis writes to. Call once at startup."""
    os.makedirs(DATA_DIR, exist_ok=True)


def missing_key_message() -> str | None:
    """Return a user-facing message if the API key is absent, else None."""
    if API_KEY:
        return None
    return (
        "API_KEY is not set. Copy .env.example to .env and add your "
        "API key."
    )
