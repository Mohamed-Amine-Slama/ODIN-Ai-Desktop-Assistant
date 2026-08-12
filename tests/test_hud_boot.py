"""ui/hud/boot.py — the full staged startup sequence (ODIN-HUD.md §8):
hairline -> iris wipe -> staggered panel reveal -> orb scale-in -> core
ignition. Uses a lightweight standalone window rather than the full
OdinHudWindow (Brain/Session/UiBridge and 15 real zone panels) so this
stays fast and isolated."""
import config
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QLabel, QWidget

from ui.hud.boot import (
    FLASH_MS, HAIRLINE_MS, IRIS_MS, ORB_REVEAL_MS, PANEL_REVEAL_MS,
    PANEL_STAGGER_MS, _BootCover, run_boot_sequence,
)
from ui.hud.voice_orb import VoiceOrb


def _make_window(panel_count=4, with_orb=True):
    window = QWidget()
    window.resize(800, 600)
    window.show()

    panels = []
    for i in range(panel_count):
        panel = QLabel(f"panel {i}", window)
        panel.setGeometry(20 + i * 60, 20 + i * 40, 100, 50)
        panel.show()
        panels.append(panel)
    window._boot_reveal_widgets = panels

    if with_orb:
        orb = VoiceOrb(window)
        orb.setGeometry(300, 200, 220, 220)
        orb.show()
        window.orb = orb

    QTest.qWaitForWindowExposed(window)
    return window


def test_reduced_motion_skips_the_sequence_entirely(qapp, monkeypatch):
    monkeypatch.setattr(config, "HUD_REDUCED_MOTION", True)
    window = _make_window()

    run_boot_sequence(window)

    assert not hasattr(window, "_boot_anims")
    assert window.orb.boot_frozen is False
    assert window.orb.graphicsEffect() is None
    window.deleteLater()


def test_cover_paints_without_raising_across_reveal_states(qapp):
    cover = _BootCover()
    cover.resize(400, 300)
    for line_frac in (0.0, 0.5, 1.0):
        cover.lineFrac = line_frac
        assert not cover.grab().isNull()
    for reveal_frac in (0.0, 0.3, 0.7, 1.0):
        cover.revealFrac = reveal_frac
        assert not cover.grab().isNull()


def test_full_sequence_ends_with_everything_cleaned_up_and_orb_unfrozen(qapp, monkeypatch):
    monkeypatch.setattr(config, "HUD_REDUCED_MOTION", False)
    window = _make_window(panel_count=6)
    orb = window.orb

    run_boot_sequence(window)

    # Immediately after kicking off: orb held still, scaled down, invisible.
    assert orb.boot_frozen is True
    assert orb.bootScale == 0.85
    assert orb.graphicsEffect() is not None

    last_stagger = 5 * PANEL_STAGGER_MS  # 6 panels -> indexes 0..5
    total_ms = HAIRLINE_MS + IRIS_MS + last_stagger + PANEL_REVEAL_MS + ORB_REVEAL_MS + FLASH_MS
    QTest.qWait(total_ms + 500)  # generous margin past the last stage

    assert window._boot_anims is None
    assert orb.boot_frozen is False
    assert orb.bootScale == 1.0
    assert orb.bootFlash == 0.0
    assert orb.graphicsEffect() is None
    for panel in window._boot_reveal_widgets:
        assert panel.graphicsEffect() is None

    window.deleteLater()


def test_panels_are_staggered_outward_from_center(qapp, monkeypatch):
    """A panel far from center must still be earlier in its reveal than
    one right at the center, regardless of the order they're listed in —
    _reveal_panels sorts by distance itself."""
    monkeypatch.setattr(config, "HUD_REDUCED_MOTION", False)
    window = QWidget()
    window.resize(800, 600)
    window.show()

    near = QLabel("near", window)
    near.setGeometry(390, 290, 20, 20)  # right at center
    near.show()
    far = QLabel("far", window)
    far.setGeometry(0, 0, 20, 20)  # top-left corner, far from center
    far.show()
    window._boot_reveal_widgets = [far, near]  # deliberately out of distance order

    QTest.qWaitForWindowExposed(window)

    run_boot_sequence(window)
    QTest.qWait(HAIRLINE_MS + IRIS_MS + PANEL_STAGGER_MS + 60)

    near_opacity = near.graphicsEffect().opacity() if near.graphicsEffect() else 1.0
    far_opacity = far.graphicsEffect().opacity() if far.graphicsEffect() else 1.0
    assert near_opacity > far_opacity

    window.deleteLater()


def test_window_deleted_mid_sequence_does_not_raise(qapp, monkeypatch):
    """The staggered reveal's QTimer.singleShot callbacks and animation
    .finished signals fire well after run_boot_sequence() returns — if the
    window (and its panels) are torn down first, those callbacks must not
    raise into the event loop."""
    monkeypatch.setattr(config, "HUD_REDUCED_MOTION", False)
    window = _make_window(panel_count=5)
    run_boot_sequence(window)

    QTest.qWait(50)
    window.deleteLater()
    QTest.qWait(50)  # let deleteLater() actually run

    # Pump the event loop past every remaining stage's timers/animations —
    # must not raise or print an unhandled exception.
    last_stagger = 4 * PANEL_STAGGER_MS
    total_ms = HAIRLINE_MS + IRIS_MS + last_stagger + PANEL_REVEAL_MS + ORB_REVEAL_MS + FLASH_MS
    QTest.qWait(total_ms + 500)
