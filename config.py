"""Central configuration, loaded from .env

Importing this module has no side effects beyond reading .env — call
`ensure_dirs()` explicitly before touching any path defined here.
"""
import os
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")

# --- Claude ---------------------------------------------------------------
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-opus-5")

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

# --- Paths ----------------------------------------------------------------
NOTES_FILE = os.path.join(DATA_DIR, "notes.txt")


def ensure_dirs() -> None:
    """Create the directories Jarvis writes to. Call once at startup."""
    os.makedirs(DATA_DIR, exist_ok=True)


def missing_key_message() -> str | None:
    """Return a user-facing message if the API key is absent, else None."""
    if ANTHROPIC_API_KEY:
        return None
    return (
        "ANTHROPIC_API_KEY is not set. Copy .env.example to .env and add your "
        "key from https://console.anthropic.com/"
    )
