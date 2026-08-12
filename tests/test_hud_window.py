"""ui/hud/window.py's OdinHudWindow — construction, dock dispatch, the
DANGEROUS-tier confirmation gate, and the knowledge-base refresh trigger.

Follows tests/test_gui.py's convention: MagicMock brain/session, a real
UiBridge (it's cheap — a QObject with a handful of signals), QTest.qWait
after show() for real layout, explicit teardown.
"""
from unittest.mock import MagicMock

import pytest
from PyQt6.QtTest import QTest

from ui.hud.confirm import ConfirmationBannerWidget
from ui.hud.window import OdinHudWindow
from ui.workers import UiBridge


@pytest.fixture
def mock_brain():
    brain = MagicMock()
    brain.ask.return_value = "Hello from ODIN!"
    return brain


@pytest.fixture
def mock_session():
    session = MagicMock()
    session.mode = "text"
    session.mic = None
    session.set_mode.return_value = "Switched mode."
    return session


@pytest.fixture
def window(qapp, mock_brain, mock_session):
    bridge = UiBridge()
    win = OdinHudWindow(mock_brain, mock_session, bridge)
    yield win
    win.dismiss()
    win.tray_icon.hide()
    win.deleteLater()


def test_window_builds_at_the_spec_fixed_resolution(window):
    assert window.size().width() == 1920
    assert window.size().height() == 1080


def test_all_named_zones_exist(window):
    # A spot check across the grid — one attribute per zone builder, not
    # every widget — enough to catch a zone silently failing to build.
    for attr in (
        "ruler", "cpu_bar", "ram_bar", "gauge_cpu", "orb", "transcript_odin",
        "clock_label", "weather_temp", "temp_cpu", "net_ip", "spectrum",
    ):
        assert hasattr(window, attr), f"zone widget '{attr}' was not built"


def test_show_and_activate_starts_the_background_workers(window):
    window.show_and_activate()
    QTest.qWait(50)
    assert window.telemetry.isRunning()
    assert window.weather.isRunning()
    assert window._anim_timer.isActive()


def test_dismiss_stops_the_background_workers(window):
    window.show_and_activate()
    QTest.qWait(50)
    window.dismiss()
    assert not window.telemetry.isRunning()
    assert not window.weather.isRunning()
    assert not window._anim_timer.isActive()


def test_dock_click_routes_through_brain_worker(window, mock_brain):
    window.show_and_activate()
    QTest.qWait(50)
    window._launch_preset("open file explorer")
    assert window.current_worker is not None
    QTest.qWait(300)
    mock_brain.ask.assert_called_with("open file explorer")


def test_launch_preset_is_a_no_op_while_a_turn_is_in_flight(window, mock_brain):
    window.show_and_activate()
    window._launch_preset("first command")
    calls_after_first = mock_brain.ask.call_count
    window._launch_preset("second command")  # current_worker is still set
    assert mock_brain.ask.call_count == calls_after_first
    QTest.qWait(300)


def test_confirm_requested_shows_a_banner_and_answering_clears_it(window):
    window.show_and_activate()
    QTest.qWait(50)
    window._on_confirm_requested("Really shut down?")
    assert window._confirm_banner is not None
    assert isinstance(window._confirm_banner, ConfirmationBannerWidget)

    banner = window._confirm_banner
    banner.answered.emit(True)
    QTest.qWait(20)
    assert window._confirm_banner is None


def test_confirm_answer_forwards_to_the_bridge(window):
    window.bridge.answer = MagicMock()
    window.show_and_activate()
    window._on_confirm_requested("Delete everything?")
    window._confirm_banner.answered.emit(False)
    QTest.qWait(20)
    window.bridge.answer.assert_called_with(False)


def test_tool_finished_error_flashes_the_orb(window):
    flashed = []
    window.orb.flash_error = lambda: flashed.append(True)
    window._on_tool_finished("close_app", True, "failed")
    assert flashed == [True]


def test_deep_learn_start_and_finish_drive_orb_learning_state(window):
    window._on_tool_started("deep_learn", "")
    assert window.orb.state == "learning"
    window._on_tool_finished("deep_learn", False, "done")
    assert window.orb.state == "thinking"


def test_kb_changed_triggers_a_knowledge_panel_refresh(window):
    calls = []
    window._refresh_knowledge_panel = lambda: calls.append(True)
    window._on_kb_changed()
    assert calls == [True]


def test_console_toggle_shows_and_hides(window):
    # isVisible() is compound on ancestor visibility in Qt — the window
    # itself must be shown first, or a visible console would still report
    # False simply because its hidden parent hides it too.
    window.show_and_activate()
    QTest.qWait(50)
    assert not window.console.isVisible()
    window.console.toggle()
    assert window.console.isVisible()
    window.console.toggle()
    assert not window.console.isVisible()


def test_console_submission_routes_to_launch_preset(window, mock_brain):
    window.show_and_activate()
    QTest.qWait(50)
    window.console.input_field.setText("open spotify")
    window.console._on_submit()
    QTest.qWait(300)
    mock_brain.ask.assert_called_with("open spotify")


def test_console_slash_command_does_not_reach_brain(window, mock_brain):
    window.console.input_field.setText("/help")
    window.console._on_submit()
    QTest.qWait(50)
    mock_brain.ask.assert_not_called()
