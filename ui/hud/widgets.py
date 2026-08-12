"""The straightforward component translations from ODIN-HUD.md §5: Panel,
Readout, BarMeter, DockButton, TickRuler. Each is a self-painted QWidget —
no QSS, no child-widget assembly beyond what a component itself needs.
"""
from __future__ import annotations

import math
from datetime import datetime

from PyQt6.QtCore import QEasingCurve, QPointF, QPropertyAnimation, QRectF, QTimer, Qt, pyqtProperty
from PyQt6.QtGui import QColor, QFontMetrics, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import QAbstractButton, QVBoxLayout, QWidget

from . import tokens

_TITLE_H = 20
# Every panel's body starts at this spacing rather than Qt's style-default
# (6-11px, varying by platform) — with 4-8 rows packed into a ~130px or even
# ~40px budget (see ui/hud/layout.py's rebalancing note), the unset default
# was worth 20-50+ extra px per panel on its own and was a real, measured
# contributor to the content overlapping its container.
_BODY_SPACING = 3


class Panel(QWidget):
    """The bracket-frame workhorse (§5.1): sharp rectangle, corner ticks,
    a titled hairline rule, an optional status pip. Add child widgets to
    `.body_layout`, not to the Panel itself."""

    def __init__(self, title: str, parent=None, status: str | None = None):
        super().__init__(parent)
        self._title = title.upper()
        self._status = status  # None | "ok" | "warn" | "crit"

        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, _TITLE_H, 8, 6)
        outer.setSpacing(0)
        self.body = QWidget(self)
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(0, 0, 0, 0)
        self.body_layout.setSpacing(_BODY_SPACING)
        outer.addWidget(self.body)

    def set_status(self, status: str | None) -> None:
        if status != self._status:
            self._status = status
            self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = self.rect()

        painter.fillRect(rect, tokens.PANEL)
        tokens.corner_ticks(painter, rect, tokens.CY_600, alpha=255)

        sq = 6
        tx, ty = 8, 6
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(tokens.CY_300)
        painter.drawRect(tx, ty, sq, sq)

        label_font = tokens.font_label(tokens.T_LABEL)
        painter.setFont(label_font)
        painter.setPen(tokens.CY_500)
        text_x = tx + sq + 6
        metrics = QFontMetrics(label_font)
        painter.drawText(text_x, ty + sq + metrics.ascent() // 2, self._title)

        text_w = metrics.horizontalAdvance(self._title)
        rule_y = ty + sq // 2
        rule_x0 = text_x + text_w + 8
        rule_x1 = rect.right() - (18 if self._status else 8)
        if rule_x1 > rule_x0:
            painter.setPen(QPen(tokens.CY_600, 1))
            painter.drawLine(rule_x0, rule_y, rule_x1, rule_y)

        if self._status:
            color = {"ok": tokens.OK, "warn": tokens.WARN, "crit": tokens.CRIT}.get(
                self._status, tokens.OK
            )
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
            painter.drawEllipse(rect.right() - 15, ty + 1, 5, 5)
        painter.end()


class Readout(QWidget):
    """A key/value row with a dotted leader (§5.9) — covers most of a
    panel's contents on its own."""

    def __init__(self, label: str, value: str = "--", parent=None):
        super().__init__(parent)
        self._label = label.upper()
        self._value = value
        self.setFixedHeight(16)

    def set_value(self, value: str) -> None:
        if value != self._value:
            self._value = value
            self.update()

    def set_label(self, label: str) -> None:
        label = label.upper()
        if label != self._label:
            self._label = label
            self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = self.rect()

        label_font = tokens.font_label(tokens.T_MICRO)
        value_font = tokens.font_data(tokens.T_MICRO + 2)
        label_metrics = QFontMetrics(label_font)
        value_metrics = QFontMetrics(value_font)

        label_w = label_metrics.horizontalAdvance(self._label)
        value_w = value_metrics.horizontalAdvance(self._value)
        value_x = rect.width() - value_w

        painter.setFont(label_font)
        painter.setPen(tokens.CY_500)
        painter.drawText(0, (rect.height() + label_metrics.ascent()) // 2 - 1, self._label)

        painter.setFont(value_font)
        painter.setPen(tokens.CY_200)
        painter.drawText(value_x, (rect.height() + value_metrics.ascent()) // 2 - 1, self._value)

        leader_x0, leader_x1 = label_w + 8, value_x - 8
        if leader_x1 > leader_x0:
            pen = QPen(tokens.CY_700, 1, Qt.PenStyle.DotLine)
            painter.setPen(pen)
            y = rect.height() // 2
            painter.drawLine(leader_x0, y, leader_x1, y)
        painter.end()


class BarMeter(QWidget):
    """A segmented horizontal load bar (§5.4): CPU, RAM, per-drive."""

    _PEAK_DECAY = 0.9  # per set_value() call (~1Hz), so a peak fades over ~10s

    def __init__(self, label: str, parent=None):
        super().__init__(parent)
        self._label = label.upper()
        self._fraction = 0.0
        self._display = "--"
        self._peak = 0.0
        self.setFixedHeight(26)

    def set_value(self, fraction: float | None, display_text: str) -> None:
        self._fraction = 0.0 if fraction is None else max(0.0, min(1.0, fraction))
        self._display = display_text
        self._peak = max(self._fraction, self._peak * self._PEAK_DECAY)
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w = self.width()

        label_font = tokens.font_label(tokens.T_MICRO)
        value_font = tokens.font_data(tokens.T_MICRO + 2)
        painter.setFont(label_font)
        painter.setPen(tokens.CY_500)
        painter.drawText(0, 9, self._label)
        painter.setFont(value_font)
        painter.setPen(tokens.CY_200)
        value_w = QFontMetrics(value_font).horizontalAdvance(self._display)
        painter.drawText(w - value_w, 10, self._display)

        bar_rect = QRectF(0, 14, w, 7)
        painter.fillRect(bar_rect, tokens.CY_700)

        color = tokens.threshold_color(self._fraction)
        fill_w = bar_rect.width() * self._fraction
        cell, gap = 2.0, 1.0
        x = 0.0
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        while x < fill_w:
            seg_w = min(cell, fill_w - x)
            painter.drawRect(QRectF(bar_rect.left() + x, bar_rect.top(), seg_w, bar_rect.height()))
            x += cell + gap

        if self._peak > 0.001:
            peak_x = bar_rect.left() + bar_rect.width() * self._peak
            painter.setPen(QPen(tokens.CY_100, 2))
            painter.drawLine(QPointF(peak_x, bar_rect.top() - 2), QPointF(peak_x, bar_rect.bottom() + 2))
        painter.end()


class TickRuler(QWidget):
    """The 24h header ruler (§5.7): a static tick/numeral pixmap, redrawn
    only on resize, plus a caret that moves once a second."""

    MAJOR_TICKS = 24
    MINOR_PER_MAJOR = 4

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(20)
        self._static: QPixmap | None = None
        self._now = datetime.now()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(1000)

    def _tick(self) -> None:
        self._now = datetime.now()
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
        w = self.width()
        total_minor = self.MAJOR_TICKS * (self.MINOR_PER_MAJOR + 1)
        painter.setFont(tokens.font_data(tokens.T_MICRO))
        for i in range(total_minor + 1):
            x = w * i / total_minor
            is_major = i % (self.MINOR_PER_MAJOR + 1) == 0
            painter.setPen(QPen(tokens.CY_400 if is_major else tokens.CY_600, 1))
            h = 9 if is_major else 4
            painter.drawLine(int(x), 0, int(x), h)
            if is_major:
                hour = i // (self.MINOR_PER_MAJOR + 1)
                painter.setPen(tokens.CY_500)
                painter.drawText(int(x) + 2, h + 9, f"{hour:02d}")
        painter.end()
        self._static = pm

    def paintEvent(self, _event) -> None:
        self._ensure_static()
        painter = QPainter(self)
        painter.drawPixmap(0, 0, self._static)
        frac = (self._now.hour * 3600 + self._now.minute * 60 + self._now.second) / 86400
        x = self.width() * frac
        painter.setPen(QPen(tokens.CY_100, 2))
        painter.drawLine(int(x), 0, int(x), self.height())
        painter.end()


class DockButton(QAbstractButton):
    """One of the dock's 9 circular launchers (§5.8). `clicked` (inherited
    from QAbstractButton) carries no payload — callers distinguish buttons
    by object identity/closure, same as any other Qt button."""

    DIAMETER = 76

    def __init__(self, glyph: str, label: str, parent=None):
        super().__init__(parent)
        self._glyph = glyph
        self._label = label.upper()
        self.setFixedSize(self.DIAMETER + 24, self.DIAMETER + 44)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setToolTip(self._label)

        self._scale = 1.0
        self._anim = QPropertyAnimation(self, b"scale", self)
        self._anim.setDuration(tokens.DUR_FAST)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    def getScale(self) -> float:
        return self._scale

    def setScale(self, value: float) -> None:
        self._scale = value
        self.update()

    scale = pyqtProperty(float, getScale, setScale)

    def _animate_to(self, target: float) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._scale)
        self._anim.setEndValue(target)
        self._anim.start()

    def enterEvent(self, event) -> None:
        self._animate_to(1.06)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._animate_to(1.0)
        super().leaveEvent(event)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        cx = self.width() / 2
        cy = self.DIAMETER / 2 + 4
        hovered = self.underMouse()
        pressed = self.isDown()
        r = self.DIAMETER / 2 * (0.96 if pressed else self._scale)

        ring_color = tokens.CY_300 if hovered else tokens.CY_400

        def stroke_ring(pen) -> None:
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QPointF(cx, cy), r, r)

        # Always-on glow, brighter on hover — reads as "powered" at rest
        # rather than a bare outline, matching the reference imagery's
        # instrument density instead of a plain flat ring.
        tokens.draw_glow(painter, stroke_ring, ring_color, 2.0, passes=3 if hovered else 1)

        # A thin secondary ring plus a handful of tick marks just outside
        # it — a compact echo of RadialGauge's tick scale, giving the
        # button real instrument detail instead of a single bare circle.
        painter.setPen(QPen(QColor(tokens.CY_600), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(QPointF(cx, cy), r + 5, r + 5)
        for i in range(12):
            ang = math.radians(i * 30)
            x0, y0 = cx + (r + 5) * math.cos(ang), cy + (r + 5) * math.sin(ang)
            x1, y1 = cx + (r + 8) * math.cos(ang), cy + (r + 8) * math.sin(ang)
            painter.drawLine(QPointF(x0, y0), QPointF(x1, y1))

        inner = QColor(tokens.CY_600) if pressed else QColor(6, 28, 48, 160)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(inner)
        painter.drawEllipse(QPointF(cx, cy), r - 3, r - 3)

        painter.setPen(tokens.CY_100 if hovered else tokens.CY_200)
        painter.setFont(tokens.font_label(tokens.T_DATA, bold=True))
        painter.drawText(QRectF(cx - r, cy - r, 2 * r, 2 * r), Qt.AlignmentFlag.AlignCenter, self._glyph)

        painter.setFont(tokens.font_label(tokens.T_MICRO))
        painter.setPen(tokens.CY_100 if hovered else tokens.CY_500)
        painter.drawText(
            QRectF(0, self.DIAMETER + 14, self.width(), 16),
            Qt.AlignmentFlag.AlignHCenter,
            self._label,
        )

        if self.hasFocus():
            painter.setPen(QPen(tokens.OK, 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QPointF(cx, cy), r + 10, r + 10)
        painter.end()
