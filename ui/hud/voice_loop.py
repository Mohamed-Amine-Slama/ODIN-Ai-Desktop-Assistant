"""VoiceLoopController — the wake/listen/sleep state machine, pulled out of
OdinHudWindow. Owns mode switching (text <-> voice), the boot-time "speak
the greeting, then listen once before requiring the wake phrase" sequence,
and the ordinary sleep -> wake-phrase -> listening cycle after that.

Talks back to its owner only through signals (heard/state_changed/
status_message/greeting_ready) — it never touches window widgets directly,
so it can be constructed and driven against a bare session/bridge in tests
with no OdinHudWindow involved.

Pulled out on its own specifically because it was the newest, most complex,
and most recently buggy cluster of methods on OdinHudWindow — see
shutdown()'s docstring for the thread-lifecycle bug this fixes for real,
not just in the test fixture that used to paper over it.
"""
from __future__ import annotations

import queue
import threading
import time

from PyQt6.QtCore import QCoreApplication, QObject, QTimer, pyqtSignal

import config
from ui.workers import UiBridge, VoiceListenWorker, VoiceSetupWorker


class VoiceLoopController(QObject):
    heard = pyqtSignal(str)            # forwarded from VoiceListenWorker.heard
    state_changed = pyqtSignal(str)    # forwarded from VoiceListenWorker.state_changed ("idle"/"listening")
    status_message = pyqtSignal(str)   # setup/mode-switch text -> console.echo
    greeting_ready = pyqtSignal(str)   # boot greeting text -> transcript_odin + console.echo
    # Internal cross-thread hop: emitted off _wait_then_signal_ready's own
    # background thread, landing back on this QObject's (the GUI) thread —
    # see that method's docstring.
    _ready_for_first_listen = pyqtSignal()

    def __init__(self, session, bridge: UiBridge, parent=None):
        super().__init__(parent)
        self.session = session
        self.bridge = bridge

        self._setup_worker: VoiceSetupWorker | None = None
        self._loop_worker: VoiceListenWorker | None = None
        self._boot_greeting_pending = False
        self._mic_queue = None
        self._mic_timer: QTimer | None = None
        self._mic_smoothed = 0.0
        # Set by shutdown() so a signal already queued when it starts can't
        # spawn a *new* worker mid-drain — the boot-greeting path is two
        # threads deep (setup -> speak-then-listen thread -> listen worker),
        # so a naive stop-and-wait can still let a late signal resurrect it.
        self._closing = False

        self._ready_for_first_listen.connect(lambda: self._start_loop(skip_wake_first=True))

    # -- read-only state for callers/tests ----------------------------------

    @property
    def setup_worker(self) -> VoiceSetupWorker | None:
        return self._setup_worker

    @property
    def loop_worker(self) -> VoiceListenWorker | None:
        return self._loop_worker

    def is_active(self) -> bool:
        return self._loop_worker is not None

    # -- mode switching ------------------------------------------------------

    def toggle_mode(self) -> None:
        if self.session.mode == "voice":
            self.switch_to_text()
        else:
            self.switch_to_voice()

    def switch_to_text(self) -> None:
        self.stop_loop()
        self.status_message.emit(self.session.set_mode("text"))

    def switch_to_voice(self) -> None:
        if self._closing or self._setup_worker is not None or self._loop_worker is not None:
            # Reachable via '/mode voice' typed twice, not just the dock
            # toggle (which already checks session.mode) — without this, a
            # second call here would overwrite _loop_worker with a new
            # VoiceListenWorker while the first is still running, leaking
            # its thread and duplicating every transcription.
            return
        self.status_message.emit("Starting microphone and loading speech models…")
        worker = VoiceSetupWorker(self.session, self)
        self._setup_worker = worker
        worker.finished_ok.connect(self._on_setup_ready)
        worker.failed.connect(self._on_setup_failed)
        worker.start()

    def start_on_boot(self) -> None:
        """Voice-first startup: speak the opening line, then listen for the
        very first order without requiring the wake phrase first — ODIN just
        finished talking and is plainly paying attention. Every turn after
        this one re-arms the normal sleep/wake-phrase gate (see
        _speak_greeting_then_listen and VoiceListenWorker's skip_wake_first)."""
        self._boot_greeting_pending = True
        self.switch_to_voice()

    # -- setup completion ------------------------------------------------

    def _on_setup_ready(self, message: str) -> None:
        self._setup_worker = None
        self.status_message.emit(message)
        if self._closing:
            return
        self.start_mic_meter()
        if self._boot_greeting_pending:
            self._boot_greeting_pending = False
            self._speak_greeting_then_listen()
        else:
            self._start_loop()

    def _on_setup_failed(self, message: str) -> None:
        self._setup_worker = None
        self._boot_greeting_pending = False
        self.status_message.emit(message)

    def _speak_greeting_then_listen(self) -> None:
        greeting = f"{config.ASSISTANT_NAME} online. How can I help?"
        self.greeting_ready.emit(greeting)
        self.session.speaker.say(greeting)
        # speaker.say() only queues playback — listening has to wait for it
        # to actually finish, or the mic picks up ODIN's own voice as the
        # "first order". _ready_for_first_listen is a queued signal, so
        # emitting it from this background thread safely lands the actual
        # VoiceListenWorker construction back on the GUI thread.
        threading.Thread(target=self._wait_then_signal_ready, daemon=True).start()

    def _wait_then_signal_ready(self) -> None:
        self.session.speaker.wait(timeout=30)
        if not self._closing:
            self._ready_for_first_listen.emit()

    # -- the loop itself ---------------------------------------------------

    def _start_loop(self, skip_wake_first: bool = False) -> None:
        if self._closing:
            return
        worker = VoiceListenWorker(self.session, skip_wake_first=skip_wake_first, parent=self)
        self._loop_worker = worker
        worker.heard.connect(self.heard.emit)
        worker.state_changed.connect(self.state_changed.emit)
        worker.start()

    def stop_loop(self) -> None:
        if self._loop_worker is not None:
            self._loop_worker.stop()
            self._loop_worker.wait(2000)
            self._loop_worker = None
        self.stop_mic_meter()

    def notify_turn_finished(self) -> None:
        if self._loop_worker is not None:
            threading.Thread(target=self._resume_after_speech, daemon=True).start()

    def _resume_after_speech(self) -> None:
        self.session.speaker.wait(timeout=60)
        if self._loop_worker is not None:
            self._loop_worker.resume()

    # -- mic amplitude, ~20Hz while listening (§5.3) --------------------------

    def start_mic_meter(self) -> None:
        if self._mic_timer is not None or self.session.mic is None:
            return
        try:
            import numpy  # noqa: F401 - only imported to confirm it's actually available
        except ImportError:
            return
        self._mic_queue = self.session.mic.subscribe()
        self._mic_smoothed = 0.0
        self._mic_timer = QTimer(self)
        self._mic_timer.timeout.connect(self._on_mic_tick)
        self._mic_timer.start(50)

    def stop_mic_meter(self) -> None:
        if self._mic_timer is not None:
            self._mic_timer.stop()
            self._mic_timer = None
        if self._mic_queue is not None and self.session.mic is not None:
            self.session.mic.unsubscribe(self._mic_queue)
        self._mic_queue = None

    def _on_mic_tick(self) -> None:
        if self._mic_queue is None:
            return
        import numpy as np

        from core.audio import rms

        level = None
        while True:
            try:
                block = self._mic_queue.get_nowait()
            except queue.Empty:
                break
            level = rms(block, np)
        if level is not None:
            self._mic_smoothed += (level - self._mic_smoothed) * 0.5  # ~60ms smoothing at 20Hz
            self.bridge.mic_rms.emit(min(1.0, self._mic_smoothed * 4))

    # -- shutdown ------------------------------------------------------------

    def shutdown(self, timeout_ms: int = 2000) -> None:
        """Stop every voice-loop thread and make sure any signal already
        queued from it (finished_ok/failed/heard/state_changed, including
        ones that themselves spawn the *next* worker — see
        _speak_greeting_then_listen's chain) is delivered and handled while
        this controller is still alive, rather than firing later against
        whatever this controller's owner has become by then. Call before
        the owning window is dismissed/deleted, or on process shutdown.
        Idempotent; safe to call more than once."""
        self._closing = True
        deadline = time.monotonic() + timeout_ms / 1000
        while time.monotonic() < deadline:
            pending = self._setup_worker is not None or self._loop_worker is not None
            self.stop_loop()
            if self._setup_worker is not None:
                self._setup_worker.wait(2000)
                self._setup_worker = None
            QCoreApplication.processEvents()
            if not pending and self._setup_worker is None and self._loop_worker is None:
                break
        self.stop_mic_meter()
