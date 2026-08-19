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
    # show_and_activate() now kicks off a real VoiceSetupWorker/
    # VoiceListenWorker QThread on first show (voice-first boot).
    # VoiceLoopController.shutdown() joins both, drains any signal already
    # queued for the GUI event loop (so it can't fire later against a
    # window this fixture already tore down), and blocks any new worker
    # from spawning mid-drain.
    win.voice.shutdown()
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
        "ruler", "cpu_hero", "ram_hero", "gauge_cpu", "orb", "transcript_odin",
        "clock_label", "weather_temp", "temp_arc_cpu", "net_ip", "spectrum",
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


def test_voice_heard_is_a_no_op_while_a_turn_is_in_flight(window, mock_brain, mock_session):
    """Mirrors _launch_preset's guard: without it, a voice-heard turn and a
    dock/console-triggered turn could both start a BrainWorker, racing on
    Brain's shared history and the confirmation bridge's single Event."""
    # A sentinel here (mirrors test_switch_to_voice_is_a_no_op_...) makes
    # show_and_activate()'s own boot-time voice.switch_to_voice() a no-op,
    # so it can't race this test's manual mode/heard setup with a real
    # VoiceSetupWorker/VoiceListenWorker thread reading a MagicMock session.
    window.voice._loop_worker = MagicMock()
    window.show_and_activate()
    mock_session.mode = "voice"
    window._launch_preset("typed command")  # sets current_worker
    calls_after_first = mock_brain.ask.call_count

    window._on_voice_heard("something spoken")

    assert mock_brain.ask.call_count == calls_after_first
    QTest.qWait(300)


def test_voice_heard_starts_a_turn_when_idle(window, mock_brain, mock_session):
    window.voice._loop_worker = MagicMock()  # see the note in the test above
    window.show_and_activate()
    mock_session.mode = "voice"
    window._on_voice_heard("hello")
    QTest.qWait(300)
    mock_brain.ask.assert_called_with("hello")


def test_reset_is_refused_while_a_turn_is_in_flight(window, mock_brain):
    """Brain.ask() snapshots self.history at the start of a turn and only
    writes it back at the end — a reset while a turn is in flight would
    appear to work, then be silently overwritten when that turn finishes."""
    window.show_and_activate()
    window._launch_preset("first command")  # sets current_worker

    window.trigger_reset()

    mock_brain.reset.assert_not_called()
    assert "wait" in window.console._scrollback.text().lower()
    QTest.qWait(300)


def test_reset_runs_when_idle(window, mock_brain):
    window.show_and_activate()
    window.trigger_reset()
    mock_brain.reset.assert_called_once()


def test_switch_to_voice_is_a_no_op_while_the_voice_loop_is_already_running(window):
    """Reachable via '/mode voice' typed twice, not just the dock toggle
    (which already checks session.mode) — without this guard, a second call
    would overwrite _loop_worker with a new VoiceListenWorker while the
    first is still running, leaking its thread and duplicating every
    transcription."""
    sentinel = MagicMock()
    window.voice._loop_worker = sentinel

    window.voice.switch_to_voice()

    assert window.voice.setup_worker is None
    assert window.voice._loop_worker is sentinel


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


def test_console_echoes_the_reply_it_triggered(window, mock_brain):
    # The transcript ticker (zone E) is ODIN's primary output, but the
    # console is a self-contained surface too — someone watching only the
    # console (not zone E, elsewhere on the HUD) must still see the reply
    # land where they typed the question.
    window.show_and_activate()
    QTest.qWait(50)
    window.console.input_field.setText("open spotify")
    window.console._on_submit()
    QTest.qWait(300)
    assert "Hello from ODIN!" in window.console._scrollback.text()


def test_each_animation_tick_feeds_the_spectrum_bands_to_the_orb(window):
    """The orb's bezel and zone K's analyser share one FFT per tick."""
    from ui.hud.spectrum import BAR_COUNT

    received = []
    window.orb.set_bands = received.append

    window.telemetry_view.advance_animation()

    assert received and len(received[0]) == BAR_COUNT


def test_the_entry_plays_once_and_re_summoning_gets_the_short_flourish(window, monkeypatch):
    """The assembly is a launch event, not a summon event — bringing the HUD
    back after Esc must never rebuild it."""
    import config
    from ui.hud.boot import ENTRY_MS, REENTRY_MS

    monkeypatch.setattr(config, "HUD_REDUCED_MOTION", False)

    window.show_and_activate()
    first = window._entry_overlay
    assert first is not None
    assert first.duration_ms == ENTRY_MS

    first.finish()
    window.show_and_activate()

    assert window._entry_overlay is not None
    assert window._entry_overlay.duration_ms == REENTRY_MS


