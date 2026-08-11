"""Central configuration, loaded from .env

Importing this module has no side effects beyond reading .env — call
`ensure_dirs()` explicitly before touching any path defined here.
"""
import os
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")

# --- LLM provider settings ------------------------------------------------
# Anything that speaks the OpenAI chat-completions protocol works here: Gemini's
# compatibility endpoint, OpenRouter, DashScope/Qwen, a local llama.cpp server.
# API_KEY / BASE_URL / MODEL are the real names; the provider-prefixed ones are
# accepted so an existing .env keeps working after a provider switch.
def _first_env(*names: str, default: str = "") -> str:
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    return default


API_KEY = _first_env(
    "API_KEY", "GEMINI_API_KEY", "OPENROUTER_API_KEY", "OPENAI_API_KEY", "DASHSCOPE_API_KEY"
)
BASE_URL = _first_env(
    "BASE_URL", "GEMINI_BASE_URL", "OPENROUTER_BASE_URL", "OPENAI_BASE_URL", "DASHSCOPE_BASE_URL",
    default="https://generativelanguage.googleapis.com/v1beta/openai/",
)
MODEL = _first_env(
    "MODEL", "GEMINI_MODEL", "OPENROUTER_MODEL", "OPENAI_MODEL", "DASHSCOPE_MODEL",
    default="gemini-3.6-flash",
)


# low | medium | high, or "off" for models that have no reasoning control.
# "low" keeps a voice assistant snappy: fewer, more consolidated tool calls and
# terser replies. Sending it to a model that doesn't take it is handled at the
# request layer, which drops the parameter and retries once.
EFFORT = os.getenv("EFFORT", "low")

# Ceiling on thinking + response text combined, NOT a target length.
# Brevity comes from the system prompt.
MAX_TOKENS = int(os.getenv("MAX_TOKENS", "8192"))

# How many tool-use round trips before we give up on a single turn. A compound
# request ("open X, find Y, message them") can easily chain a dozen-plus calls
# — open/navigate, several wait+see_screen+click cycles, type, send — so this
# needs real headroom, not just enough for a couple of simple lookups.
MAX_TOOL_ITERATIONS = int(os.getenv("MAX_TOOL_ITERATIONS", "25"))

# Bound active conversation context in long-running sessions. Persistent
# history remains in SQLite; only the request working set is trimmed.
MAX_HISTORY_MESSAGES = int(os.getenv("MAX_HISTORY_MESSAGES", "80"))
MEMORY_CONTEXT_LIMIT = int(os.getenv("MEMORY_CONTEXT_LIMIT", "5"))

# How many deep_learn notes chunks to surface per turn. 0 disables retrieval
# without needing chromadb/sentence-transformers installed at all.
KNOWLEDGE_CONTEXT_RESULTS = int(os.getenv("KNOWLEDGE_CONTEXT_RESULTS", "4"))

# --- Behaviour ------------------------------------------------------------
ASSISTANT_NAME = os.getenv("ASSISTANT_NAME", "ODIN")
DEFAULT_MODE = os.getenv("DEFAULT_MODE", "text")  # "voice" or "text"
CONFIRM_DESTRUCTIVE = os.getenv("CONFIRM_DESTRUCTIVE", "1") not in ("0", "false", "False")
DEBUG = os.getenv("DEBUG", "0") not in ("0", "false", "False")

# --- System access --------------------------------------------------------
# Global hotkey that summons the HUD. Needs the optional `keyboard` package;
# without it the orb and the tray icon are still there. Set to "off" to skip.
HUD_HOTKEY = os.getenv("HUD_HOTKEY", "ctrl+alt+j")

ENABLE_SHELL = os.getenv("ENABLE_SHELL", "1") not in ("0", "false", "False")
ENABLE_INPUT_CONTROL = os.getenv("ENABLE_INPUT_CONTROL", "1") not in ("0", "false", "False")
UNDO_WINDOW_SECONDS = float(os.getenv("UNDO_WINDOW_SECONDS", "900"))

# HUD only: how long a confirmation banner waits before it defaults to "no".
# Text mode blocks on input() and cannot honour a timeout, so this has no
# effect there.
CONFIRM_TIMEOUT_SECONDS = float(os.getenv("CONFIRM_TIMEOUT_SECONDS", "120"))
TRASH_MAX_ENTRIES = int(os.getenv("TRASH_MAX_ENTRIES", "200"))
TRASH_MAX_AGE_DAYS = float(os.getenv("TRASH_MAX_AGE_DAYS", "7"))

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
