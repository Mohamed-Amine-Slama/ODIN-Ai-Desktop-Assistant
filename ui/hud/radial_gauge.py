"""RadialGauge — ODIN-HUD.md §5.2: the four gauges flanking the orb, reused
anywhere a 0-100 value needs an instrument face.

Angle convention, verified empirically against a rendered pixmap rather than
trusted from the SVG source (QPainter.drawArc uses 0deg = 3 o'clock, positive
= counter-clockwise, same as standard trig — NOT the SVG/CSS "0deg = 12
o'clock, clockwise" convention the spec's numbers were written in). The
spec's "270deg sweep, gap at the bottom, starting bottom-left / ending
bottom-right" shape, translated into Qt's convention, is: start at 225deg
(bottom-left), sweep -270deg (clockwise, i.e. up through the top) to end at
-45deg == 315deg (bottom-right).
"""
from __future__ import annotations

import math

from PyQt6.QtCore import QEasingCurve, QPropertyAnimation, QRectF, Qt, pyqtProperty
from PyQt6.QtGui import QColor, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import QWidget

from . import tokens

START_DEG = 225.0
SWEEP_DEG = 270.0  # traversed clockwise (negative Qt span) from START_DEG
PULSE_RATE = 1.5  # rad/s — matches the old 30ms-timer's 0.045rad/tick cadence


class RadialGauge(QWidget):
    REF = 120.0  # the spec's SVG viewBox size; all geometry below is in
    R = 50.0  # this reference frame, then scaled to the widget's real size.
    CENTER = 60.0
    TICK_COUNT = 21  # every 5%

    def __init__(self, unit: str = "%", parent=None):
        super().__init__(parent)
        self._unit = unit
        self._value = 0.0  # animated 0..1 — the arc's actual sweep fraction
        self._display: float | None = None  # the raw number shown at center, or None for "--"
        self.setMinimumSize(96, 96)

        self._anim = QPropertyAnimation(self, b"value", self)
        self._anim.setDuration(tokens.DUR_VAL)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        self._is_critical = False
        self._pulse_phase = 0.0

        # Perf: the track arc + all 21 tick marks/labels are fixed geometry
        # (only the colored value arc and center text change frame to
        # frame) — cached into a pixmap once per size, same as
        # ui/hud/widgets.py's TickRuler._ensure_static, instead of redone
        # via trig + drawLine/drawText on every paint.
        self._static: QPixmap | None = None

    def getValue(self) -> float:
        return self._value

    def setValue(self, value: float) -> None:
        self._value = value
        self.update()

    value = pyqtProperty(float, getValue, setValue)

    def set_percent(self, percent: float | None) -> None:
        """percent: 0..100, or None for an unavailable sensor — the arc
        eases to 0 and the center renders `--` (never fabricate, §10)."""
        self._display = percent
        target = 0.0 if percent is None else max(0.0, min(100.0, percent)) / 100.0

        self._anim.stop()
        self._anim.setStartValue(self._value)
        self._anim.setEndValue(target)
        self._anim.start()

        is_crit = percent is not None and percent >= tokens.CRIT_THRESHOLD * 100
        if is_crit != self._is_critical:
            self._is_critical = is_crit
            if not is_crit:
                self._pulse_phase = 0.0
            self.update()

    def advance(self, dt: float) -> None:
        """Driven by the shared ~30fps loop (ODIN-HUD.md §10,
        TelemetryPresenter.advance_animation) rather than a private QTimer
        — up to four gauges pulsing critical at once used to mean four
        independent 30ms timers running outside the app's one shared
        animation clock."""
        if not self._is_critical:
            return
        self._pulse_phase += dt * PULSE_RATE
        self.update()

    def resizeEvent(self, event) -> None:
        self._static = None
        super().resizeEvent(event)

    def _ensure_static(self) -> None:
        if self._static is not None and self._static.size() == self.size():
            return
        pm = QPixmap(self.size())
        pm.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pm)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        side = min(self.width(), self.height())
        if side > 0:
            scale = side / self.REF
            painter.translate((self.width() - side) / 2, (self.height() - side) / 2)
            painter.scale(scale, scale)

            rect = QRectF(self.CENTER - self.R, self.CENTER - self.R, self.R * 2, self.R * 2)
            track_pen = QPen(tokens.CY_700, 4)
            track_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(track_pen)
            painter.drawArc(rect, int(START_DEG * 16), int(-SWEEP_DEG * 16))

            painter.setFont(tokens.font_data(9))
            for i in range(self.TICK_COUNT):
                frac = i / (self.TICK_COUNT - 1)
                ang = math.radians(START_DEG - SWEEP_DEG * frac)
                major = i % 5 == 0
                r_out = self.R + 4
                r_in = self.R + (9 if major else 5)
                x0, y0 = self.CENTER + r_out * math.cos(ang), self.CENTER - r_out * math.sin(ang)
                x1, y1 = self.CENTER + r_in * math.cos(ang), self.CENTER - r_in * math.sin(ang)
                painter.setPen(QPen(tokens.CY_400 if major else tokens.CY_600, 1))
                painter.drawLine(int(x0), int(y0), int(x1), int(y1))
                if major:
                    tx = self.CENTER + (r_in + 8) * math.cos(ang)
                    ty = self.CENTER - (r_in + 8) * math.sin(ang)
                    painter.setPen(tokens.CY_400)
                    painter.drawText(
                        QRectF(tx - 10, ty - 6, 20, 12), Qt.AlignmentFlag.AlignCenter, str(int(frac * 100))
                    )
        painter.end()
        self._static = pm

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        self._ensure_static()
        painter.drawPixmap(0, 0, self._static)

        side = min(self.width(), self.height())
        if side > 0:
            scale = side / self.REF
            painter.save()
            painter.translate((self.width() - side) / 2, (self.height() - side) / 2)
            painter.scale(scale, scale)

            rect = QRectF(self.CENTER - self.R, self.CENTER - self.R, self.R * 2, self.R * 2)
            color = tokens.threshold_color(self._value)
            if self._is_critical:
                alpha = 0.55 + 0.45 * abs(math.sin(self._pulse_phase))
                color = QColor(color)
                color.setAlphaF(alpha)

            def stroke(pen) -> None:
                painter.setPen(pen)
                painter.drawArc(rect, int(START_DEG * 16), int(-SWEEP_DEG * self._value * 16))

            if self._value > 0.002:
                tokens.draw_glow(painter, stroke, color, 4)

            painter.restore()

        value_text = "--" if self._display is None else f"{self._display:.0f}"
        cx, cy = self.width() / 2, self.height() / 2
        painter.setFont(tokens.font_data(tokens.T_LG))
        painter.setPen(tokens.CY_100)
        painter.drawText(QRectF(cx - side / 2, cy - 16, side, 24), Qt.AlignmentFlag.AlignCenter, value_text)
        painter.setFont(tokens.font_label(tokens.T_MICRO))
        painter.setPen(tokens.CY_500)
        painter.drawText(QRectF(cx - side / 2, cy + 10, side, 14), Qt.AlignmentFlag.AlignCenter, self._unit)
        painter.end()
