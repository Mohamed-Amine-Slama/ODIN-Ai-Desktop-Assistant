"""Wake-word detection — "Hey Jarvis".

openWakeWord ships a pretrained `hey_jarvis` model, so this needs no API key,
no Picovoice account, and no training. Models download once on first run.
"""
import config
from core.audio import Microphone, MicrophoneUnavailable


class WakeWordUnavailable(RuntimeError):
    """Raised when the wake-word engine or its model can't be loaded."""


class WakeWordDetector:
    def __init__(self, mic: Microphone, wake_word: str | None = None, threshold: float | None = None):
        self.mic = mic
        self.wake_word = wake_word or config.WAKE_WORD
        self.threshold = threshold if threshold is not None else config.WAKE_THRESHOLD
        self._model = _load_model(self.wake_word)

    def wait(self, stop_event=None) -> bool:
        """Block until the wake word is heard. Returns False if stopped first."""
        q = self.mic.subscribe()
        try:
            self._model.reset()
            while True:
                if stop_event is not None and stop_event.is_set():
                    return False
                try:
                    block = q.get(timeout=0.5)
                except Exception:
                    continue

                scores = self._model.predict(block)
                if any(score >= self.threshold for score in scores.values()):
                    self._model.reset()
                    return True
        finally:
            self.mic.unsubscribe(q)


def _load_model(wake_word: str):
    try:
        from openwakeword.model import Model
        import openwakeword
    except ImportError as e:
        raise WakeWordUnavailable(
            "openwakeword isn't installed. Run: pip install -r requirements.txt"
        ) from e

    # Pretrained weights are fetched once and cached by the package.
    try:
        openwakeword.utils.download_models()
    except Exception:
        pass  # already present, or offline — Model() will report properly

    try:
        return Model(wakeword_models=[wake_word], inference_framework="onnx")
    except Exception as e:
        raise WakeWordUnavailable(
            f"couldn't load the '{wake_word}' wake-word model ({e}). "
            "Set WAKE_WORD=off in .env to use push-to-talk instead."
        ) from e


def make_detector(mic: Microphone) -> "WakeWordDetector | None":
    """Build a detector, or return None if wake-word mode is off/unavailable.

    Never raises: a missing wake word should degrade to push-to-talk, not stop
    Jarvis from starting.
    """
    if config.WAKE_WORD.lower() in ("off", "none", ""):
        return None
    try:
        return WakeWordDetector(mic)
    except (WakeWordUnavailable, MicrophoneUnavailable) as e:
        print(f"[wake] {e}")
        return None
