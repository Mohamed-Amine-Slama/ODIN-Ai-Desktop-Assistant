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
