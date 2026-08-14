"""Tests for skills/gesture_skills.py: the voice/text activation path for
hand-gesture cursor control.

The manual HUD/tray toggle (ui/hud/window.py) bypasses this skill entirely,
so it isn't covered here — see the risk-tier reasoning in
core/gesture.py/skills/gesture_skills.py's module docstrings for why the two
paths are gated differently.
"""
import importlib

import pytest

import config
import skills.skill_manager as sm
from core.gesture import GestureController, set_gesture_controller
from core.risk import Risk
from skills.gesture_skills import HandControlSkill


@pytest.fixture(autouse=True)
def _clean_controller():
    yield
    set_gesture_controller(None)


class _FakeController:
    def __init__(self):
        self.calls = []

    def start(self):
        self.calls.append("start")
        return "Hand control is starting."

    def stop(self):
        self.calls.append("stop")
        return "Hand control is off."


def test_start_is_dangerous():
    assert HandControlSkill().risk_for(action="start") == Risk.DANGEROUS


def test_stop_is_safe_and_never_gated():
    """Turning the camera off must never be blocked behind a confirmation."""
    assert HandControlSkill().risk_for(action="stop") == Risk.SAFE


def test_consequence_mentions_the_camera_and_the_gesture_vocabulary():
    text = HandControlSkill().consequence(action="start")
    assert "camera" in text.lower()
    assert "pinch" in text.lower()


def test_run_start_delegates_to_the_controller():
    fake = _FakeController()
    set_gesture_controller(fake)
    assert HandControlSkill().run(action="start") == "Hand control is starting."
    assert fake.calls == ["start"]


def test_run_stop_delegates_to_the_controller():
    fake = _FakeController()
    set_gesture_controller(fake)
    assert HandControlSkill().run(action="stop") == "Hand control is off."
    assert fake.calls == ["stop"]


def test_run_start_and_stop_use_the_same_singleton():
    """Both the skill and the HUD tray toggle must drive the one real
    controller app.py wires up at startup, not independent instances."""
    fake = _FakeController()
    set_gesture_controller(fake)
    HandControlSkill().run(action="start")
    HandControlSkill().run(action="stop")
    assert fake.calls == ["start", "stop"]


def test_run_rejects_an_unknown_action():
    out = HandControlSkill().run(action="sideways")
    assert "start" in out and "stop" in out


def test_missing_opencv_surfaces_as_a_message_not_a_crash(monkeypatch):
    """No fake controller installed: get_gesture_controller() builds a real
    GestureController, and starting it without opencv installed must degrade
    gracefully (via on_state_change("error", ...) on the capture thread),
    same as every other optional-dependency skill."""
    import sys
    import time

    monkeypatch.setitem(sys.modules, "cv2", None)
    controller = GestureController()
    set_gesture_controller(controller)

    result = HandControlSkill().run(action="start")

    assert result == "Hand control is starting."  # start() itself never blocks
    deadline = time.time() + 2.0
    while controller.is_running() and time.time() < deadline:
        time.sleep(0.02)
    assert not controller.is_running()


def test_kill_switch_removes_the_tool(monkeypatch):
    """Off by default: hand_control must not appear in the tool list unless
    ENABLE_GESTURE_CONTROL is explicitly turned on."""
    monkeypatch.setattr(config, "ENABLE_GESTURE_CONTROL", False)
    importlib.reload(sm)
    try:
        names = {t["name"] for t in sm.SkillManager().tool_definitions()}
        assert "hand_control" not in names
    finally:
        importlib.reload(sm)


def test_enabling_the_flag_registers_the_tool(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_GESTURE_CONTROL", True)
    importlib.reload(sm)
    try:
        names = {t["name"] for t in sm.SkillManager().tool_definitions()}
        assert "hand_control" in names
    finally:
        monkeypatch.setattr(config, "ENABLE_GESTURE_CONTROL", False)
        importlib.reload(sm)
