"""ui/workers.py's UiBridge — the HUD-only signal additions (skill_logged
duration, kb_changed on a successful deep_learn, the core.learning_status ->
learning_progress relay) plus the core on_text/confirm/on_action behavior
shared by every UI that wires a UiBridge (moved here from the now-deleted
tests/test_gui.py, which tested it against the legacy JarvisMainWindow).
"""
import threading
from unittest.mock import MagicMock

from core import learning_status
from ui.workers import SkillLogEntry, UiBridge


def test_skill_logged_carries_a_duration(qapp, monkeypatch):
    bridge = UiBridge()
    received = []
    bridge.skill_logged.connect(received.append)

    ticks = iter([100.0, 100.25])  # 250ms apart
    monkeypatch.setattr("ui.workers.time.monotonic", lambda: next(ticks))

    bridge.on_tool_activity("start", "web_search", {"query": "x"})
    outcome = MagicMock(is_error=False, content="done")
    bridge.on_tool_activity("end", "web_search", {"query": "x"}, outcome)

    assert len(received) == 1
    entry = received[0]
    assert isinstance(entry, SkillLogEntry)
    assert entry.skill == "web_search"
    assert entry.ok is True
    assert abs(entry.ms - 250) < 1


def test_skill_logged_marks_errors(qapp):
    bridge = UiBridge()
    received = []
    bridge.skill_logged.connect(received.append)

    bridge.on_tool_activity("start", "close_app", {"name": "x"})
    outcome = MagicMock(is_error=True, content="failed")
    bridge.on_tool_activity("end", "close_app", {"name": "x"}, outcome)

    assert received[0].ok is False


def test_deep_learn_success_emits_kb_changed(qapp):
    bridge = UiBridge()
    kb_signals = []
    bridge.kb_changed.connect(lambda: kb_signals.append(True))

    bridge.on_tool_activity("start", "deep_learn", {"topic": "react"})
    outcome = MagicMock(is_error=False, content="learned")
    bridge.on_tool_activity("end", "deep_learn", {"topic": "react"}, outcome)

    assert kb_signals == [True]


def test_deep_learn_failure_does_not_emit_kb_changed(qapp):
    bridge = UiBridge()
    kb_signals = []
    bridge.kb_changed.connect(lambda: kb_signals.append(True))

    bridge.on_tool_activity("start", "deep_learn", {"topic": "react"})
    outcome = MagicMock(is_error=True, content="failed")
    bridge.on_tool_activity("end", "deep_learn", {"topic": "react"}, outcome)

    assert kb_signals == []


def test_other_skills_do_not_emit_kb_changed(qapp):
    bridge = UiBridge()
    kb_signals = []
    bridge.kb_changed.connect(lambda: kb_signals.append(True))

    bridge.on_tool_activity("start", "web_search", {"query": "x"})
    outcome = MagicMock(is_error=False, content="ok")
    bridge.on_tool_activity("end", "web_search", {"query": "x"}, outcome)

    assert kb_signals == []


def test_report_learning_progress_re_emits_as_a_signal(qapp):
    bridge = UiBridge()
    received = []
    bridge.learning_progress.connect(lambda *args: received.append(args))

    bridge.report_learning_progress("react", "Hooks", 0.5)
    assert received == [("react", "Hooks", 0.5)]


def test_learning_status_report_reaches_the_bridge_when_wired(qapp):
    bridge = UiBridge()
    received = []
    bridge.learning_progress.connect(lambda *args: received.append(args))

    learning_status.set_callback(bridge.report_learning_progress)
    try:
        learning_status.report("react", "Hooks", 0.75)
    finally:
        learning_status.set_callback(None)  # don't leak into other tests

    assert received == [("react", "Hooks", 0.75)]


def test_learning_status_report_is_a_no_op_without_a_callback():
    learning_status.set_callback(None)
    learning_status.report("react", "Hooks", 0.75)  # must not raise


# -- core on_text/confirm/on_action behavior --------------------------------


def test_bridge_speaks_and_displays_every_sentence(qapp):
    """Regression: the GUI replaced the brain's on_text callback with one that
    only drew to the screen, which left the desktop app completely mute."""
    speaker = MagicMock()
    bridge = UiBridge(speaker=speaker)
    seen = []
    bridge.text_chunk.connect(seen.append)

    bridge.on_text("All done.")

    speaker.say.assert_called_once_with("All done.")
    assert seen == ["All done."]


def test_bridge_confirmation_blocks_until_answered(qapp):
    bridge = UiBridge()
    skill = MagicMock()
    skill.consequence.return_value = "Delete it?"

    result = {}
    worker = threading.Thread(target=lambda: result.update(ok=bridge.confirm(skill, {})))
    worker.start()
    # The worker is parked on the Event until the GUI thread answers.
    assert not bridge._answered.wait(timeout=0.1)
    bridge.answer(True)
    worker.join(timeout=5)

    assert result == {"ok": True}


def test_bridge_confirmation_defaults_to_no_on_timeout(qapp, monkeypatch):
    """Nothing is refused outright, but an unanswered question is not consent."""
    import config

    monkeypatch.setattr(config, "CONFIRM_TIMEOUT_SECONDS", 0.05)
    bridge = UiBridge()
    skill = MagicMock()
    skill.consequence.return_value = "Format the drive?"

    assert bridge.confirm(skill, {}) is False


def test_bridge_reports_undo_token_only_when_one_exists(qapp):
    from core.undo import UndoJournal, set_journal

    journal = UndoJournal()
    set_journal(journal)
    try:
        bridge = UiBridge()
        seen = []
        bridge.action_reported.connect(lambda *args: seen.append(args))

        skill = MagicMock()
        skill.name = "type_text"
        bridge.on_action(skill, {}, MagicMock(undo_token=None))
        assert seen[-1] == ("type_text", "", "")

        token = journal.record("Restore notes.txt", lambda: "back")
        skill.name = "write_file"
        bridge.on_action(skill, {}, MagicMock(undo_token=token))
        assert seen[-1] == ("write_file", token, "Restore notes.txt")
    finally:
        set_journal(None)
