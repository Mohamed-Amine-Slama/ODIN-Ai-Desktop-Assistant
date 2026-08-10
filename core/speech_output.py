"""Text-to-speech.

Speech runs on a background thread fed by a queue, so the brain can push
sentences as they stream in and Jarvis starts talking before the model has
finished generating. Stage 4 swaps the engine underneath for edge-tts without
changing this interface.
"""
import queue
import threading

_SENTINEL = object()


class SpeechOutput:
    """Queued speaker. `say()` returns immediately; `wait()` blocks until the
    backlog has been spoken."""

    def __init__(self, rate: int = 180, voice_index: int = 0, enabled: bool = True):
        self.enabled = enabled
        self._engine = None
        self._queue: "queue.Queue" = queue.Queue()
        self._idle = threading.Event()
        self._idle.set()
        self._stop = threading.Event()

        if self.enabled:
            self._engine = _make_engine(rate, voice_index)
            if self._engine is None:
                self.enabled = False

        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()

    def say(self, text: str) -> None:
        """Print immediately, queue for speech. Non-blocking."""
        text = (text or "").strip()
        if not text:
            return
        print(f"> {text}")
        if self.enabled:
            self._idle.clear()
            self._queue.put(text)

    def wait(self, timeout: float | None = None) -> None:
        """Block until everything queued so far has been spoken."""
        if self.enabled:
            self._idle.wait(timeout)

    def shutdown(self) -> None:
        self._stop.set()
        self._queue.put(_SENTINEL)

    # -- internals ---------------------------------------------------------

    def _worker(self) -> None:
        while not self._stop.is_set():
            item = self._queue.get()
            if item is _SENTINEL:
                break
            try:
                self._speak_blocking(item)
            except Exception as e:  # never let a TTS glitch kill the assistant
                print(f"[tts] {e}")
            finally:
                if self._queue.empty():
                    self._idle.set()

    def _speak_blocking(self, text: str) -> None:
        if self._engine is None:
            return
        self._engine.say(text)
        self._engine.runAndWait()


def _make_engine(rate: int, voice_index: int):
    """Return a configured pyttsx3 engine, or None if TTS is unavailable
    (no audio device, running under WSL, missing driver)."""
    try:
        import pyttsx3
    except ImportError:
        print("[tts] pyttsx3 not installed — running silent.")
        return None

    try:
        engine = pyttsx3.init()
        engine.setProperty("rate", rate)
        voices = engine.getProperty("voices")
        if voices:
            engine.setProperty("voice", voices[min(voice_index, len(voices) - 1)].id)
        return engine
    except Exception as e:
        print(f"[tts] Speech output unavailable ({e}) — running silent.")
        return None
