"""The straightforward component translations from ODIN-HUD.md §5: Panel,
Readout, BarMeter, DockButton, TickRuler. Each is a self-painted QWidget —
no QSS, no child-widget assembly beyond what a component itself needs.
"""
from __future__ import annotations

import math
from datetime import datetime

from PyQt6.QtCore import QEasingCurve, QEvent, QPointF, QPropertyAnimation, QRectF, QTimer, Qt, pyqtProperty
from PyQt6.QtGui import QColor, QFontMetrics, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import QAbstractButton, QVBoxLayout, QWidget

from . import tokens
from .icons import ICON_BOX, icon_path

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
    """One of the dock's circular launchers (§5.8).

    Three states it never used to have, each answering a question the dock
    couldn't before: `active` (CON and HAND are toggles and looked identical
    to the launchers whether on or off), `available` (a click during a turn
    is dropped by the window, and used to be swallowed in silence), and a
    launch flash (a dispatched command otherwise produced no feedback at all).

    Magnification is set by the owning Dock rather than by the button itself:
    the whole row has to agree on who's biggest.
    """

    DIAMETER = 76
    CELL_W = DIAMETER + 24
    MAX_MAG = 1.55
    CELL_H = int(DIAMETER * MAX_MAG) + 46
    BASELINE = 24            # pixels from the cell's bottom to the circle's
    FLASH_DECAY = 2.6        # per second

    def __init__(self, glyph: str, label: str, parent=None, dispatches: bool = True):
        super().__init__(parent)
        self._glyph = glyph
        self._label = label.upper()
        # Whether this button's action goes through the brain. The three that
        # don't (SET, CON, HAND) are handled entirely in the window and stay
        # live while a turn is running — see Dock.set_available.
        self.dispatches = dispatches
        self._icon = icon_path(glyph)
        self.resize(self.CELL_W, self.CELL_H)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setToolTip(self._label)

        self._magnification = 1.0
        self._magnification_target = 1.0
        self._active = False
        self._flash = 0.0

    # -- state ------------------------------------------------------------

    @property
    def glyph(self) -> str:
        return self._glyph

    @property
    def is_active(self) -> bool:
        return self._active

    def set_active(self, active: bool) -> None:
        active = bool(active)
        if active != self._active:
            self._active = active
            self.update()

    def set_available(self, available: bool) -> None:
        """Unavailable buttons dim and stop accepting clicks — Qt's own
        disabled handling does the second part, so there is no second code
        path that could disagree with what's drawn."""
        if bool(available) != self.isEnabled():
            self.setEnabled(bool(available))
            self.update()

    @property
    def launch_flash(self) -> float:
        return self._flash

    def flash_launch(self) -> None:
        self._flash = 1.0
        self.update()

    @property
    def magnification(self) -> float:
        return self._magnification

    def set_magnification(self, target: float) -> None:
        self._magnification_target = max(1.0, min(self.MAX_MAG, float(target)))

    def advance(self, dt: float) -> bool:
        """Ease toward the target magnification and decay any launch flash.
        Returns whether anything moved, so the Dock can skip re-laying out a
        row that has settled."""
        moved = False
        if self._magnification != self._magnification_target:
            self._magnification = tokens.ease_toward(self._magnification, self._magnification_target, dt)
            moved = True
        if self._flash > 0.0:
            self._flash = max(0.0, self._flash - self.FLASH_DECAY * dt)
            self.update()
        return moved

    def radius(self) -> float:
        return self.DIAMETER / 2 * self._magnification

    # -- input ------------------------------------------------------------

    def focusInEvent(self, event) -> None:
        # paintEvent's hasFocus() check alone isn't enough to make the
        # focus ring actually appear: Qt doesn't reliably schedule a
        # repaint on a widget's first-ever focus transition (verified by
        # instrumenting paintEvent — a bare setFocus() from no prior focus
        # produced zero repaints), which is exactly the case a keyboard
        # user hits pressing Tab into the dock for the first time.
        self.update()
        super().focusInEvent(event)

    def focusOutEvent(self, event) -> None:
        self.update()
        super().focusOutEvent(event)

    # -- painting ---------------------------------------------------------

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if not self.isEnabled():
            # One dimming for everything below, rather than a disabled
            # variant of each colour.
            painter.setOpacity(0.32)

        radius = self.radius()
        pressed = self.isDown()
        if pressed:
            radius *= 0.96
        centre = QPointF(self.width() / 2, self.height() - self.BASELINE - radius)
        hovered = self.underMouse() and self.isEnabled()

        if self._active:
            ring_color = tokens.CY_100
        elif hovered:
            ring_color = tokens.CY_300
        else:
            ring_color = tokens.CY_400

        def stroke_ring(pen) -> None:
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(centre, radius, radius)

        # Always-on glow, brighter on hover — reads as "powered" at rest
        # rather than a bare outline, matching the reference imagery's
        # instrument density instead of a plain flat ring.
        passes = 3 if (hovered or self._active) else 1
        tokens.draw_glow(painter, stroke_ring, ring_color, 2.0, passes=passes)

        # A thin secondary ring plus tick marks just outside it — a compact
        # echo of RadialGauge's tick scale.
        painter.setPen(QPen(QColor(tokens.CY_600), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(centre, radius + 5, radius + 5)
        for i in range(12):
            angle = math.radians(i * 30)
            cos_a, sin_a = math.cos(angle), math.sin(angle)
            painter.drawLine(
                QPointF(centre.x() + (radius + 5) * cos_a, centre.y() + (radius + 5) * sin_a),
                QPointF(centre.x() + (radius + 8) * cos_a, centre.y() + (radius + 8) * sin_a),
            )

        if self._flash > 0.0:
            # A ring bursting outward on dispatch: the command has left the
            # dock, even though the answer takes a moment.
            burst = QColor(tokens.CY_100)
            burst.setAlphaF(self._flash * 0.85)
            painter.setPen(QPen(burst, 2.0))
            painter.drawEllipse(centre, radius + 8 + (1.0 - self._flash) * 22,
                                radius + 8 + (1.0 - self._flash) * 22)

        inner = QColor(tokens.CY_600) if (pressed or self._active) else QColor(6, 28, 48, 160)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(inner)
        painter.drawEllipse(centre, radius - 3, radius - 3)

        self._paint_icon(painter, centre, radius, hovered)

        # The short code under the icon, inside the ring. The icon alone left
        # the dock unreadable to anyone who hadn't already learned it.
        painter.setFont(tokens.font_label(tokens.T_MICRO, bold=True))
        painter.setPen(tokens.CY_100 if (hovered or self._active) else tokens.CY_400)
        painter.drawText(
            QRectF(centre.x() - radius, centre.y() + radius * 0.30, radius * 2, radius * 0.42),
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
            self._glyph,
        )

        painter.setFont(tokens.font_label(tokens.T_MICRO))
        painter.setPen(tokens.CY_100 if (hovered or self._active) else tokens.CY_500)
        painter.drawText(
            QRectF(0, self.height() - 18, self.width(), 16),
            Qt.AlignmentFlag.AlignHCenter,
            self._label,
        )

        if self.hasFocus():
            painter.setPen(QPen(tokens.OK, 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(centre, radius + 10, radius + 10)
        painter.end()

    def _paint_icon(self, painter: QPainter, centre: QPointF, radius: float, hovered: bool) -> None:
        """The cached 24x24 path (ui/hud/icons.py), scaled to the circle and
        stroked — never rebuilt, only transformed."""
        span = radius * 0.95
        painter.save()
        painter.translate(centre.x() - span / 2, centre.y() - span / 2 - radius * 0.16)
        painter.scale(span / ICON_BOX, span / ICON_BOX)
        pen = QPen(tokens.CY_100 if (hovered or self._active) else tokens.CY_200, 1.7)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(self._icon)
        painter.restore()


class Dock(QWidget):
    """The row of launchers, laid out by hand rather than by a QHBoxLayout.

    The magnifier is why: each button's width follows its magnification, so
    the hovered one has to push its neighbours aside rather than grow over
    them. A layout manager would fight that every frame.
    """

    AMPLITUDE = 0.55     # how much the button under the cursor grows
    SIGMA = 1.15         # falloff, in base cell widths
    SETTLE_STEPS = 400   # cap on settle()'s loop; ~6.5s of eased motion

    def __init__(self, parent=None):
        super().__init__(parent)
        self.buttons: list[DockButton] = []
        self._cursor_x: float | None = None
        self._laid_out_width = -1
        self._pending_relax = False
        self.layout_passes = 0
        self.setMouseTracking(True)
        self.setFixedHeight(DockButton.CELL_H)

    def add_button(self, button: DockButton) -> None:
        button.setParent(self)
        button.setMouseTracking(True)
        # Qt delivers mouse moves to the child under the cursor, so the dock
        # itself only ever sees them in the gaps between buttons. The filter
        # is what makes hovering a button actually magnify it.
        button.installEventFilter(self)
        self.buttons.append(button)
        self._relayout()

    def button(self, glyph: str) -> DockButton | None:
        """The launcher with this glyph, if the dock has one — how the window
        reaches a single button to flash or light it."""
        for button in self.buttons:
            if button.glyph == glyph:
                return button
        return None

    def set_available(self, available: bool) -> None:
        """Dim the launchers while a turn is in flight — but only those. The
        local toggles never reach the brain, and disabling them left hand
        control unreachable for as long as a turn took."""
        for button in self.buttons:
            if button.dispatches:
                button.set_available(available)

    # -- magnification ------------------------------------------------------

    @property
    def cursor_x(self) -> float | None:
        return self._cursor_x

    def set_cursor_x(self, x: float | None) -> None:
        """`None` means the cursor has left the dock."""
        self._cursor_x = None if x is None else float(x)
        self._retarget()

    def _rest_centres(self) -> list[float]:
        """Where the buttons sit with nothing magnified. Targets are measured
        against these, not against live positions — otherwise growing a button
        moves it under the cursor, which moves its own target, and the row
        oscillates."""
        width = DockButton.CELL_W
        total = width * len(self.buttons)
        left = (self.width() - total) / 2
        return [left + width * (i + 0.5) for i in range(len(self.buttons))]

    def _retarget(self) -> None:
        if self._cursor_x is None:
            for button in self.buttons:
                button.set_magnification(1.0)
            return
        spread = self.SIGMA * DockButton.CELL_W
        for button, centre in zip(self.buttons, self._rest_centres()):
            distance = (self._cursor_x - centre) / spread
            button.set_magnification(1.0 + self.AMPLITUDE * math.exp(-0.5 * distance * distance))

    def advance(self, dt: float) -> None:
        if self._pending_relax:
            self._pending_relax = False
            if not (self.underMouse() or any(b.underMouse() for b in self.buttons)):
                self.set_cursor_x(None)
        # A width change is picked up here rather than relying on
        # resizeEvent: Qt only *posts* that event for a widget that has never
        # been shown, so anything measuring the row before the event loop
        # runs would otherwise read stale geometry.
        moved = self.width() != self._laid_out_width
        for button in self.buttons:
            if button.advance(dt):
                moved = True
        if moved:
            self._retarget()
            self._relayout()

    def settle(self) -> None:
        """Run the easing to completion — used by tests, and by anything that
        needs the row in its final geometry without waiting for frames."""
        for _ in range(self.SETTLE_STEPS):
            before = [b.magnification for b in self.buttons]
            self.advance(1 / 60)
            if [b.magnification for b in self.buttons] == before:
                return

    def _relayout(self) -> None:
        if not self.buttons:
            return
        self.layout_passes += 1
        self._laid_out_width = self.width()
        widths = [DockButton.CELL_W * b.magnification for b in self.buttons]
        x = (self.width() - sum(widths)) / 2
        for button, width in zip(self.buttons, widths):
            button.setGeometry(int(round(x)), 0, int(round(width)), self.height())
            x += width

    # -- input --------------------------------------------------------------

    def resizeEvent(self, event) -> None:
        self._retarget()
        self._relayout()
        super().resizeEvent(event)

    def mouseMoveEvent(self, event) -> None:
        self.set_cursor_x(event.position().x())
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:
        self._pending_relax = True
        super().leaveEvent(event)

    def eventFilter(self, watched, event) -> bool:
        kind = event.type()
        if kind in (QEvent.Type.MouseMove, QEvent.Type.Enter) and watched in self.buttons:
            local = event.position() if hasattr(event, "position") else None
            if local is not None:
                self.set_cursor_x(watched.mapToParent(local.toPoint()).x())
            else:
                self.set_cursor_x(watched.geometry().center().x())
        elif kind == QEvent.Type.Leave and watched in self.buttons:
            # Moving between two buttons fires Leave on the one being left
            # before Enter reaches the one being entered; relaxing here would
            # make the row stutter as the cursor glides along it. Defer the
            # decision to the next frame, by which point Qt's hover state has
            # settled and can answer honestly.
            self._pending_relax = True
        return False
