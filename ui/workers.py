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

from PyQt6.QtCore import QObject, QThread, pyqtSignal

import config
from core.brain import friendly_error
from core.undo import get_journal


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

    def __init__(self, speaker=None, parent=None):
        super().__init__(parent)
        self.speaker = speaker
        self._answer = False
        self._answered = threading.Event()

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
        the call produced anything undoable."""
        if phase == "start":
            self.tool_started.emit(skill_name, _brief_args(tool_input))
        else:
            is_error = bool(outcome.is_error) if outcome is not None else False
            content = outcome.content if outcome is not None else ""
            self.tool_finished.emit(skill_name, is_error, _brief_result(content))

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
