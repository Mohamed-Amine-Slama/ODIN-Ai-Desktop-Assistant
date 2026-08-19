"""The arc reactor: a hand-painted orb that shows what Jarvis is doing.

Everything here is drawn with QPainter rather than assembled from images, so it
scales to any size and recolours with the palette. The molecular field inside
it is the part that carries meaning: a few hundred particles drifting freely,
bonded to whichever neighbours they're near, calm when idle and stirred up
while Jarvis is thinking or working — activity you can read from across the
room in a way a spinner cannot.

The field itself lives in ui/molecule.py, shared with the full HUD's much
larger VoiceOrb (ui/hud/voice_orb.py); only the size, palette and per-state
energy differ between the two.
"""
import math

from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QConicalGradient, QPainter, QPen, QRadialGradient
from PyQt6.QtWidgets import QWidget

from ui.molecule import MoleculeField

# States, and how the field behaves in each: how agitated the molecule is
# (0..1, see ui/molecule.py) and how bright the core burns.
STATE_STYLE = {
    "idle": {"energy": 0.14, "glow": 0.55},
    "listening": {"energy": 0.45, "glow": 0.80},
    "thinking": {"energy": 0.90, "glow": 1.00},
    # A hands-on-the-machine state, distinct from "thinking" (waiting on the
    # model) — the most agitated of the set, so a tool call reads as a sharp
    # burst of activity rather than more of the same churn.
    "acting": {"energy": 1.00, "glow": 1.00},
    "speaking": {"energy": 0.60, "glow": 0.95},
}

STATE_COLOR = {
    "idle": QColor(34, 211, 238),
    "listening": QColor(52, 211, 153),
    "thinking": QColor(251, 191, 36),
    "acting": QColor(248, 113, 113),
    "speaking": QColor(167, 139, 250),
}

PARTICLE_COUNT = 130
FIELD_SPAN = 1.0  # the molecule's shell, just inside the thin sheen ring
FRAME_MS = 16  # ~60fps


class ReactorOrb(QWidget):
    """The orb itself. Set .state to one of STATE_STYLE to change its mood."""

    clicked = pyqtSignal()

    def __init__(self, parent=None, seed: int = 7):
        super().__init__(parent)
        self.field = MoleculeField(PARTICLE_COUNT, seed=seed)
        self._state = "idle"
        self._phase = 0.0
        self._level = 0.0  # 0..1 audio-ish energy, drives the core pulse
        self.setMinimumSize(120, 120)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(FRAME_MS)
        self._retarget()

    # -- state -------------------------------------------------------------

    @property
    def state(self) -> str:
        return self._state

    @state.setter
    def state(self, value: str) -> None:
        value = value if value in STATE_STYLE else "idle"
        if value == self._state:
            return
        self._state = value
        self._retarget()

    def _retarget(self) -> None:
        self.field.set_energy(STATE_STYLE[self._state]["energy"])

    def stop(self) -> None:
        """Halt the animation. Called when the orb is hidden so an idle Jarvis
        isn't repainting 60 times a second forever."""
        self._timer.stop()

    def start(self) -> None:
        if not self._timer.isActive():
            self._timer.start(FRAME_MS)

    # -- animation ---------------------------------------------------------

    def _tick(self) -> None:
        style = STATE_STYLE[self._state]
        self._phase += 0.016
        target_level = style["glow"]
        self._level += (target_level - self._level) * 0.08
        self.field.advance(FRAME_MS / 1000)
        self.update()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)

    # -- painting ----------------------------------------------------------

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        side = min(self.width(), self.height())
        centre = QPointF(self.width() / 2, self.height() / 2)
        radius = side / 2 * 0.62
        accent = STATE_COLOR[self._state]

        self._paint_halo(painter, centre, radius, accent)
        self._paint_rings(painter, centre, radius, accent)
        self._paint_core(painter, centre, radius, accent)
        self.field.paint(painter, centre, radius * FIELD_SPAN, accent)
        painter.end()

    def _paint_halo(self, painter, centre, radius, accent) -> None:
        pulse = 1.0 + 0.06 * math.sin(self._phase * 2.2)
        outer = radius * 1.9 * pulse
        glow = QRadialGradient(centre, outer)
        glow.setColorAt(0.0, QColor(accent.red(), accent.green(), accent.blue(), int(70 * self._level)))
        glow.setColorAt(0.45, QColor(accent.red(), accent.green(), accent.blue(), int(26 * self._level)))
        glow.setColorAt(1.0, QColor(accent.red(), accent.green(), accent.blue(), 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(glow)
        painter.drawEllipse(centre, outer, outer)

    def _paint_rings(self, painter, centre, radius, accent) -> None:
        painter.setBrush(Qt.BrushStyle.NoBrush)

        # Segmented outer ring, counter-rotating against the swarm.
        rect = QRectF(centre.x() - radius * 1.22, centre.y() - radius * 1.22,
                      radius * 2.44, radius * 2.44)
        pen = QPen(QColor(accent.red(), accent.green(), accent.blue(), 150), max(1.0, radius * 0.035))
        pen.setCapStyle(Qt.PenCapStyle.FlatCap)
        painter.setPen(pen)
        offset = -self._phase * 26
        for index in range(8):
            start = offset + index * 45
            painter.drawArc(rect, int(start * 16), int(28 * 16))

        # Thin continuous ring with a conical sheen, rotating the other way.
        sheen = QConicalGradient(centre, -self._phase * 55 % 360)
        sheen.setColorAt(0.0, QColor(accent.red(), accent.green(), accent.blue(), 30))
        sheen.setColorAt(0.25, QColor(255, 255, 255, 190))
        sheen.setColorAt(0.5, QColor(accent.red(), accent.green(), accent.blue(), 30))
        sheen.setColorAt(1.0, QColor(accent.red(), accent.green(), accent.blue(), 30))
        pen = QPen(sheen, max(1.0, radius * 0.02))
        painter.setPen(pen)
        painter.drawEllipse(centre, radius * 1.05, radius * 1.05)

        # Inner bezel: the ring of coils that makes it read as a reactor.
        painter.setPen(QPen(QColor(accent.red(), accent.green(), accent.blue(), 90), max(1.0, radius * 0.015)))
        painter.drawEllipse(centre, radius * 0.66, radius * 0.66)
        for index in range(10):
            angle = math.tau * index / 10 + self._phase * 0.35
            inner = QPointF(centre.x() + math.cos(angle) * radius * 0.5,
                            centre.y() + math.sin(angle) * radius * 0.5)
            painter.setBrush(QColor(accent.red(), accent.green(), accent.blue(), 130))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(inner, radius * 0.055, radius * 0.055)
        painter.setBrush(Qt.BrushStyle.NoBrush)

    def _paint_core(self, painter, centre, radius, accent) -> None:
        pulse = 0.88 + 0.12 * math.sin(self._phase * 3.1)
        core_radius = radius * 0.34 * pulse  # a nucleus, not the whole show
        core = QRadialGradient(centre, core_radius)
        core.setColorAt(0.0, QColor(255, 255, 255, 240))
        core.setColorAt(0.35, QColor(
            min(255, accent.red() + 90), min(255, accent.green() + 60),
            min(255, accent.blue() + 20), 220))
        core.setColorAt(1.0, QColor(accent.red(), accent.green(), accent.blue(), 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(core)
        painter.drawEllipse(centre, core_radius, core_radius)
