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


# -- the molecular field inside the orb: a cloud of freely drifting particles
# bonded to their neighbours, steered by the same state the rings answer to ---

import numpy as np  # noqa: E402


def test_the_orb_carries_a_field_that_drifts_as_it_advances(qapp):
    orb = VoiceOrb()
    before = orb.field.positions.copy()
    orb.advance(1 / 30)
    assert not np.array_equal(orb.field.positions, before)


def test_the_field_stays_inside_the_data_ring(qapp):
    """The data ring is the molecule's containment shell — nothing may spill
    across it into the tick ring and launcher labels."""
    orb = VoiceOrb()
    orb.state = "thinking"
    for _ in range(120):
        orb.advance(1 / 30)
    xy, size, _ = orb.field.project(orb.R_FIELD)
    assert np.max(np.linalg.norm(xy, axis=1) + size) <= orb.R_DATA


def test_the_field_freezes_with_the_rings_during_the_error_flash(qapp):
    orb = VoiceOrb()
    orb.flash_error()
    before = orb.field.positions.copy()
    orb.advance(1 / 30)
    assert np.array_equal(orb.field.positions, before)


def test_the_field_holds_still_until_the_core_ignites(qapp):
    """§8: nothing in the orb moves before ignition — and the ignition itself
    kicks the cloud outward, so the molecule blooms with the flash."""
    orb = VoiceOrb()
    orb.boot_frozen = True
    before = orb.field.positions.copy()
    orb.advance(1 / 30)
    assert np.array_equal(orb.field.positions, before)

    orb.boot_frozen = False
    assert orb.field.pulse_level > 0
    orb.advance(1 / 30)
    assert not np.array_equal(orb.field.positions, before)


def test_thinking_agitates_the_field_more_than_idle(qapp):
    orb = VoiceOrb()
    orb.state = "idle"
    orb.advance(1 / 30)
    idle = orb.field.energy

    orb.state = "thinking"
    orb.advance(1 / 30)
    assert orb.field.energy > idle


def test_a_louder_voice_opens_the_field(qapp):
    orb = VoiceOrb()
    orb.state = "listening"
    orb.set_mic_level(0.0)
    orb.advance(1 / 30)
    quiet = orb.field.energy

    orb.set_mic_level(0.9)
    orb.advance(1 / 30)
    assert orb.field.energy > quiet


def test_each_spoken_beat_pulses_the_field(qapp):
    """Speaking's 180ms synthetic word pulse already steps the ring chase; the
    molecule takes the same beat as a kick outward."""
    orb = VoiceOrb()
    orb.state = "speaking"
    orb.advance(0.17)  # just short of the first beat
    assert orb.field.pulse_level == 0.0

    orb.advance(0.02)  # crosses it
    assert orb.field.pulse_level > 0.0


# -- the outer ring as a live spectrum bezel -----------------------------


def test_bezel_reads_the_bands_it_is_given(qapp):
    orb = VoiceOrb()
    orb.set_bands([0.0] * 24 + [1.0] * 24)
    values = orb.bezel_values()

    assert len(values) == orb.BEZEL_SEGMENTS
    assert values[0] < values[-1]  # the ramp survives the resample
    assert all(0.0 <= v <= 1.0 for v in values)


def test_bezel_falls_back_to_a_shimmer_with_no_audio_source(qapp):
    """HUD_SPECTRUM_SOURCE=off, or any run where nothing ever calls
    set_bands: the ring must still breathe rather than sit dead flat."""
    orb = VoiceOrb()
    orb.advance(1 / 60)
    values = orb.bezel_values()

    assert len(values) == orb.BEZEL_SEGMENTS
    assert max(values) > min(values)


def test_a_louder_voice_lifts_the_bezel_while_listening(qapp):
    """Loopback hears nothing while the user is talking, so listening meters
    the mic instead — otherwise the ring goes flat exactly when it matters."""
    orb = VoiceOrb()
    orb.state = "listening"
    orb.set_bands([0.0] * 48)

    orb.set_mic_level(0.0)
    quiet = max(orb.bezel_values())
    orb.set_mic_level(0.9)
    loud = max(orb.bezel_values())

    assert loud > quiet


def test_bands_do_not_leak_into_other_states(qapp):
    """Idle shows the audio, but the mic gain is a listening-only behaviour."""
    orb = VoiceOrb()
    orb.set_bands([0.5] * 48)
    orb.set_mic_level(1.0)
    orb.state = "idle"

    assert max(orb.bezel_values()) < 1.0


def test_the_bezel_paints_in_a_handful_of_batched_calls(qapp):
    """48 bars must not be 48 native stroke calls a frame: they're bucketed by
    level into at most BEZEL_TIERS paths, the same trick the bond layer uses."""
    from PyQt6.QtGui import QColor, QPainter, QPixmap

    class Recorder(QPainter):
        def __init__(self, device):
            super().__init__(device)
            self.paths = 0
            self.lines = 0

        def drawPath(self, path):
            self.paths += 1
            super().drawPath(path)

        def drawLine(self, *args):
            self.lines += 1
            super().drawLine(*args)

    orb = VoiceOrb()
    orb.set_bands([i / 48 for i in range(48)])
    pixmap = QPixmap(440, 440)
    pixmap.fill(QColor(0, 0, 0))
    painter = Recorder(pixmap)

    orb._paint_bezel(painter, QColor(53, 200, 245))
    painter.end()

    assert 0 < painter.paths <= orb.BEZEL_TIERS
    assert painter.lines == 0


# -- the entry animation's orb stage: rings sweeping closed one by one, then
# the molecule condensing into them (driven by ui/hud/boot.py) --------------


def test_the_orb_is_fully_assembled_by_default(qapp):
    """Nothing about normal running should depend on the entry animation."""
    orb = VoiceOrb()
    assert orb.bootReveal == 1.0
    assert orb.field.assemble == 1.0


def test_boot_reveal_condenses_the_molecule_last(qapp):
    orb = VoiceOrb()
    orb.bootReveal = 0.0
    assert orb.field.assemble == 0.0

    orb.bootReveal = 0.8
    partly = orb.field.assemble
    orb.bootReveal = 1.0

    assert 0.0 < partly < orb.field.assemble == 1.0


def test_rings_arrive_in_order_rather_than_all_at_once(qapp):
    """Each ring owns a slice of the reveal, so the orb builds outside-in
    instead of every circle fading up together."""
    orb = VoiceOrb()
    early = orb.ring_reveals(0.2)
    late = orb.ring_reveals(0.7)

    assert early["dash"] > early["data"]        # the outermost lands first
    assert late["data"] > early["data"]
    assert all(0.0 <= v <= 1.0 for v in early.values())


def test_every_reveal_fraction_renders(qapp):
    """A partial wedge clip is easy to get wrong at the 0 and 1 ends."""
    orb = VoiceOrb()
    for reveal in (0.0, 0.05, 0.33, 0.5, 0.9, 1.0):
        orb.bootReveal = reveal
        _render(orb)


def test_the_assembling_molecule_never_paints_outside_the_orb(qapp):
    """Particles fly in from beyond the rings — but not beyond the widget,
    where they'd be sliced off against its rectangle."""
    orb = VoiceOrb()
    for reveal in (0.55, 0.65, 0.75, 0.9, 1.0):
        orb.bootReveal = reveal
        xy, size, _ = orb.field.project(orb.R_FIELD)
        assert np.max(np.linalg.norm(xy, axis=1) + size) <= orb.CENTER
