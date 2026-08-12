"""ui/workers.py's UiBridge — the HUD-only signal additions: skill_logged
duration, kb_changed on a successful deep_learn, and the
core.learning_status -> learning_progress relay.
"""
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
