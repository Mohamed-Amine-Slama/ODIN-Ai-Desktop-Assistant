"""Sparkline — ODIN-HUD.md §5.5. Qt has no `<canvas>`, but a plain
`QWidget.paintEvent` painting a polyline plus a gradient-filled area beneath
it is the more direct equivalent anyway: `QPainter` already is the retained
drawing surface Canvas2D exists on the web only because nothing else does.
"""
from __future__ import annotations

from collections import deque

from PyQt6.QtCore import QEasingCurve, QPropertyAnimation, QRectF, Qt, pyqtProperty
from PyQt6.QtGui import QColor, QFontMetrics, QLinearGradient, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QWidget

from . import tokens

MAX_SAMPLES = 60


class Sparkline(QWidget):
    def __init__(self, unit: str = "", parent=None):
        super().__init__(parent)
        self._unit = unit
        self._samples: deque[float] = deque(maxlen=MAX_SAMPLES)
        self._scale_max = 1.0
        self.setMinimumHeight(36)

        self._anim = QPropertyAnimation(self, b"scaleMax", self)
        self._anim.setDuration(tokens.DUR_VAL)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    def getScaleMax(self) -> float:
        return self._scale_max

    def setScaleMax(self, value: float) -> None:
        self._scale_max = value
        self.update()

    scaleMax = pyqtProperty(float, getScaleMax, setScaleMax)

    def push(self, value: float) -> None:
        """One sample per incoming telemetry frame (1Hz) — not per animation
        frame; the axis eases toward its new ceiling independently."""
        self._samples.append(max(value, 0.0))
        window_max = max(self._samples)
        target = max(window_max * 1.15, 1.0)  # 15% headroom (§5.5)

        self._anim.stop()
        self._anim.setStartValue(self._scale_max)
        self._anim.setEndValue(target)
        self._anim.start()
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = self.rect()

        if len(self._samples) >= 2 and self._scale_max > 0:
            n = len(self._samples)
            step = rect.width() / (MAX_SAMPLES - 1)
            offset = MAX_SAMPLES - n
            points = [
                (
                    (offset + i) * step,
                    rect.height() - min(v / self._scale_max, 1.0) * rect.height(),
                )
                for i, v in enumerate(self._samples)
            ]

            line = QPainterPath()
            line.moveTo(*points[0])
            for x, y in points[1:]:
                line.lineTo(x, y)

            fill = QPainterPath(line)
            fill.lineTo(points[-1][0], rect.height())
            fill.lineTo(points[0][0], rect.height())
            fill.closeSubpath()

            gradient = QLinearGradient(0, 0, 0, rect.height())
            top = QColor(tokens.CY_300)
            top.setAlphaF(0.28)
            bottom = QColor(tokens.CY_300)
            bottom.setAlphaF(0.0)
            gradient.setColorAt(0.0, top)
            gradient.setColorAt(1.0, bottom)
            painter.fillPath(fill, gradient)

            painter.setPen(QPen(tokens.CY_300, 1.5))
            painter.drawPath(line)

        if self._samples:
            text = f"{self._samples[-1]:.1f}{self._unit}"
            font = tokens.font_data(tokens.T_MICRO)
            painter.setFont(font)
            painter.setPen(tokens.CY_200)
            text_w = QFontMetrics(font).horizontalAdvance(text)
            painter.drawText(QRectF(rect.width() - text_w - 4, 2, text_w + 4, 14), Qt.AlignmentFlag.AlignRight, text)
        painter.end()
