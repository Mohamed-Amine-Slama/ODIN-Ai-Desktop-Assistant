"""Text-to-speech.

Speech runs on a background thread fed by a queue, so the brain can push
sentences as they stream in and Jarvis starts talking before the model has
finished generating.

Two engines, selected by config.TTS_ENGINE:
  edge  - Microsoft Edge neural voices. Free, no API key, far more natural
          than SAPI5. Needs a network connection.
  sapi  - Offline Windows SAPI5 voices via pyttsx3. Robotic but always there.
"""
import asyncio
import os
import queue
import tempfile
import threading
import time

import config

_SENTINEL = object()


class SpeechOutput:
    """Queued speaker. `say()` returns immediately; `wait()` blocks until the
    backlog has been spoken."""

    def __init__(self, engine: str | None = None, voice: str | None = None, enabled: bool = True):
        self.enabled = enabled
        self._engine = None
        self._queue: "queue.Queue" = queue.Queue()
        self._idle = threading.Event()
        self._idle.set()
        self._stop = threading.Event()

        if self.enabled:
            self._engine = _make_engine(engine or config.TTS_ENGINE, voice or config.TTS_VOICE)
            if self._engine is None:
                self.enabled = False

        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()

    @property
    def engine_name(self) -> str:
        return getattr(self._engine, "name", "silent")

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

    def is_speaking(self) -> bool:
        """Whether the worker is actively playing or has a backlog queued."""
        return self.enabled and not self._idle.is_set()

    def stop(self) -> None:
        """Cut off whatever is currently playing and drop anything still
        queued. Used for barge-in: once the user starts talking, the rest of
        a queued reply is no longer worth finishing."""
        if not self.enabled:
            return
        try:
            while True:
                self._queue.get_nowait()
        except queue.Empty:
            pass
        if self._engine is not None:
            try:
                self._engine.stop()
            except Exception as e:  # never let a TTS glitch propagate
                print(f"[tts] {e}")
        self._idle.set()

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
        self._engine.speak(text)


class EdgeEngine:
    """Microsoft Edge neural voices. Free, no key, needs network."""

    name = "edge"

    def __init__(self, voice: str):
        import edge_tts  # noqa: F401 - imported here so a missing dep fails fast

        os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
        import pygame

        self.voice = voice
        self._edge_tts = edge_tts
        pygame.mixer.init()
        self._mixer = pygame.mixer

    def speak(self, text: str) -> None:
        path = None
        try:
            path = self._synthesize(text)
            # mixer.music (not Sound) is the streaming path that handles mp3.
            self._mixer.music.load(path)
            self._mixer.music.play()
            while self._mixer.music.get_busy():
                time.sleep(0.02)
        finally:
            if path:
                # Windows keeps the file handle open until unload(), which
                # makes the unlink below fail with "file in use".
                try:
                    self._mixer.music.unload()
                except Exception:
                    pass
                try:
                    os.unlink(path)
                except OSError:
                    pass

    def _synthesize(self, text: str) -> str:
        fd, path = tempfile.mkstemp(suffix=".mp3")
        os.close(fd)

        async def run():
            await self._edge_tts.Communicate(text, self.voice).save(path)

        # The worker thread has no running loop, so a fresh one is fine here.
        asyncio.run(run())
        return path


class SapiEngine:
    """Offline Windows SAPI5 voices via pyttsx3."""

    name = "sapi"

    def __init__(self, rate: int):
        import pyttsx3

        self._engine = pyttsx3.init()
        self._engine.setProperty("rate", rate)

    def speak(self, text: str) -> None:
        self._engine.say(text)
        self._engine.runAndWait()


def _make_engine(preference: str, voice: str):
    """Build the best available engine, or None to run silent.

    'auto' prefers edge-tts and falls back to SAPI, because a missing network
    or a missing pygame shouldn't leave Jarvis mute.
    """
    preference = (preference or "auto").lower()
    if preference in ("off", "none", "silent"):
        return None

    attempts = []
    if preference in ("auto", "edge"):
        attempts.append(("edge", lambda: EdgeEngine(voice)))
    if preference in ("auto", "sapi", "pyttsx3"):
        attempts.append(("sapi", lambda: SapiEngine(config.TTS_RATE)))

    problems = []
    for label, build in attempts:
        try:
            return build()
        except Exception as e:
            problems.append(f"{label}: {e}")

    for problem in problems:
        print(f"[tts] {problem}")
    print("[tts] No speech engine available — running silent (text still prints).")
    return None
