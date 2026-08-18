"""ui/hud/voice_orb.py — every state renders, error flash is transient,
launcher-ring hit-testing and click routing."""
from PyQt6.QtGui import QPixmap
from PyQt6.QtTest import QTest

from ui.hud.voice_orb import STATES, VoiceOrb


def _render(orb, size=(440, 440)):
    orb.resize(*size)
    pixmap = QPixmap(orb.size())
    orb.render(pixmap)
    return pixmap


def test_every_state_renders_without_raising(qapp):
    orb = VoiceOrb()
    for state in STATES:
        orb.state = state
        orb.set_mic_level(0.5)
        orb.set_system_load(0.4)
        orb.set_learning_progress("Hooks", 0.3)
        for _ in range(3):
            orb.advance(1 / 30)
        _render(orb)


def test_unknown_state_falls_back_to_idle(qapp):
    orb = VoiceOrb()
    orb.state = "not-a-real-state"
    assert orb.state == "idle"


def test_status_changed_emits_on_state_and_subtopic_change(qapp):
    orb = VoiceOrb()
    received = []
    orb.status_changed.connect(received.append)

    orb.state = "learning"
    assert received[-1] == "LEARNING"

    orb.set_learning_progress("Hooks", 0.2)
    assert received[-1] == "LEARNING: Hooks"

    orb.state = "idle"
    assert received[-1] == "IDLE"


def test_flash_error_is_transient(qapp):
    orb = VoiceOrb()
    orb.state = "idle"
    orb.flash_error()
    assert orb._flashing is True
    QTest.qWait(700)  # past the 600ms freeze window
    assert orb._flashing is False


def test_advance_freezes_rings_while_flashing(qapp):
    orb = VoiceOrb()
    orb.flash_error()
    phase_before = orb._phase
    orb.advance(1 / 30)
    assert orb._phase == phase_before  # rings frozen during the flash


def test_launcher_segment_hit_testing_matches_click_emission(qapp):
    orb = VoiceOrb()
    orb.resize(440, 440)
    index = orb._segment_at(220, 220 - 170)  # top of the ring == label index 0
    assert index == 0
    assert orb.LAUNCHER_LABELS[index] == "SYS"

    received = []
    orb.launcher_clicked.connect(received.append)
    from PyQt6.QtCore import QPointF, Qt
    from PyQt6.QtGui import QMouseEvent

    event = QMouseEvent(
        QMouseEvent.Type.MouseButtonPress, QPointF(220, 50),
        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
    )
    orb.mousePressEvent(event)
    assert received == ["SYS"]


def test_click_outside_the_launcher_band_emits_nothing(qapp):
    orb = VoiceOrb()
    orb.resize(440, 440)
    assert orb._segment_at(220, 220) is None  # dead center — inside the core, not the ring


# -- perf: tick ring / outer ring caching (was 120 trig-computed drawLine
# calls and 32 fresh QColor+QPen allocations, both every single frame at
# 30fps) --------------------------------------------------------------

def test_tick_path_is_built_once_and_reused_across_frames(qapp):
    orb = VoiceOrb()
    path = orb._tick_path
    assert path.elementCount() > 0
    for _ in range(5):
        orb.advance(1 / 30)
        _render(orb)
    assert orb._tick_path is path  # never rebuilt — only rotated at paint time


# -- §5.3 per-state ring signatures: listening's sweep arc, speaking's
# radiating chase — previously both states just reused the generic idle
# shimmer, so nothing distinguished them visually beyond color/speed -------

def test_listening_sweep_advances_once_per_second_and_resets_on_entry(qapp):
    orb = VoiceOrb()
    orb.state = "listening"
    assert orb._sweep_phase == 0.0
    orb.advance(0.5)
    assert orb._sweep_phase == 180.0  # §5.3: one full lap per second

    orb.state = "idle"
    orb.advance(1.0)
    assert orb._sweep_phase == 180.0  # only advances while listening

    orb.state = "listening"
    assert orb._sweep_phase == 0.0  # re-entry starts the sweep fresh


def test_speaking_pulse_advances_peak_every_180ms_and_resets_on_entry(qapp):
    orb = VoiceOrb()
    orb.state = "speaking"
    assert orb._speak_peak == 0.0

    orb.advance(0.18)  # exactly one synthetic word-pulse interval
    assert orb._speak_peak == 45.0
    assert orb._speak_elapsed == 0.0

    orb.state = "idle"
    orb.state = "speaking"
    assert orb._speak_peak == 0.0  # re-entry starts the chase fresh


def test_speak_wave_peaks_at_the_current_ripple_origin(qapp):
    orb = VoiceOrb()
    orb.state = "speaking"
    # At t=0 (just after a pulse), the hot spot is the peak itself: brightness
    # falls off monotonically with angular distance away from it.
    at_peak = orb._speak_wave(0.0)
    nearby = orb._speak_wave(20.0)
    far = orb._speak_wave(150.0)
    assert at_peak > nearby > far


def test_outer_ring_uses_chase_wave_only_while_speaking(qapp):
    orb = VoiceOrb()
    orb.state = "speaking"
    for _ in range(3):
        orb.advance(1 / 30)
    _render(orb)  # must not raise — exercises the speaking branch in _paint_outer_ring


def test_outer_ring_pens_are_cached_per_accent_color(qapp):
    orb = VoiceOrb()
    orb.state = "idle"
    _render(orb)
    idle_pens = orb._outer_pens
    assert len(idle_pens) == 32

    _render(orb)
    assert orb._outer_pens is idle_pens  # same accent -> same pen objects reused

    orb.state = "thinking"  # a different ring_accent (tokens.THINKING)
    _render(orb)
    assert orb._outer_pens is not idle_pens  # accent changed -> pens rebuilt once
