"""ui/hud/boot.py — the power-on entry animation.

The whole sequence is one full-screen overlay painted on top of an already
complete HUD, driven by one `progress` property. These tests lean on that:
they set progress directly instead of waiting out six seconds of wall clock.

The load-bearing test here is `test_panels_are_never_touched_during_the_entry`.
The previous implementation hid every panel and re-showed it from a staggered
QTimer.singleShot, which meant any interruption — Esc, a re-summon, the window
closing — left panels to pop back in later, or not at all. Nothing is hidden,
moved, or given a graphics effect any more, so that failure mode cannot occur.
"""
import config
import pytest
from PyQt6.QtWidgets import QLabel, QWidget

from ui.hud.boot import (
    ENTRY_MS,
    REENTRY_MS,
    cancel_entry_animation,
    run_boot_sequence,
    run_reentry_flourish,
)
from ui.hud.voice_orb import VoiceOrb

SAMPLES = (0.0, 0.05, 0.15, 0.3, 0.45, 0.6, 0.75, 0.9, 0.99, 1.0)


@pytest.fixture(autouse=True)
def _motion_on(monkeypatch):
    monkeypatch.setattr(config, "HUD_REDUCED_MOTION", False)


def _make_window(panel_count=5, with_orb=True):
    window = QWidget()
    window.resize(1200, 800)
    window.show()

    panels = []
    for i in range(panel_count):
        panel = QLabel(f"panel {i}", window)
        panel.setGeometry(40 + i * 120, 60 + i * 90, 160, 70)
        panel.show()
        panels.append(panel)
    window._boot_reveal_widgets = panels

    if with_orb:
        orb = VoiceOrb(window)
        orb.setGeometry(480, 250, 300, 300)
        orb.show()
        window.orb = orb
    return window


def test_reduced_motion_skips_the_entry_entirely(qapp, monkeypatch):
    monkeypatch.setattr(config, "HUD_REDUCED_MOTION", True)
    window = _make_window()

    run_boot_sequence(window)

    assert getattr(window, "_entry_overlay", None) is None
    assert window.orb.bootReveal == 1.0
    assert window.orb.boot_frozen is False
    window.deleteLater()


def test_panels_are_never_touched_during_the_entry(qapp):
    """No hiding, no moving, no graphics effects — the overlay occludes them
    instead. An interrupted entry therefore cannot leave a panel missing or
    make one reappear later."""
    window = _make_window()
    before = [(w.isVisible(), w.pos(), w.graphicsEffect()) for w in window._boot_reveal_widgets]

    run_boot_sequence(window)
    overlay = window._entry_overlay
    for progress in SAMPLES:
        overlay.progress = progress
        for widget, (visible, pos, effect) in zip(window._boot_reveal_widgets, before):
            assert widget.isVisible() is visible
            assert widget.pos() == pos
            assert widget.graphicsEffect() is effect is None

    cancel_entry_animation(window)
    window.deleteLater()


def test_a_second_call_does_not_start_a_second_entry(qapp):
    window = _make_window()

    run_boot_sequence(window)
    first = window._entry_overlay
    run_boot_sequence(window)

    assert window._entry_overlay is first
    cancel_entry_animation(window)
    window.deleteLater()


def test_the_orb_assembles_in_step_with_the_overlay(qapp):
    window = _make_window()
    run_boot_sequence(window)
    overlay = window._entry_overlay

    overlay.progress = 0.0
    assert window.orb.bootReveal == 0.0
    assert window.orb.boot_frozen is True

    seen = []
    for progress in SAMPLES:
        overlay.progress = progress
        seen.append(window.orb.bootReveal)

    assert seen == sorted(seen)          # never runs backwards
    assert seen[-1] == 1.0
    cancel_entry_animation(window)
    window.deleteLater()


def test_cancelling_mid_entry_leaves_the_hud_in_its_finished_state(qapp):
    window = _make_window()
    run_boot_sequence(window)
    window._entry_overlay.progress = 0.35

    cancel_entry_animation(window)

    assert getattr(window, "_entry_overlay", None) is None
    orb = window.orb
    assert orb.bootReveal == 1.0
    assert orb.bootScale == 1.0
    assert orb.bootFlash == 0.0
    assert orb.boot_frozen is False
    assert orb.field.assemble == 1.0
    assert all(w.isVisible() for w in window._boot_reveal_widgets)
    window.deleteLater()


def test_reaching_the_end_finishes_the_same_way_cancelling_does(qapp):
    window = _make_window()
    run_boot_sequence(window)

    window._entry_overlay.progress = 1.0

    assert getattr(window, "_entry_overlay", None) is None
    assert window.orb.bootReveal == 1.0
    assert window.orb.boot_frozen is False
    window.deleteLater()


def test_the_overlay_paints_at_every_stage(qapp):
    window = _make_window()
    run_boot_sequence(window)
    overlay = window._entry_overlay

    for progress in SAMPLES[:-1]:
        overlay.progress = progress
        assert not overlay.grab().isNull()

    cancel_entry_animation(window)
    window.deleteLater()


def test_reentry_is_short_and_leaves_the_orb_alone(qapp):
    """Re-summoning after Esc gets its own quick flourish — it must never
    replay the assembly, which would mean the HUD rebuilding itself every
    time you bring it back."""
    window = _make_window()

    run_reentry_flourish(window)
    overlay = window._entry_overlay

    assert overlay.duration_ms == REENTRY_MS < ENTRY_MS
    assert window.orb.bootReveal == 1.0   # untouched: nothing to assemble
    overlay.progress = 0.5
    assert window.orb.bootReveal == 1.0
    assert not overlay.grab().isNull()

    overlay.progress = 1.0
    assert getattr(window, "_entry_overlay", None) is None
    window.deleteLater()


def test_cancelling_without_an_entry_running_is_harmless(qapp):
    window = _make_window()
    cancel_entry_animation(window)
    window.deleteLater()


def test_a_window_with_no_orb_still_completes(qapp):
    window = _make_window(with_orb=False)

    run_boot_sequence(window)
    window._entry_overlay.progress = 1.0

    assert getattr(window, "_entry_overlay", None) is None
    window.deleteLater()