def test_dismissing_mid_entry_cancels_it_and_leaves_the_orb_whole(window, monkeypatch):
    import config

    monkeypatch.setattr(config, "HUD_REDUCED_MOTION", False)
    window.show_and_activate()
    assert window._entry_overlay is not None

    window.dismiss()

    assert window._entry_overlay is None
    assert window.orb.bootReveal == 1.0
    assert window.orb.boot_frozen is False


# -- the rebuilt side panels ------------------------------------------------
#
# These assert on what a reading actually puts on screen, not just that the
# presenter ran: the panels' whole job is turning one TelemetryFrame into
# something scannable, and every one of these fields was either invisible or
# absent before the rebuild.


def _frame(**overrides):
    from ui.hud.telemetry import (
        BatterySample, CpuSample, DiskIoSample, DiskSample, MemSample,
        NetSample, NicSample, TelemetryFrame, ThermalSample,
    )

    base = dict(
        ts=1000.0,
        cpu=CpuSample(
            percent=41.0, per_core=[30.0] * 8, freq_mhz=2699, processes=386,
            top=[("chrome.exe", 18.4), ("python.exe", 9.1), ("code.exe", 4.0)],
        ),
        mem=MemSample(used_gb=21.4, total_gb=32.0, percent=67.0, swap_percent=18.0),
        disks=[DiskSample(mount="C:", used_gb=447.0, total_gb=460.0, percent=97.2)],
        disk_io=DiskIoSample(read_mbs=5.9, write_mbs=10.3),
        net=NetSample(
            up_kbs=938.7, down_kbs=2463.7, total_up_gb=12.4, total_down_gb=88.1,
            ip="192.168.1.129",
            nics=[NicSample(name="Wi-Fi", up_kbs=900.0, down_kbs=2400.0),
                  NicSample(name="Ethernet", up_kbs=38.7, down_kbs=63.7)],
        ),
        battery=BatterySample(percent=76.0, plugged=False, secs_left=4500),
        thermals=ThermalSample(cpu_c=63.0, gpu_c=60.0, gpu_load=11.0,
                               gpu_vram_percent=44.0, fan_rpm=1915.0),
        uptime_sec=98_400.0,
    )
    base.update(overrides)
    return TelemetryFrame(**base)


def test_cpu_panel_shows_a_hero_value_history_and_top_processes(window):
    window.telemetry_view.render_frame(_frame())

    assert window.cpu_graph.samples == [41.0]
    assert window.cpu_procs.rows[0] == ("chrome.exe", 18.4)
    assert len(window.cpu_procs.rows) == 1        # the heaviest one, not a table


def test_memory_panel_shows_the_headline_figure_and_its_history(window):
    window.telemetry_view.render_frame(_frame())

    assert window.ram_graph.samples == [67.0]
    assert "21.4/32" in window.ram_hero._caption


def test_network_panel_shows_the_rate_and_its_history(window):
    window.telemetry_view.render_frame(_frame())

    assert window.net_graph_down.samples == [2463.7]
    assert window.net_ip._value == "192.168.1.129"


def test_storage_panel_reports_io_on_one_line(window):
    window.telemetry_view.render_frame(_frame())

    assert "5.9" in window.disk_io._value and "10.3" in window.disk_io._value


def test_thermals_drive_arcs_and_the_battery_meter(window):
    window.telemetry_view.render_frame(_frame())

    assert window.temp_arc_cpu._value == 63.0
    assert window.temp_arc_gpu._value == 60.0
    assert window.battery.caption == "1H 15M LEFT"
    assert not hasattr(window, "temp_arc_load")   # the GPU gauge by the orb already says this
    assert not hasattr(window, "temp_fan")


def test_missing_sensors_read_as_unavailable_rather_than_zero(window):
    """§10's never-fabricate rule: an arc with no backend must not sit at the
    bottom of its scale looking like a real reading of zero."""
    from ui.hud.telemetry import ThermalSample

    window.telemetry_view.render_frame(_frame(
        thermals=ThermalSample(cpu_c=None, gpu_c=None, gpu_load=None,
                               gpu_vram_percent=None, fan_rpm=None),
    ))

    assert window.temp_arc_cpu._value is None
    assert window.temp_arc_gpu._value is None


