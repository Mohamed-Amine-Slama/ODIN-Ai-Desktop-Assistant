"""Instrument widgets for the HUD's side panels (ODIN-HUD.md §6).

Six pieces the left and right columns are assembled from: a scrolling history
graph, a hero numeral, a compact arc gauge, a process list, a weather forecast
strip, and a battery meter.

Two rules they all share, and the reasons for them:

*Fed slowly, drawn quickly.* Telemetry arrives about once a second; the HUD
paints at frame rate. So each widget holds a target and eases toward it, and
the graph slides between readings instead of stepping — the movement you see
is interpolation, never invented data.

*Advanced, not self-timed.* None of these owns a QTimer. The window's one
shared loop calls `advance(dt)` (§10: "one shared loop, not one per widget"),
which is also what keeps ten animated panels affordable.

Colors come from ui/hud/tokens.py without exception, same as everywhere else
under ui/hud/.
"""
from __future__ import annotations

from collections import deque
from datetime import datetime

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QWidget

from . import tokens

class _Instrument(QWidget):
    """Shared base: transparent, non-interactive, driven by the shared loop."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def advance(self, dt: float) -> None:
        """Called every frame by the window's loop. No-op unless the widget
        has something to ease."""


class MetricGraph(_Instrument):
    """A scrolling history trace, filled underneath.

    `maximum=None` autoscales to the tallest sample it still holds, which is
    the only way an unbounded series (network rates) stays readable; a fixed
    maximum keeps percentages honest against their real ceiling.
    """

    def __init__(self, capacity: int = 60, maximum: float | None = None,
                 interval: float = 1.0, accent: QColor | None = None, parent=None):
        super().__init__(parent)
        self._samples: deque[float] = deque(maxlen=max(2, capacity))
        self._maximum = maximum
        self._interval = max(0.05, interval)
        self._phase = 0.0
        self._accent = QColor(accent) if accent is not None else QColor(tokens.CY_300)
        self._line_path: QPainterPath | None = None
        self._area_path: QPainterPath | None = None
        self.setMinimumHeight(28)

    # -- data ------------------------------------------------------------

    @property
    def samples(self) -> list[float]:
        return list(self._samples)

    @property
    def scale_max(self) -> float:
        if self._maximum is not None:
            return self._maximum
        return max(1e-6, max(self._samples, default=0.0))

    @property
    def scroll_phase(self) -> float:
        return self._phase

    def set_accent(self, color: QColor) -> None:
        if color.rgb() != self._accent.rgb():
            self._accent = QColor(color)
            self.update()

    def push(self, value: float) -> None:
        self._samples.append(float(value))
        self._phase = 0.0
        self._invalidate()
        self.update()

    def advance(self, dt: float) -> None:
        if not self._samples or self._phase >= 1.0:
            # Capped at one full step: if the telemetry thread stalls, the
            # trace holds at the axis edge rather than sliding off it — and
            # stops asking to be repainted, since nothing is moving.
            return
        self._phase = min(1.0, self._phase + dt / self._interval)
        self.update()

    def resizeEvent(self, event) -> None:
        self._invalidate()
        super().resizeEvent(event)

    def _invalidate(self) -> None:
        self._line_path = None
        self._area_path = None

    # -- geometry ---------------------------------------------------------

    def _step(self) -> float:
        return self.width() / max(1, len(self._samples) - 1)

    def scroll_offset(self) -> float:
        """How far left the trace is slid this frame. Zero until the buffer
        is full: while history is still growing the trace extends into the
        panel, and sliding it as well would make the line lurch sideways on
        every reading."""
        if len(self._samples) < (self._samples.maxlen or 0):
            return 0.0
        return -self._phase * self._step()

    def trace_path(self) -> QPainterPath:
        """The trace itself, rebuilt only when a reading arrives or the widget
        resizes — frames in between just translate it."""
        if self._line_path is None:
            self._build()
        return self._line_path

    def _build(self) -> None:
        line = QPainterPath()
        area = QPainterPath()
        width, height = float(self.width()), float(self.height())
        count = len(self._samples)
        if count < 2 or width <= 0 or height <= 0:
            self._line_path, self._area_path = line, area
            return

        # Spaced across the panel by how many readings there actually are,
        # not by the buffer's capacity: laying it out against capacity left a
        # freshly opened HUD showing its trace squeezed into the right-hand
        # third with a minute of blank beside it.
        step = self._step()
        ceiling = self.scale_max
        points = [
            QPointF(i * step, height - min(1.0, value / ceiling) * (height - 2.0) - 1.0)
            for i, value in enumerate(self._samples)
        ]
        line.moveTo(points[0])
        for point in points[1:]:
            line.lineTo(point)

        area.addPath(line)
        area.lineTo(points[-1].x(), height)
        area.lineTo(points[0].x(), height)
        area.closeSubpath()
        self._line_path, self._area_path = line, area

    # -- painting ---------------------------------------------------------

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        width, height = self.width(), self.height()

        base = QColor(tokens.CY_700)
        painter.setPen(QPen(base, 1))
        for fraction in (0.5, 1.0):
            y = int(height * fraction) - 1
            painter.drawLine(0, y, width, y)

        if len(self._samples) < 2:
            painter.end()
            return

        painter.save()
        painter.translate(self.scroll_offset(), 0)
        fill = QColor(self._accent)
        fill.setAlphaF(0.16)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(fill)
        if self._area_path is None:
            self._build()
        painter.drawPath(self._area_path)

        pen = QPen(self._accent, 1.6)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(self._line_path)
        painter.restore()
        painter.end()


class HeroValue(_Instrument):
    """The panel's headline number: large, eased, and recoloured at the same
    75%/90% thresholds every other meter on the HUD uses."""

    def __init__(self, label: str, unit: str, maximum: float = 100.0,
                 decimals: int = 0, parent=None):
        super().__init__(parent)
        self._label = label.upper()
        self._unit = unit
        self._maximum = maximum
        self._decimals = decimals
        self._target = 0.0
        self._displayed = 0.0
        self._caption = ""
        self.setMinimumHeight(46)

    @property
    def displayed(self) -> float:
        return self._displayed

    def accent(self) -> QColor:
        return tokens.threshold_color(self._displayed / self._maximum if self._maximum else 0.0)

    def set_value(self, value: float) -> None:
        self._target = float(value)

    def set_caption(self, text: str) -> None:
        """The small line under the numeral — used/total, rate units, etc."""
        if text != self._caption:
            self._caption = text
            self.update()

    def advance(self, dt: float) -> None:
        if self._displayed != self._target:
            self._displayed = tokens.ease_toward(self._displayed, self._target, dt)
            self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        accent = self.accent()

        painter.setFont(tokens.font_label(tokens.T_MICRO))
        painter.setPen(tokens.CY_500)
        painter.drawText(QRectF(0, 0, self.width(), 12), Qt.AlignmentFlag.AlignLeft, self._label)

        painter.setFont(tokens.font_data(tokens.T_LG))
        painter.setPen(accent)
        text = f"{self._displayed:.{self._decimals}f}"
        value_rect = QRectF(0, 10, self.width(), 30)
        painter.drawText(value_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, text)

        metrics = painter.fontMetrics()
        painter.setFont(tokens.font_label(tokens.T_LABEL))
        painter.setPen(tokens.CY_400)
        painter.drawText(
            QRectF(metrics.horizontalAdvance(text) + 6, 10, 60, 30),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            self._unit,
        )

        if self._caption:
            painter.setFont(tokens.font_data(tokens.T_LABEL))
            painter.setPen(tokens.CY_200)
            painter.drawText(
                QRectF(0, 10, self.width(), 30),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                self._caption,
            )
        painter.end()


class MiniArc(_Instrument):
    """A compact 240° gauge for one sensor. Reads `--` and sits at zero when
    the backend can't supply a value — §10's "never fabricate"."""

    SPAN = 240.0
    START = 210.0  # degrees, sweeping clockwise from lower-left

    def __init__(self, label: str, unit: str, minimum: float = 0.0,
                 maximum: float = 100.0, parent=None):
        super().__init__(parent)
        self._label = label.upper()
        self._unit = unit
        self._min = minimum
        self._max = maximum
        self._value: float | None = None
        self._target = 0.0
        self._displayed = 0.0
        self.setMinimumSize(72, 72)

    @property
    def fraction(self) -> float:
        return self._displayed

    def set_value(self, value: float | None) -> None:
        self._value = value
        if value is None:
            self._target = self._displayed = 0.0
        else:
            span = max(1e-6, self._max - self._min)
            self._target = max(0.0, min(1.0, (value - self._min) / span))
        self.update()

    def advance(self, dt: float) -> None:
        if self._displayed != self._target:
            self._displayed = tokens.ease_toward(self._displayed, self._target, dt)
            self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        side = min(self.width(), self.height())
        if side <= 0:
            painter.end()
            return
        inset = 9.0
        rect = QRectF(
            (self.width() - side) / 2 + inset, (self.height() - side) / 2 + inset,
            side - inset * 2, side - inset * 2,
        )
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(tokens.CY_700, 4))
        painter.drawArc(rect, int(self.START * 16), int(-self.SPAN * 16))

        if self._value is not None:
            accent = tokens.threshold_color(self._displayed)
            pen = QPen(accent, 4)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.drawArc(rect, int(self.START * 16), int(-self.SPAN * self._displayed * 16))

        painter.setFont(tokens.font_data(tokens.T_BODY))
        painter.setPen(tokens.CY_100 if self._value is not None else tokens.CY_500)
        text = "--" if self._value is None else f"{self._value:.0f}{self._unit}"
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)

        painter.setFont(tokens.font_label(tokens.T_MICRO))
        painter.setPen(tokens.CY_500)
        painter.drawText(
            QRectF(0, self.height() - 12, self.width(), 12),
            Qt.AlignmentFlag.AlignHCenter, self._label,
        )
        painter.end()


class ProcessRows(_Instrument):
    """The top consumers, as name / bar / value rows. Bars are relative to the
    heaviest row, not to any absolute scale — the question these answer is
    "what's eating it", not "how much of the machine"."""

    ROW_H = 15

    def __init__(self, count: int = 3, unit: str = "%", decimals: int = 1, parent=None):
        super().__init__(parent)
        self._count = count
        self._unit = unit
        self._decimals = decimals
        self._rows: list[tuple[str, float]] = []
        self._eased: list[float] = []
        self.setMinimumHeight(self.ROW_H * count)

    @property
    def rows(self) -> list[tuple[str, float]]:
        return list(self._rows)

    def row_fraction(self, index: int) -> float:
        if not self._rows or index >= len(self._rows):
            return 0.0
        ceiling = max(value for _, value in self._rows) or 1.0
        return self._rows[index][1] / ceiling

    def set_rows(self, rows) -> None:
        self._rows = [(str(name), float(value)) for name, value in rows][: self._count]
        self._eased = (self._eased + [0.0] * len(self._rows))[: len(self._rows)]
        self.update()

    def advance(self, dt: float) -> None:
        changed = False
        for i in range(len(self._rows)):
            target = self.row_fraction(i)
            if self._eased[i] != target:
                self._eased[i] = tokens.ease_toward(self._eased[i], target, dt)
                changed = True
        if changed:
            self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        width = self.width()
        # Sized to the widest value actually present rather than a constant:
        # "18.4%" and "3831 KB/S" need very different columns, and a fixed
        # one either clips the long form or wastes half the row on the short.
        painter.setFont(tokens.font_data(tokens.T_MICRO))
        metrics = painter.fontMetrics()
        value_w = 30
        for _, value in self._rows:
            value_w = max(value_w, metrics.horizontalAdvance(f"{value:.{self._decimals}f}{self._unit}") + 8)
        value_w = int(min(value_w, width * 0.5))
        for i, (name, value) in enumerate(self._rows):
            top = i * self.ROW_H
            bar_w = int((width - value_w) * (self._eased[i] if i < len(self._eased) else 0.0))
            track = QColor(tokens.CY_300)
            track.setAlphaF(0.13)
            painter.fillRect(QRectF(0, top + 2, bar_w, self.ROW_H - 5), track)

            painter.setFont(tokens.font_label(tokens.T_MICRO))
            painter.setPen(tokens.CY_200)
            painter.drawText(
                QRectF(2, top, width - value_w - 4, self.ROW_H),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                painter.fontMetrics().elidedText(
                    name.upper(), Qt.TextElideMode.ElideMiddle, width - value_w - 8
                ),
            )
            painter.setFont(tokens.font_data(tokens.T_MICRO))
            painter.setPen(tokens.CY_100)
            painter.drawText(
                QRectF(width - value_w, top, value_w - 4, self.ROW_H),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                f"{value:.{self._decimals}f}{self._unit}",
            )
        painter.end()


class ForecastStrip(_Instrument):
    """The multi-day outlook the weather worker already fetches. Each day is a
    vertical bar spanning its low to its high, all mapped onto one shared
    range so the days are comparable at a glance."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._days: list[tuple[str, float, float]] = []
        self.setMinimumHeight(48)

    @property
    def days(self) -> list[tuple[str, float, float]]:
        return list(self._days)

    @property
    def range_c(self) -> tuple[float, float]:
        if not self._days:
            return (0.0, 0.0)
        return (min(day[1] for day in self._days), max(day[2] for day in self._days))

    def set_forecast(self, days) -> None:
        self._days = [(str(d), float(lo), float(hi)) for d, lo, hi in days]
        self.update()

    @staticmethod
    def _weekday(date_text: str) -> str:
        try:
            return datetime.strptime(date_text[:10], "%Y-%m-%d").strftime("%a").upper()
        except ValueError:
            return date_text[:3].upper()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if not self._days:
            painter.setFont(tokens.font_label(tokens.T_MICRO))
            painter.setPen(tokens.CY_600)
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "NO FORECAST")
            painter.end()
            return

        low, high = self.range_c
        span = max(1e-6, high - low)
        column = self.width() / len(self._days)
        track_top, track_h = 14.0, self.height() - 28.0

        for i, (date_text, day_low, day_high) in enumerate(self._days):
            cx = column * (i + 0.5)
            y_high = track_top + (1.0 - (day_high - low) / span) * track_h
            y_low = track_top + (1.0 - (day_low - low) / span) * track_h

            pen = QPen(tokens.threshold_color((day_high - low) / span * 0.7), 4)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.drawLine(QPointF(cx, y_high), QPointF(cx, max(y_low, y_high + 1.0)))

            painter.setFont(tokens.font_label(tokens.T_MICRO))
            painter.setPen(tokens.CY_500)
            painter.drawText(
                QRectF(cx - column / 2, self.height() - 13, column, 12),
                Qt.AlignmentFlag.AlignHCenter, self._weekday(date_text),
            )
            painter.setFont(tokens.font_data(tokens.T_MICRO))
            painter.setPen(tokens.CY_100)
            painter.drawText(
                QRectF(cx - column / 2, 0, column, 13),
                Qt.AlignmentFlag.AlignHCenter, f"{day_high:.0f}°",
            )
        painter.end()


