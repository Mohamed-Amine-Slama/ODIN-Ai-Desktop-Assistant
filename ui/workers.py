"""Threading seam between the Qt event loop and the brain.

Brain.ask() blocks for as long as the model and its tools take, so it runs on a
worker thread. Its three callbacks fire on that thread, and Qt widgets may only
be touched from the GUI thread — UiBridge exists to turn each callback into a
signal, which Qt delivers to the GUI thread for us.

Confirmation runs the other way: the worker has to stop and wait for a human.
It blocks on an Event that the GUI sets when a button is clicked, and defaults
to declining if nobody ever answers.
"""
import threading
import time
from dataclasses import dataclass

from PyQt6.QtCore import QObject, QThread, pyqtSignal

import config
from core.brain import friendly_error
from core.undo import get_journal


@dataclass
class SkillLogEntry:
    """One row for the HUD's skill-activity panel (ODIN-HUD.md §6.5, zone
    E2) — everything ActionCardWidget/ActivityLogWidget used to render
    inline in the chat feed, now carried as data instead of a widget."""

    ts: float
    skill: str
    ok: bool
    ms: float


def _brief_args(tool_input: dict) -> str:
    """First couple of kwargs, truncated — enough to recognise the call at a
    glance without dumping a full argument list into the activity feed."""
    if not tool_input:
        return ""
    parts = []
    for key, value in list(tool_input.items())[:2]:
        text = str(value)
        if len(text) > 40:
            text = text[:40] + "…"
        parts.append(f"{key}={text}")
    return ", ".join(parts)


def _brief_result(content) -> str:
    if isinstance(content, list):
        return "(image)"
    text = str(content)
    return text if len(text) <= 90 else text[:90] + "…"


class UiBridge(QObject):
    """Brain callbacks in, Qt signals out."""

    text_chunk = pyqtSignal(str)
    action_reported = pyqtSignal(str, str, str)  # skill name, undo token, description
    confirm_requested = pyqtSignal(str)          # the question to put to the user
    tool_started = pyqtSignal(str, str)          # skill name, brief args
    tool_finished = pyqtSignal(str, bool, str)   # skill name, is_error, brief result

    # HUD-only additions (ODIN-HUD.md §7.1's "odin" message, decomposed into
    # per-field signals instead of one dict-shaped payload).
    mic_rms = pyqtSignal(float)                       # ~20Hz while listening
    learning_progress = pyqtSignal(str, str, float)    # topic, subtopic, 0..1
    skill_logged = pyqtSignal(object)                  # SkillLogEntry
    kb_changed = pyqtSignal()                          # a deep_learn run just completed
    gesture_state_changed = pyqtSignal(str, str)       # "idle" | "active" | "error", message

    def __init__(self, speaker=None, parent=None):
        super().__init__(parent)
        self.speaker = speaker
        self._answer = False
        self._answered = threading.Event()
        self._tool_start_ts: float | None = None

    # -- called on the worker thread --------------------------------------

    def on_text(self, sentence: str) -> None:
        if self.speaker is not None:
            self.speaker.say(sentence)
        self.text_chunk.emit(sentence)

    def on_action(self, skill, tool_input, outcome) -> None:  # noqa: ARG002
        """Report a completed action. Only claims it is undoable when the skill
        actually recorded a way back, so the button never lies."""
        token = outcome.undo_token or ""
        description = ""
        if token:
            entry = get_journal().latest()
            description = entry.description if entry is not None else ""
        self.action_reported.emit(skill.name, token, description)

    def on_tool_activity(self, phase, skill_name, tool_input, outcome=None) -> None:
        """Every tool call, start and end — the live trace of a multi-step
        turn. Unlike on_action, this fires regardless of risk tier or whether
        the call produced anything undoable.

        Tool calls within one turn run strictly sequentially (Brain._run_tools
        iterates its response blocks one at a time), so a single stashed
        start timestamp is enough to time each call — no dict keyed by call
        ID needed.
        """
        if phase == "start":
            self._tool_start_ts = time.monotonic()
            self.tool_started.emit(skill_name, _brief_args(tool_input))
        else:
            is_error = bool(outcome.is_error) if outcome is not None else False
            content = outcome.content if outcome is not None else ""
            self.tool_finished.emit(skill_name, is_error, _brief_result(content))
            ms = (time.monotonic() - self._tool_start_ts) * 1000 if self._tool_start_ts is not None else 0.0
            self.skill_logged.emit(SkillLogEntry(ts=time.time(), skill=skill_name, ok=not is_error, ms=ms))
            if skill_name == "deep_learn" and not is_error:
                self.kb_changed.emit()

    def report_learning_progress(self, topic: str, subtopic: str, progress: float) -> None:
        """Wired to core.learning_status.set_callback() once at startup — a
        thin re-emit so core/ stays Qt-free (see core/learning_status.py)."""
        self.learning_progress.emit(topic, subtopic, progress)

    def on_gesture_state(self, state: str, message: str) -> None:
        """Wired to GestureController's on_state_change at startup (app.py) —
        fires from the camera capture thread, same re-emit pattern as
        report_learning_progress above, so core/gesture.py stays Qt-free."""
        self.gesture_state_changed.emit(state, message)

    def confirm(self, skill, tool_input) -> bool:
        """Block the worker until the HUD answers. Defaults to no."""
        self._answer = False
        self._answered.clear()
        self.confirm_requested.emit(skill.consequence(**tool_input))
        if not self._answered.wait(timeout=config.CONFIRM_TIMEOUT_SECONDS):
            return False
        return self._answer

    # -- called on the GUI thread -----------------------------------------

    def answer(self, approved: bool) -> None:
        self._answer = bool(approved)
        self._answered.set()

    def release(self) -> None:
        """Unblock a pending confirmation as a decline. Used on shutdown so a
        worker parked on the Event doesn't keep the process alive."""
        self._answer = False
        self._answered.set()


