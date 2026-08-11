"""The arc reactor: a hand-painted orb that shows what Jarvis is doing.

Everything here is drawn with QPainter rather than assembled from images, so it
scales to any size and recolours with the palette. The particle swarm is the
part that carries meaning: it holds a tight ring when idle and scatters while
Jarvis is thinking or talking, which reads as activity from across the room in
a way a spinner does not.
"""
import math
import random

from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QConicalGradient, QPainter, QPen, QRadialGradient
from PyQt6.QtWidgets import QWidget

# States, and how the swarm behaves in each: (inner, outer) radius band as a
# fraction of the orb radius, angular speed multiplier, and core brightness.
STATE_STYLE = {
    "idle": {"band": (0.74, 0.86), "speed": 0.35, "glow": 0.55, "jitter": 0.10},
    "listening": {"band": (0.60, 0.94), "speed": 0.85, "glow": 0.80, "jitter": 0.35},
    "thinking": {"band": (0.42, 1.18), "speed": 1.90, "glow": 1.00, "jitter": 0.85},
    "speaking": {"band": (0.55, 1.05), "speed": 1.25, "glow": 0.95, "jitter": 0.55},
}

STATE_COLOR = {
    "idle": QColor(56, 189, 248),
    "listening": QColor(52, 211, 153),
    "thinking": QColor(251, 191, 36),
    "speaking": QColor(167, 139, 250),
}

PARTICLE_COUNT = 130
FRAME_MS = 16  # ~60fps


class _Particle:
    """One mote in the swarm. Radius eases toward a per-state target rather than
    snapping, so a state change looks like the swarm reacting, not teleporting."""

    __slots__ = ("angle", "radius", "target", "speed", "size", "phase", "seed")

    def __init__(self, rng: random.Random):
        self.angle = rng.uniform(0, math.tau)
        self.seed = rng.random()
        self.radius = rng.uniform(0.7, 0.9)
        self.target = self.radius
        self.speed = rng.uniform(0.6, 1.6) * (1 if rng.random() < 0.75 else -1)
        self.size = rng.uniform(1.1, 3.0)
        self.phase = rng.uniform(0, math.tau)

    def retarget(self, band: tuple[float, float], rng: random.Random) -> None:
        low, high = band
        self.target = low + (high - low) * self.seed

    def step(self, style: dict, dt: float) -> None:
        self.angle += self.speed * style["speed"] * dt
        self.phase += dt * 2.0
        # Ease 12% of the remaining distance per frame: fast enough to feel
        # reactive, slow enough that the scatter reads as motion.
        self.radius += (self.target - self.radius) * 0.12
        self.radius += math.sin(self.phase) * 0.0035 * style["jitter"] * 10


class ReactorOrb(QWidget):
    """The orb itself. Set .state to one of STATE_STYLE to change its mood."""

    clicked = pyqtSignal()

    def __init__(self, parent=None, seed: int = 7):
        super().__init__(parent)
        self._rng = random.Random(seed)
        self._particles = [_Particle(self._rng) for _ in range(PARTICLE_COUNT)]
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
        band = STATE_STYLE[self._state]["band"]
        for particle in self._particles:
            particle.retarget(band, self._rng)

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
        for particle in self._particles:
            particle.step(style, 0.016)
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
        self._paint_particles(painter, centre, radius, accent)
        self._paint_core(painter, centre, radius, accent)
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

    def _paint_particles(self, painter, centre, radius, accent) -> None:
        painter.setPen(Qt.PenStyle.NoPen)
        for particle in self._particles:
            distance = radius * particle.radius
            point = QPointF(centre.x() + math.cos(particle.angle) * distance,
                            centre.y() + math.sin(particle.angle) * distance)
            # Fade the ones that have drifted furthest out, so the scatter
            # dissolves at its edge instead of ending in a hard rim.
            fade = max(0.0, min(1.0, 1.35 - particle.radius))
            alpha = int(40 + 190 * fade)
            painter.setBrush(QColor(accent.red(), accent.green(), accent.blue(), alpha))
            size = particle.size * (0.6 + 0.4 * fade)
            painter.drawEllipse(point, size, size)

    def _paint_core(self, painter, centre, radius, accent) -> None:
        pulse = 0.88 + 0.12 * math.sin(self._phase * 3.1)
        core_radius = radius * 0.42 * pulse
        core = QRadialGradient(centre, core_radius)
        core.setColorAt(0.0, QColor(255, 255, 255, 240))
        core.setColorAt(0.35, QColor(
            min(255, accent.red() + 90), min(255, accent.green() + 60),
            min(255, accent.blue() + 20), 220))
        core.setColorAt(1.0, QColor(accent.red(), accent.green(), accent.blue(), 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(core)
        painter.drawEllipse(centre, core_radius, core_radius)

        # Triangular reactor plate, the detail that says "arc reactor".
        painter.setPen(QPen(QColor(255, 255, 255, 110), max(1.0, radius * 0.018)))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for turn in (0, math.pi / 3):
            points = [
                QPointF(centre.x() + math.cos(turn + math.tau * i / 3) * radius * 0.3,
                        centre.y() + math.sin(turn + math.tau * i / 3) * radius * 0.3)
                for i in range(3)
            ]
            painter.drawPolygon(*points)
