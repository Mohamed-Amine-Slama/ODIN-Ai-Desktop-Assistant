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

# How long to wait for the model before giving up on a request. Without
# this, the SDK's own default (minutes) applies — and a slow/overloaded
# provider (free-tier models in particular) can leave a turn hanging with
# no error and no way to send another message until it resolves, since
# only one turn is ever allowed in flight at a time.
REQUEST_TIMEOUT_SECONDS = float(os.getenv("REQUEST_TIMEOUT_SECONDS", "60"))

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

# Hand-gesture cursor control (core/gesture.py). Off by default, unlike the
# other ENABLE_* flags above — this is new and activates a webcam, so it's
# opt-in rather than opt-out.
ENABLE_GESTURE_CONTROL = os.getenv("ENABLE_GESTURE_CONTROL", "0") not in ("0", "false", "False")
GESTURE_CAMERA_INDEX = int(os.getenv("GESTURE_CAMERA_INDEX", "0"))
GESTURE_FPS_LIMIT = int(os.getenv("GESTURE_FPS_LIMIT", "30"))
GESTURE_SMOOTHING = float(os.getenv("GESTURE_SMOOTHING", "0.5"))
GESTURE_CLICK_HOLD_MS = float(os.getenv("GESTURE_CLICK_HOLD_MS", "250"))

# HUD only: how long a confirmation banner waits before it defaults to "no".
# Text mode blocks on input() and cannot honour a timeout, so this has no
# effect there.
CONFIRM_TIMEOUT_SECONDS = float(os.getenv("CONFIRM_TIMEOUT_SECONDS", "120"))
TRASH_MAX_ENTRIES = int(os.getenv("TRASH_MAX_ENTRIES", "200"))
TRASH_MAX_AGE_DAYS = float(os.getenv("TRASH_MAX_AGE_DAYS", "7"))

# --- Instrument HUD (ODIN-HUD.md) ------------------------------------------
HUD_TELEMETRY_INTERVAL_MS = int(os.getenv("HUD_TELEMETRY_INTERVAL_MS", "1000"))
HUD_DISK_POLL_SECONDS = float(os.getenv("HUD_DISK_POLL_SECONDS", "15"))
# "" lets wttr.in auto-locate from the requesting IP instead of a named city.
WEATHER_CITY = os.getenv("WEATHER_CITY", "")
HUD_WEATHER_POLL_SECONDS = float(os.getenv("HUD_WEATHER_POLL_SECONDS", "600"))
# loopback | mic | off — see ui/hud/spectrum.py for the fallback chain when
# the chosen source isn't actually available.
HUD_SPECTRUM_SOURCE = os.getenv("HUD_SPECTRUM_SOURCE", "loopback")
HUD_REDUCED_MOTION = os.getenv("HUD_REDUCED_MOTION", "0") not in ("0", "false", "False")

# --- Voice ----------------------------------------------------------------
# Wake trigger: say ASSISTANT_NAME and "wake up" to bring Jarvis back from
# sleep. Works with any name, no API key, no training (see core/wake.py).
# Set WAKE_WORD=off for push-to-talk instead.
WAKE_WORD = os.getenv("WAKE_WORD", "on")

# Speech-to-text: faster-whisper model size. base.en is a good CPU default;
# small.en is more accurate if your machine can take it.
STT_MODEL = os.getenv("STT_MODEL", "base.en")

# Which device runs the speech model, and at what numeric precision. "auto"
# for both (the default for each) lets CTranslate2 pick the fastest
# combination this machine actually supports — an NVIDIA GPU with float16 (or
# better) when one's usable, CPU int8 otherwise. This is what makes STT fast
# rather than a source of lag. Force STT_DEVICE=cuda only to troubleshoot: a
# request for a GPU that isn't actually usable then raises a clear error at
# startup instead of silently and slowly falling back to CPU.
STT_DEVICE = os.getenv("STT_DEVICE", "auto")
STT_COMPUTE = os.getenv("STT_COMPUTE", "auto")

# Text-to-speech: "auto" prefers edge-tts (natural, free, no key) and falls
# back to the offline Windows SAPI voices. Use "sapi" to force offline, "off"
# to run silent.
TTS_ENGINE = os.getenv("TTS_ENGINE", "auto")
TTS_VOICE = os.getenv("TTS_VOICE", "en-GB-RyanNeural")
TTS_RATE = int(os.getenv("TTS_RATE", "180"))  # SAPI only