class VoiceSetupWorker(QThread):
    """Loads the microphone, speech-to-text, and wake-word models off the GUI
    thread.

    session.set_mode("voice") can mean a first-run model download
    (openWakeWord, faster-whisper) or slow audio device negotiation — anywhere
    from a couple of seconds to a real network-bound wait. Doing that inline
    in a button click handler, as the HUD used to, freezes the whole window
    for the entire duration with no way to cancel short of killing the
    process. This just moves the same call off the GUI thread.
    """

    finished_ok = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, session, parent=None):
        super().__init__(parent)
        self.session = session

    def run(self) -> None:
        try:
            message = self.session.set_mode("voice")
        except Exception as e:  # noqa: BLE001 - a failed setup must not kill the HUD
            self.failed.emit(f"Couldn't start voice mode: {e}")
            return
        if self.session.mode == "voice":
            self.finished_ok.emit(message)
        else:
            # set_mode() never raises — a setup failure is reported by
            # falling back to text mode and returning an explanation instead.
            self.failed.emit(message)


class VoiceListenWorker(QThread):
    """Runs wait-for-wake-word -> record -> transcribe in a loop, off the GUI
    thread, until stop() is called.

    Paused (not stopped) while a turn is being handled and spoken back —
    otherwise the open mic would pick up the assistant's own reply through
    the speakers and treat it as the next thing the user said.
    """

    heard = pyqtSignal(str)
    state_changed = pyqtSignal(str)  # "idle" (waiting) or "listening" (recording)
    error = pyqtSignal(str)

    def __init__(self, session, skip_wake_first: bool = False, parent=None):
        super().__init__(parent)
        self.session = session
        self._stop_event = threading.Event()
        self._pause = threading.Event()
        # Consumed on the loop's first live iteration only — lets a caller
        # (the HUD's post-greeting boot flow) skip straight to listening
        # once, without disabling the sleep/wake-phrase gate for every turn
        # after it.
        self._skip_wake_first = skip_wake_first

    def stop(self) -> None:
        self._stop_event.set()

    def pause(self) -> None:
        self._pause.set()

    def resume(self) -> None:
        self._pause.clear()

    def run(self) -> None:
        while not self._stop_event.is_set():
            if self._pause.is_set():
                self._stop_event.wait(0.2)
                continue
            skip_wake, self._skip_wake_first = self._skip_wake_first, False
            try:
                if self.session.wake is not None and not skip_wake:
                    self.state_changed.emit("idle")
                    if not self.session.wake.wait(stop_event=self._stop_event):
                        continue
                if self._stop_event.is_set():
                    break
                self.state_changed.emit("listening")
                text = self.session.listener.listen()
            except Exception as e:  # noqa: BLE001 - one bad cycle must not kill the loop
                self.error.emit(str(e))
                self._stop_event.wait(1.0)
                continue

            if text:
                # Stop capturing the moment something was heard — resumed by
                # the GUI once the resulting turn has finished playing back.
                self._pause.set()
                self.heard.emit(text)
        self.state_changed.emit("idle")


class BrainWorker(QThread):
    """Runs exactly one turn.

    The brain's callbacks are wired once, at construction of the app, not swapped
    per turn — swapping shared state from a worker thread is how you end up with
    one turn's output arriving on another turn's widgets.
    """

    turn_finished = pyqtSignal(str)
    error_occurred = pyqtSignal(str)

    def __init__(self, brain, user_text: str, parent=None):
        super().__init__(parent)
        self.brain = brain
        self.user_text = user_text

    def run(self) -> None:
        try:
            self.turn_finished.emit(self.brain.ask(self.user_text))
        except Exception as e:  # noqa: BLE001 - a failed turn must not kill the HUD
            self.error_occurred.emit(friendly_error(e))
