"""ui/orb.py's ReactorOrb — the small always-on-top desktop orb (OrbWindow,
ui/app_window.py), independent of the full-screen HUD's own VoiceOrb
(ui/hud/voice_orb.py, tested in tests/test_hud_orb.py). Moved here from the
now-deleted tests/test_gui.py, which tested it alongside the legacy
JarvisMainWindow.
"""
from PyQt6.QtGui import QPixmap

from ui.orb import STATE_STYLE, ReactorOrb


def test_orb_swarm_reacts_to_state(qapp):
    """The whole point of the swarm: tight when idle, scattered when working."""
    orb = ReactorOrb()
    orb.stop()

    orb.state = "idle"
    idle_spread = max(p.target for p in orb._particles) - min(p.target for p in orb._particles)
    orb.state = "thinking"
    thinking_spread = max(p.target for p in orb._particles) - min(p.target for p in orb._particles)

    assert thinking_spread > idle_spread * 2
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
