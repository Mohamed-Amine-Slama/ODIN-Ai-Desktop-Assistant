"""ui/orb.py's ReactorOrb — the small always-on-top desktop orb (OrbWindow,
ui/app_window.py), independent of the full-screen HUD's own VoiceOrb
(ui/hud/voice_orb.py, tested in tests/test_hud_orb.py). Moved here from the
now-deleted tests/test_gui.py, which tested it alongside the legacy
JarvisMainWindow.
"""
import numpy as np
from PyQt6.QtGui import QPixmap

from ui.orb import STATE_STYLE, ReactorOrb


def test_orb_field_agitates_with_state(qapp):
    """The whole point of the swarm: calm when idle, stirred up when working."""
    orb = ReactorOrb()
    orb.stop()

    orb.state = "idle"
    idle = orb.field.energy
    orb.state = "thinking"

    assert orb.field.energy > idle * 2
    orb.deleteLater()


def test_orb_field_drifts_on_every_tick(qapp):
    orb = ReactorOrb()
    orb.stop()
    before = orb.field.positions.copy()

    orb._tick()

    assert not np.array_equal(orb.field.positions, before)
    orb.deleteLater()


def test_orb_renders_in_every_state(qapp):
    """paintEvent runs a lot of geometry; a crash in it takes the HUD down."""
    orb = ReactorOrb()
    orb.resize(240, 240)
    for state in STATE_STYLE:
        orb.state = state
        orb._tick()
        pixmap = QPixmap(240, 240)
        orb.render(pixmap)
    orb.stop()
