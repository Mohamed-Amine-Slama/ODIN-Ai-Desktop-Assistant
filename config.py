"""Central configuration, loaded from .env

Importing this module has no side effects beyond reading .env — call
`ensure_dirs()` explicitly before touching any path defined here.
"""
import os
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")

# --- LLM Provider Settings ------------------------------------------------
API_KEY = (
    os.getenv("API_KEY")
    or os.getenv("DASHSCOPE_API_KEY")
    or os.getenv("OPENAI_API_KEY")
    or os.getenv("OPENROUTER_API_KEY")
    or os.getenv("ANTHROPIC_API_KEY", "")
)

BASE_URL = (
    os.getenv("BASE_URL")
    or os.getenv("DASHSCOPE_BASE_URL")
    or os.getenv("OPENAI_BASE_URL")
    or os.getenv("OPENROUTER_BASE_URL", "")
)

MODEL = (
    os.getenv("MODEL")
    or os.getenv("DASHSCOPE_MODEL")
    or os.getenv("OPENAI_MODEL")
    or os.getenv("OPENROUTER_MODEL")
    or os.getenv("CLAUDE_MODEL", "qwen-max")
)

_provider_env = os.getenv("LLM_PROVIDER", "").lower()
if _provider_env in ("openai", "dashscope", "alibaba", "qwen"):
    LLM_PROVIDER = "openai"
elif _provider_env in ("anthropic", "claude"):
    LLM_PROVIDER = "anthropic"
elif "compatible-mode" in BASE_URL or "aliyuncs.com" in BASE_URL or os.getenv("DASHSCOPE_API_KEY") or os.getenv("OPENAI_API_KEY"):
    LLM_PROVIDER = "openai"
elif "openrouter.ai" in BASE_URL and os.getenv("OPENROUTER_API_KEY"):
    LLM_PROVIDER = "anthropic"
else:
    LLM_PROVIDER = "openai" if BASE_URL else "anthropic"

# Backward compatibility aliases
OPENROUTER_API_KEY = API_KEY
ANTHROPIC_API_KEY = API_KEY
OPENROUTER_BASE_URL = BASE_URL
CLAUDE_MODEL = MODEL

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