# Recording: stop after this much silence, and never record longer than max.
VAD_SILENCE_SECONDS = float(os.getenv("VAD_SILENCE_SECONDS", "0.8"))
VAD_MAX_SECONDS = float(os.getenv("VAD_MAX_SECONDS", "20"))

# Barge-in: interrupt Jarvis mid-sentence by talking over it. Same RMS-energy
# scale as the VAD floor in speech_input (typical speech clears ~0.02-0.05);
# lower catches interruptions faster but risks tripping on room noise. This
# has no acoustic echo cancellation, so it works best with headphones —
# without them, Jarvis's own voice coming back through the mic from the
# speakers can trigger a false interruption.
BARGE_IN_THRESHOLD = float(os.getenv("BARGE_IN_THRESHOLD", "0.05"))

# --- Security ---------------------------------------------------------------
# Scans file reads, shell command output, and web fetches for things that look
# like API keys/passwords/tokens before they reach the model, get spoken, or
# get persisted to the conversation log. off | warn (log only, pass through) |
# redact (mask matches, default) | block (withhold the result entirely).
SECURITY_SCAN_MODE = os.getenv("SECURITY_SCAN_MODE", "redact").strip().lower()

# Token-bucket throttle on web_fetch/http_request/get_news (core/rate_limit.py)
# — same limit no matter who triggered the call, so a runaway or
# prompt-injected loop can't hammer a target unattended. 0 disables it.
RATE_LIMIT_PER_MINUTE = int(os.getenv("RATE_LIMIT_PER_MINUTE", "30"))
RATE_LIMIT_BURST = int(os.getenv("RATE_LIMIT_BURST", "10"))

# --- Remote channels ---------------------------------------------------------
# Telegram bridge (core/telegram_channel.py): text the assistant from your
# phone and get replies back. Off unless a bot token is set. TELEGRAM_CHAT_ID
# locks the bot to one chat — see that module's docstring for why this matters.
# Actions requiring confirmation are always auto-declined over this channel;
# there's nobody there to answer.
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# Discord bridge (core/discord_channel.py): same shape as Telegram above, just
# on Discord's Gateway instead of a long-poll REST API. Off unless a bot token
# is set; DISCORD_CHANNEL_ID locks it to one channel the same way
# TELEGRAM_CHAT_ID does. Needs the optional 'discord.py' package.
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "")
DISCORD_CHANNEL_ID = os.getenv("DISCORD_CHANNEL_ID", "")

# --- News / RSS -------------------------------------------------------------
# get_news skill: comma-separated feed URLs used when the request doesn't name
# a specific feed. Optional — well-known names (see skills/web_skills.py's
# NewsSkill.KNOWN) and explicit feed URLs work without this being set.
RSS_FEEDS = [u.strip() for u in os.getenv("RSS_FEEDS", "").split(",") if u.strip()]

# --- Amazon Bedrock -----------------------------------------------------
# Selected when MODEL starts with "bedrock/" — see core/brain.py's
# _BEDROCK_PREFIX. Auth is an AWS Bedrock API key (bearer token, no IAM
# access-key/secret pair): set AWS_BEARER_TOKEN_BEDROCK in .env and boto3
# reads it automatically, so nothing here stores or forwards it — this is
# only the region. Needs the optional `anthropic[bedrock]` extra (pulls in
# boto3).
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")

# --- Paths ----------------------------------------------------------------
NOTES_FILE = os.path.join(DATA_DIR, "notes.txt")


def ensure_dirs() -> None:
    """Create the directories Jarvis writes to. Call once at startup."""
    os.makedirs(DATA_DIR, exist_ok=True)


def missing_key_message() -> str | None:
    """Return a user-facing message if the API key is absent, else None.

    Amazon Bedrock (MODEL=bedrock/...) authenticates via
    AWS_BEARER_TOKEN_BEDROCK, which boto3 reads directly from the
    environment — API_KEY is never set for that path, so it must not be
    treated as missing.
    """
    if API_KEY:
        return None
    if MODEL.startswith("bedrock/"):
        if os.getenv("AWS_BEARER_TOKEN_BEDROCK"):
            return None
        return (
            "AWS_BEARER_TOKEN_BEDROCK is not set. Add your AWS Bedrock API "
            "key to .env."
        )
    return (
        "API_KEY is not set. Copy .env.example to .env and add your "
        "API key."
    )
