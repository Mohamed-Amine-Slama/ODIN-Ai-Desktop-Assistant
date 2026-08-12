"""VoiceOrb — ODIN-HUD.md §5.3, the HUD's one signature, elaborate element.
Built fresh rather than extending ui/orb.py's ReactorOrb: that widget drives
the small always-on-top ambient OrbWindow (kept, unchanged, per the HUD
rebuild's own scope), repaints continuously as a lightweight background
presence, and answers to a different state set and a different visual
language (a particle swarm, not concentric rings + a launcher ring + a
triangular reactor core). They share only the state-color mapping in
ui/hud/tokens.py.

Not self-timed: `advance(dt)` is called by the owning window's one shared
~30fps loop (ODIN-HUD.md §10's "one shared requestAnimationFrame loop, not
one per widget") rather than this widget running its own QTimer.
"""
from __future__ import annotations

import math

from PyQt6.QtCore import QEasingCurve, QPointF, QPropertyAnimation, QRectF, Qt, QTimer, pyqtProperty, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPen, QRadialGradient
from PyQt6.QtWidgets import QWidget

from . import tokens

STATES = ("idle", "listening", "thinking", "speaking", "learning", "error")

_STATUS_LABELS = {
    "idle": "IDLE",
    "listening": "LISTENING",
    "thinking": "PROCESSING",
    "speaking": "SPEAKING",
    "error": "ERROR",
}


def _lerp_color(a: QColor, b: QColor, t: float) -> QColor:
    t = max(0.0, min(1.0, t))
    return QColor(
        int(a.red() + (b.red() - a.red()) * t),
        int(a.green() + (b.green() - a.green()) * t),
        int(a.blue() + (b.blue() - a.blue()) * t),
    )


