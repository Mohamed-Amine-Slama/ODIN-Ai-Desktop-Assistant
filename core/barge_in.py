"""Barge-in: let the user interrupt Jarvis mid-sentence by talking over it.

Unlike wake-word detection (a keyword match against a trained model), this is
a plain sustained-energy watcher: while Jarvis is speaking, a burst of
speech-level volume on the mic — a few consecutive loud blocks, not a single
spike from a cough or a chair creak — counts as "the user wants to interrupt".

No acoustic echo cancellation: this has no way to tell the user's voice apart
from Jarvis's own reply coming back through the mic off the speakers, so it
works best with headphones. Over open speakers it may trigger on Jarvis's own
voice; raise BARGE_IN_THRESHOLD or use headphones if that happens.
"""
import threading

import config
from core.audio import Microphone, rms

# Consecutive loud blocks required before it counts as a real interruption
# rather than a single spike. At the 80ms block size in core.audio, this is
# roughly a quarter-second of sustained volume.
_SUSTAINED_BLOCKS = 3


class BargeInWatcher:
    """Watches the shared microphone for sustained speech while active.

    start()/stop() control a background thread and are cheap to call around
    each turn's speaking phase — start() is a no-op if already running.
    on_interrupt is called from the watcher's own thread, not the caller's.
    """

    def __init__(self, mic: Microphone, on_interrupt, threshold: float | None = None):
        self.mic = mic
        self.on_interrupt = on_interrupt
        self.threshold = threshold if threshold is not None else config.BARGE_IN_THRESHOLD
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._watch, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        self._thread = None

    def _watch(self) -> None:
        q = self.mic.subscribe()
        loud_run = 0
        try:
            while not self._stop.is_set():
                try:
                    block = q.get(timeout=0.2)
                except Exception:
                    continue

                level = rms(block, self.mic._np)
                if level >= self.threshold:
                    loud_run += 1
                    if loud_run >= _SUSTAINED_BLOCKS:
                        self._stop.set()
                        self.on_interrupt()
                        return
                else:
                    loud_run = 0
        finally:
            self.mic.unsubscribe(q)


def make_watcher(mic: "Microphone | None", on_interrupt) -> "BargeInWatcher | None":
    """Build a watcher, or None if there's no microphone to watch.

    Never raises: barge-in is a bonus on top of voice mode, not a
    precondition for it — a build failure here must not stop voice mode
    from working at all.
    """
    if mic is None:
        return None
    try:
        return BargeInWatcher(mic, on_interrupt)
    except Exception as e:
        print(f"[barge-in] {e}")
        return None