class BatteryMeter(_Instrument):
    """Charge as a pictogram plus a caption. The sample is already collected
    every tick and, until now, drawn nowhere."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._percent: float | None = None
        self._plugged: bool | None = None
        self._secs_left: int | None = None
        self._displayed = 0.0
        self.setMinimumHeight(26)

    @property
    def caption(self) -> str:
        if self._percent is None:
            return "NO BATTERY"
        if self._plugged:
            return "CHARGED" if self._percent >= 99.5 else "CHARGING"
        if self._secs_left:
            hours, minutes = divmod(int(self._secs_left) // 60, 60)
            return f"{hours}H {minutes:02d}M LEFT" if hours else f"{minutes}M LEFT"
        return "ON BATTERY"

    def set_state(self, percent: float | None, plugged: bool | None,
                  secs_left: int | None) -> None:
        self._percent, self._plugged, self._secs_left = percent, plugged, secs_left
        self.update()

    def advance(self, dt: float) -> None:
        target = (self._percent or 0.0) / 100.0
        if self._displayed != target:
            self._displayed = tokens.ease_toward(self._displayed, target, dt)
            self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        height = min(16.0, self.height() - 4.0)
        body = QRectF(1, (self.height() - height) / 2, 34, height)
        cap = QRectF(body.right(), body.center().y() - 3, 3, 6)

        accent = tokens.CY_500 if self._percent is None else tokens.threshold_color(
            1.0 - self._displayed  # a *low* battery is the dangerous end
        )
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(tokens.CY_500, 1.2))
        painter.drawRect(body)
        painter.fillRect(cap, tokens.CY_500)

        if self._percent is not None:
            fill = body.adjusted(2, 2, -2, -2)
            fill.setWidth(max(0.0, fill.width() * self._displayed))
            painter.fillRect(fill, accent)

        painter.setFont(tokens.font_data(tokens.T_LABEL))
        painter.setPen(tokens.CY_100 if self._percent is not None else tokens.CY_500)
        text = "--" if self._percent is None else f"{self._percent:.0f}%"
        painter.drawText(
            QRectF(body.right() + 8, 0, 46, self.height()),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, text,
        )
        painter.setFont(tokens.font_label(tokens.T_MICRO))
        painter.setPen(tokens.CY_500)
        painter.drawText(
            QRectF(0, 0, self.width() - 8, self.height()),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, self.caption,
        )
        painter.end()