class VoiceOrb(QWidget):
    launcher_clicked = pyqtSignal(str)  # one of LAUNCHER_LABELS
    status_changed = pyqtSignal(str)    # display text for the caption beneath the orb (§6.4)

    REF = 440.0
    CENTER = 220.0
    R_OUTER = 200.0
    R_LAUNCHER = 170.0
    R_TICK = 145.0
    R_DATA = 118.0
    R_CORE = 78.0

    LAUNCHER_LABELS = ("SYS", "FILES", "WEB", "CODE", "MUSIC", "VOL", "LEARN", "PWR")  # §6.7

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(220, 220)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        self._state = "idle"
        self._phase = 0.0       # outer-ring rotation, degrees
        self._tick_phase = 0.0  # tick-ring counter-rotation, degrees
        self._breathe_phase = 0.0  # seconds accumulator for sinusoidal motion
        self._mic_level = 0.0
        self._data_value = 0.0
        self._learning_subtopic = ""
        self._hover_index: int | None = None
        self._flashing = False

        # Boot sequence hooks (ui/hud/boot.py): frozen holds the rings still
        # until the core-ignition beat, per ODIN-HUD.md §8 ("rings begin
        # rotating" only once the core ignites, not from frame one).
        # bootScale/bootFlash are separate from the state-driven properties
        # above so the boot animation never has to fight the idle breathing
        # or state-color logic — it's a pure multiply/blend on top.
        self.boot_frozen = False
        self._boot_scale = 1.0
        self._boot_flash = 0.0

        self._data_anim = QPropertyAnimation(self, b"dataValue", self)
        self._data_anim.setDuration(tokens.DUR_VAL)
        self._data_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        self._error_timer = QTimer(self)
        self._error_timer.setSingleShot(True)
        self._error_timer.timeout.connect(self._clear_error_flash)

        # Perf: the tick ring is 120 hairlines recomputed from trig every
        # single frame at 30fps in the original implementation — by far the
        # most expensive thing this widget painted, since each is its own
        # native draw call. The geometry is fixed (only overall rotation
        # changes), so it's built exactly once here and just rotated with a
        # transform at paint time — 120 drawLine calls become one drawPath.
        self._tick_path = self._build_tick_path()

        # Perf: the outer ring's 32 segments each need a fresh alpha every
        # frame (a breathing sine wave), but the QColor/QPen objects
        # themselves don't need to be reconstructed from scratch each
        # time — they're built once per accent color and mutated in place.
        self._outer_pens: list[QPen] = []
        self._outer_pens_accent: tuple[int, int, int] | None = None

    # -- state ---------------------------------------------------------

    @property
    def state(self) -> str:
        return self._state

    @state.setter
    def state(self, value: str) -> None:
        value = value if value in STATES else "idle"
        if value == self._state:
            return
        self._state = value
        self.status_changed.emit(self._status_text())
        self.update()

    def _status_text(self) -> str:
        if self._state == "learning":
            return f"LEARNING: {self._learning_subtopic}" if self._learning_subtopic else "LEARNING"
        return _STATUS_LABELS.get(self._state, self._state.upper())

    def set_mic_level(self, level: float) -> None:
        """0..1 smoothed RMS amplitude, ~20Hz while state == 'listening'."""
        self._mic_level = max(0.0, min(1.0, level))
        self.update()

    def set_system_load(self, fraction: float) -> None:
        """0.5*cpu + 0.3*ram + 0.2*disk_io, from the latest TelemetryFrame —
        the data ring's normal (non-learning, non-thinking) source (§5.3)."""
        self._animate_data_to(fraction)

    def set_learning_progress(self, subtopic: str, fraction: float) -> None:
        changed = subtopic != self._learning_subtopic
        self._learning_subtopic = subtopic
        self._animate_data_to(fraction)
        if changed and self._state == "learning":
            self.status_changed.emit(self._status_text())

    def flash_error(self) -> None:
        """A transient flash, not a persistent mode (§5.3: 'flashes crit
        twice, 200ms; rings freeze for 600ms') — triggered on a failed tool
        call, layered on top of whatever `state` already is."""
        self._flashing = True
        self._error_timer.start(600)
        self.update()

    def _clear_error_flash(self) -> None:
        self._flashing = False
        self.update()

    def _animate_data_to(self, fraction: float) -> None:
        fraction = max(0.0, min(1.0, fraction))
        self._data_anim.stop()
        self._data_anim.setStartValue(self._data_value)
        self._data_anim.setEndValue(fraction)
        self._data_anim.start()

    def getDataValue(self) -> float:
        return self._data_value

    def setDataValue(self, value: float) -> None:
        self._data_value = value
        self.update()

    dataValue = pyqtProperty(float, getDataValue, setDataValue)

    def getBootScale(self) -> float:
        return self._boot_scale

    def setBootScale(self, value: float) -> None:
        self._boot_scale = value
        self.update()

    bootScale = pyqtProperty(float, getBootScale, setBootScale)

    def getBootFlash(self) -> float:
        return self._boot_flash

    def setBootFlash(self, value: float) -> None:
        self._boot_flash = value
        self.update()

    bootFlash = pyqtProperty(float, getBootFlash, setBootFlash)

    # -- driven by the shared animation loop (ODIN-HUD.md §10) --------------

    def advance(self, dt: float) -> None:
        # Held still by the boot sequence until the core-ignition beat
        # (ODIN-HUD.md §8: "rings begin rotating" there, not from frame
        # one) — the breathing/flash phase still needs to advance so the
        # ignition flash itself can animate smoothly.
        if not self.boot_frozen:
            if not self._flashing:
                self._phase = (self._phase + dt * self._ring_speed()) % 360.0
                self._tick_phase = (self._tick_phase - dt * 4.0) % 360.0
        self._breathe_phase += dt
        self.update()

    def _ring_speed(self) -> float:
        if self._state == "listening":
            return 360.0 / 24.0  # §5.3: speeds to a 24s revolution
        return 360.0 / 60.0      # §5.3: base 60s revolution

    # -- launcher ring hit-testing --------------------------------------

    def _segment_at(self, x: float, y: float) -> int | None:
        side = min(self.width(), self.height())
        if side <= 0:
            return None
        scale = side / self.REF
        cx = (self.width() - side) / 2 + self.CENTER * scale
        cy = (self.height() - side) / 2 + self.CENTER * scale
        dx, dy = (x - cx) / scale, (y - cy) / scale
        radius = math.hypot(dx, dy)
        if not (self.R_LAUNCHER - 22 <= radius <= self.R_LAUNCHER + 22):
            return None
        angle_deg = math.degrees(math.atan2(-dy, dx)) % 360
        return round((90 - angle_deg) / 45) % 8

    def mouseMoveEvent(self, event) -> None:
        pos = event.position()
        index = self._segment_at(pos.x(), pos.y())
        if index != self._hover_index:
            self._hover_index = index
            self.setCursor(
                Qt.CursorShape.PointingHandCursor if index is not None else Qt.CursorShape.ArrowCursor
            )
            self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:
        if self._hover_index is not None:
            self._hover_index = None
            self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            pos = event.position()
            index = self._segment_at(pos.x(), pos.y())
            if index is not None:
                self.launcher_clicked.emit(self.LAUNCHER_LABELS[index])
        super().mousePressEvent(event)

    # -- core look -------------------------------------------------------

    def _core_radius(self) -> float:
        if self._state == "listening":
            return self.R_CORE + self._mic_level * 22  # §5.3: 78 + amp*22
        if self._flashing:
            return self.R_CORE
        breathe = 0.5 + 0.5 * math.sin(self._breathe_phase * (2 * math.pi / 4.0))
        if self._state == "idle":
            return self.R_CORE * (1.0 + 0.04 * breathe)
        if self._state == "speaking":
            return self.R_CORE * (1.0 + 0.10 * breathe)
        return self.R_CORE

    def _core_color(self) -> QColor:
        if self._flashing:
            blink = abs(math.sin(self._breathe_phase * 2 * math.pi * 5))
            return QColor(tokens.CRIT) if blink > 0.4 else QColor(tokens.CY_300)
        return tokens.orb_accent(self._state)

    def _triangle_angle(self) -> float:
        if self._state == "thinking":
            return (self._breathe_phase * (360.0 / 3.0)) % 360.0  # §5.3: 360deg over 3s
        return 0.0

    # -- painting ----------------------------------------------------------

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        side = min(self.width(), self.height())
        if side <= 0:
            painter.end()
            return

        scale = side / self.REF
        painter.save()
        painter.translate((self.width() - side) / 2, (self.height() - side) / 2)
        painter.scale(scale, scale)
        if self._boot_scale != 1.0:
            # Boot sequence only (ui/hud/boot.py): rings scale in from 0.85
            # to 1.0 around the orb's own center, per ODIN-HUD.md §8.
            painter.translate(self.CENTER, self.CENTER)
            painter.scale(self._boot_scale, self._boot_scale)
            painter.translate(-self.CENTER, -self.CENTER)

        accent = self._core_color()
        ring_accent = tokens.THINKING if self._state == "thinking" else tokens.CY_300

        self._paint_halo(painter, accent)
        self._paint_outer_ring(painter, ring_accent)
        self._paint_launcher_ring(painter)
        self._paint_tick_ring(painter)
        self._paint_data_ring(painter, accent)
        self._paint_core(painter, accent)

        painter.restore()
        painter.end()

    def _paint_halo(self, painter: QPainter, accent: QColor) -> None:
        r = self._core_radius() * 2.6
        glow = QRadialGradient(QPointF(self.CENTER, self.CENTER), r)
        near = QColor(accent)
        near.setAlpha(70)
        far = QColor(accent)
        far.setAlpha(0)
        glow.setColorAt(0.0, near)
        glow.setColorAt(1.0, far)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(glow)
        painter.drawEllipse(QPointF(self.CENTER, self.CENTER), r, r)

    def _outer_ring_pens(self, accent: QColor, n: int) -> list[QPen]:
        """Perf: n QColor + n QPen objects were reconstructed from scratch
        every single frame here (only the alpha actually changes frame to
        frame) — now built once per accent color and reused, only their
        alpha mutated in place."""
        key = (accent.red(), accent.green(), accent.blue())
        if self._outer_pens_accent != key or len(self._outer_pens) != n:
            self._outer_pens = []
            for _ in range(n):
                pen = QPen(QColor(accent), 3.0)
                pen.setCapStyle(Qt.PenCapStyle.FlatCap)
                self._outer_pens.append(pen)
            self._outer_pens_accent = key
        return self._outer_pens

    def _paint_outer_ring(self, painter: QPainter, accent: QColor) -> None:
        n = 32
        gap_deg = 4.0
        seg_span = 360.0 / n - gap_deg
        rect = QRectF(self.CENTER - self.R_OUTER, self.CENTER - self.R_OUTER, self.R_OUTER * 2, self.R_OUTER * 2)
        pens = self._outer_ring_pens(accent, n)
        for i in range(n):
            start = self._phase + i * (360.0 / n)
            wave = 0.5 + 0.5 * math.sin(math.radians(start) + self._breathe_phase)
            pen = pens[i]
            color = pen.color()
            color.setAlphaF(0.25 + 0.75 * wave)
            pen.setColor(color)
            painter.setPen(pen)
            painter.drawArc(rect, int(start * 16), int(-seg_span * 16))

    def _paint_launcher_ring(self, painter: QPainter) -> None:
        rect = QRectF(self.CENTER - self.R_LAUNCHER, self.CENTER - self.R_LAUNCHER, self.R_LAUNCHER * 2, self.R_LAUNCHER * 2)
        gap_deg = 6.0
        seg_span = 45.0 - gap_deg
        painter.setFont(tokens.font_label(tokens.T_LABEL, bold=True))
        for i, label in enumerate(self.LAUNCHER_LABELS):
            center_angle = 90 - i * 45
            start = center_angle + seg_span / 2
            hovered = i == self._hover_index

            if hovered:
                painter.setPen(Qt.PenStyle.NoPen)
                fill = QColor(tokens.CY_300)
                fill.setAlphaF(0.14)
                painter.setBrush(fill)
                painter.drawPie(rect, int(start * 16), int(-seg_span * 16))
                painter.setBrush(Qt.BrushStyle.NoBrush)

            pen = QPen(tokens.CY_100 if hovered else tokens.CY_400, 2.5)
            painter.setPen(pen)
            painter.drawArc(rect, int(start * 16), int(-seg_span * 16))

            ang = math.radians(center_angle)
            tx = self.CENTER + self.R_LAUNCHER * math.cos(ang)
            ty = self.CENTER - self.R_LAUNCHER * math.sin(ang)
            painter.setPen(tokens.CY_100 if hovered else tokens.CY_200)
            painter.drawText(QRectF(tx - 30, ty - 8, 60, 16), Qt.AlignmentFlag.AlignCenter, label)

    def _build_tick_path(self) -> QPainterPath:
        """The 120 tick marks' geometry is fixed — only their overall
        rotation changes frame to frame — so it's built once at a baseline
        orientation (tick_phase == 0) and just rotated at paint time
        instead of recomputing 120 trig-derived line segments every frame."""
        n = 120
        path = QPainterPath()
        for i in range(n):
            ang = math.radians(i * 360.0 / n)
            x0 = self.CENTER + (self.R_TICK - 6) * math.cos(ang)
            y0 = self.CENTER - (self.R_TICK - 6) * math.sin(ang)
            x1 = self.CENTER + self.R_TICK * math.cos(ang)
            y1 = self.CENTER - self.R_TICK * math.sin(ang)
            path.moveTo(x0, y0)
            path.lineTo(x1, y1)
        return path

    def _paint_tick_ring(self, painter: QPainter) -> None:
        color = QColor(tokens.CY_400)
        color.setAlphaF(0.55)
        painter.setPen(QPen(color, 1.2))
        painter.save()
        painter.translate(self.CENTER, self.CENTER)
        painter.rotate(-self._tick_phase)
        painter.translate(-self.CENTER, -self.CENTER)
        painter.drawPath(self._tick_path)
        painter.restore()

    def _paint_data_ring(self, painter: QPainter, accent: QColor) -> None:
        rect = QRectF(self.CENTER - self.R_DATA, self.CENTER - self.R_DATA, self.R_DATA * 2, self.R_DATA * 2)
        painter.setPen(QPen(tokens.CY_700, 3))
        painter.drawEllipse(rect)

        if self._state == "thinking":
            start = (self._phase * 3) % 360  # indeterminate, spins faster than the outer ring
            pen = QPen(accent, 3)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.drawArc(rect, int(start * 16), int(-90 * 16))  # 25% of the circle
            return

        def stroke(pen) -> None:
            painter.setPen(pen)
            painter.drawArc(rect, 90 * 16, int(-360 * self._data_value * 16))

        if self._data_value > 0.002:
            tokens.draw_glow(painter, stroke, accent, 3, passes=2)

    def _paint_core(self, painter: QPainter, accent: QColor) -> None:
        r = self._core_radius()
        if self._boot_flash > 0.0:
            # Boot sequence only (ui/hud/boot.py): core ignition — a flash
            # to white settling back to the idle gradient, per
            # ODIN-HUD.md §8.
            accent = _lerp_color(accent, QColor(255, 255, 255), self._boot_flash)
            r *= 1.0 + 0.5 * self._boot_flash
        gradient = QRadialGradient(QPointF(self.CENTER, self.CENTER), r)
        gradient.setColorAt(0.0, QColor(0xDF, 0xFB, 0xFF))
        gradient.setColorAt(0.55, accent)
        transparent = QColor(accent)
        transparent.setAlpha(0)
        gradient.setColorAt(1.0, transparent)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(gradient)
        painter.drawEllipse(QPointF(self.CENTER, self.CENTER), r, r)

        tri_r = self.R_CORE * 0.62
        base = math.radians(90 + self._triangle_angle())
        points = [
            QPointF(
                self.CENTER + tri_r * math.cos(base + 2 * math.pi * i / 3),
                self.CENTER - tri_r * math.sin(base + 2 * math.pi * i / 3),
            )
            for i in range(3)
        ]
        painter.setPen(QPen(QColor(255, 255, 255, 140), 1.6))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPolygon(*points)