def test_the_forecast_that_was_being_thrown_away_is_drawn(window):
    from ui.hud.weather import WeatherSample

    window.telemetry_view.render_weather(WeatherSample(
        temp_c=24.0, condition="Partly cloudy", humidity=58.0, feels_like_c=25.0,
        wind_kph=11.0, pressure_mb=1014.0, sunrise="06:12", sunset="19:44",
        forecast=[("2026-08-20", 19.0, 29.0), ("2026-08-21", 20.0, 31.0)],
    ))

    assert len(window.weather_forecast.days) == 2


def test_the_hud_animates_a_bounded_number_of_instruments(window):
    """Every registered instrument repaints while it eases, so this count is
    the per-frame cost of the side panels. Kept deliberately small — the
    panels were trimmed back to one headline figure and one history each."""
    assert len(window._instruments) <= 12


def test_every_panel_instrument_advances_with_the_shared_loop(window):
    """One loop drives them all (§10) — a widget left out would freeze at
    whatever it happened to be showing."""
    window.telemetry_view.render_frame(_frame())
    before = window.cpu_hero.displayed

    for _ in range(30):
        window.telemetry_view.advance_animation()

    assert window.cpu_hero.displayed > before
    assert window.ram_hero.displayed > 0.0
    assert window.temp_arc_cpu.fraction > 0.0


def test_uptime_is_reported_once_in_the_header(window):
    window.telemetry_view.render_frame(_frame(uptime_sec=98_400.0))
    window.telemetry_view.render_clock()

    assert "1D 3H 20M" in window.uptime_label.text()
    assert not hasattr(window, "clock_day")     # the clock panel is a clock


# -- the dock's states ------------------------------------------------------


def test_the_dock_dims_while_a_turn_is_in_flight(window, mock_brain):
    """_launch_preset drops clicks while a turn runs. The dock used to look
    completely live through all of it."""
    window.show_and_activate()
    assert all(b.isEnabled() for b in window.dock.buttons)

    window._launch_preset("first command")
    # The launchers dim; the locally handled toggles stay live (see
    # test_local_toggles_stay_clickable_while_a_turn_is_in_flight).
    assert not any(b.isEnabled() for b in window.dock.buttons if b.dispatches)

    # Wait on the turn actually ending rather than on a fixed sleep — the
    # voice workers share this event loop and the timing isn't fixed.
    for _ in range(60):
        if window.current_worker is None:
            break
        QTest.qWait(50)

    assert window.current_worker is None
    assert all(b.isEnabled() for b in window.dock.buttons)


def test_launching_from_the_dock_flashes_the_button_that_did_it(window):
    window.show_and_activate()
    button = next(b for b in window.dock.buttons if b.glyph == "WEB")

    window._on_dock_clicked("WEB", "open my default browser")

    assert button.launch_flash > 0.0
    QTest.qWait(300)


def test_the_console_button_lights_while_the_console_is_open(window):
    button = next(b for b in window.dock.buttons if b.glyph == "CON")
    assert button.is_active is False

    window._on_dock_clicked("CON", None)
    assert button.is_active is True

    window._on_dock_clicked("CON", None)
    assert button.is_active is False


def test_the_hand_button_follows_gesture_control_state(window):
    """Hand control runs off-screen; the dock is the only place its state is
    visible at a glance."""
    button = next(b for b in window.dock.buttons if b.glyph == "HAND")

    window._on_gesture_state("running", "hand control on")
    assert button.is_active is True

    window._on_gesture_state("stopped", "hand control off")
    assert button.is_active is False


def test_local_toggles_stay_clickable_while_a_turn_is_in_flight(window):
    """Reproduces the reported bug: hand control could not be activated. The
    dock dimmed every button during a turn, including the three that are
    handled locally and never reach the brain at all."""
    window._process_user_turn("hello")
    assert window.current_worker is not None

    assert not window.dock.button("EXP").isEnabled()    # launchers do dim
    for glyph in ("SET", "CON", "HAND"):
        assert window.dock.button(glyph).isEnabled(), f"{glyph} was disabled mid-turn"

    # Click it against a stub controller: the real one opens the webcam, and
    # a test must not leave a capture thread running behind the suite.
    import ui.hud.window as window_module

    class _StubController:
        def __init__(self):
            self.started = False

        def is_running(self):
            return self.started

        def start(self):
            self.started = True
            return "starting"

        def stop(self):
            self.started = False
            return "stopped"

    stub = _StubController()
    original = window_module.get_gesture_controller
    window_module.get_gesture_controller = lambda: stub
    try:
        window.dock.button("HAND").click()
    finally:
        window_module.get_gesture_controller = original

    assert stub.started is True

    for _ in range(60):
        if window.current_worker is None:
            break
        QTest.qWait(50)
