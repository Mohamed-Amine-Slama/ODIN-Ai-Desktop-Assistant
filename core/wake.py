"""Wake trigger — say the assistant's name and "wake up".

openWakeWord's only usable pretrained model recognises a fixed phrase
("hey jarvis"), which doesn't track a configurable ASSISTANT_NAME. Instead
this reuses the same VAD+Whisper pipeline SpeechInput already loads for
normal commands: while "asleep", short utterances are transcribed and
checked for the assistant's name plus "wake up". That trades the low-CPU,
always-on efficiency of a dedicated wake-word model for a phrase that
follows whatever name Jarvis is branded as, at the cost of a full
transcription per utterance and a slightly slower, less certain trigger.
"""
import re

import config
from core.audio import Microphone
from core.speech_input import SpeechInput

# Short: a wake phrase is a couple of words, and keeping this well under
# SpeechInput's normal command window (config.VAD_MAX_SECONDS) keeps the
# detector checking stop_event often instead of blocking for a full 20s
# whenever the room is silent.
WAKE_LISTEN_SECONDS = 6.0


class PhraseWakeDetector:
    """Blocks until an utterance containing the assistant's name and "wake
    up" is heard, in either order ("ODIN, wake up" / "wake up, ODIN")."""

    def __init__(self, listener: SpeechInput, name: str | None = None):
        self.listener = listener
        name = re.escape((name or config.ASSISTANT_NAME).strip().lower())
        self._pattern = re.compile(
            rf"\b{name}\b.{{0,25}}\bwake\s*up\b|\bwake\s*up\b.{{0,25}}\b{name}\b"
        )

    def wait(self, stop_event=None) -> bool:
        """Block until the wake phrase is heard. Returns False if stopped first."""
        while True:
            if stop_event is not None and stop_event.is_set():
                return False
            text = self.listener.listen(max_seconds=WAKE_LISTEN_SECONDS)
            if stop_event is not None and stop_event.is_set():
                return False
            if text and self._pattern.search(text.lower()):
                return True


def make_detector(mic: Microphone, listener: "SpeechInput | None" = None) -> "PhraseWakeDetector | None":
    """Build the wake trigger, or return None if wake mode is off/unavailable.

    `listener` lets a caller that already built a SpeechInput (and so
    already paid for loading the Whisper model) share it instead of this
    loading a second copy. Never raises: a wake trigger that fails to come
    up should degrade to push-to-talk, not stop Jarvis from starting.
    """
    if config.WAKE_WORD.lower() in ("off", "none", ""):
        return None
    try:
        speech = listener or SpeechInput(mic=mic)
        return PhraseWakeDetector(speech)
    except Exception as e:
        print(f"[wake] couldn't start the wake-phrase listener ({e}).")
        return None
