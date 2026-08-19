"""Entry animation — the HUD powering itself on (ODIN-HUD.md §8, rebuilt).

The whole sequence is **one full-screen overlay driven by one animation**,
painted on top of a HUD that is already fully built and visible underneath.
The overlay's only job is to stop occluding things, in stages.

That structure is deliberate, and it is what the previous implementation got
wrong. It used to hide every panel, then re-show each one from its own
staggered `QTimer.singleShot` with a `QGraphicsOpacityEffect` attached. Two
consequences followed. Correctness: any interruption — Esc, a re-summon, the
window closing — left a scatter of pending timers that either popped panels
back in later or never ran at all, leaving them invisible. Performance: an
attached opacity effect forces its widget through an offscreen-composited
repaint on every update, and the orb repaints continuously.

Here, nothing is ever hidden, moved, or given a graphics effect. Cancelling is
therefore total and instant: delete one widget, and what's underneath is
already correct. There is exactly one clock, so there is nothing to leave
half-finished.

  0.0s  Charge — black, a hairline tracing the vertical center, flickering.
  0.8s  Iris — the cover splits outward; the grid ignites and races out from
        center with it.
  1.6s  Assembly — each panel is uncovered by a wipe with a bright leading
        edge, staggered outward from the center of the screen.
  3.0s  The orb traces itself into place, ring by ring, and the molecule
        condenses into it mote by mote (ui/hud/voice_orb.py's bootReveal).
  5.3s  Ignition — the core flashes and a shockwave ring bursts outward.

Skipped entirely under `config.HUD_REDUCED_MOTION`, per §8's own escape hatch.
Re-summoning the HUD later runs `run_reentry_flourish` instead: a 250ms scan
pass that never touches the orb, so the assembly plays once per launch and
cannot replay.
"""
from __future__ import annotations

import math

from PyQt6.QtCore import QEasingCurve, QPointF, QPropertyAnimation, QRect, QRectF, Qt, pyqtProperty
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import QWidget

import config
from . import tokens

ENTRY_MS = 6000
REENTRY_MS = 250

# Stage windows along the overlay's single 0..1 progress. They overlap: a
# stage starts while the one before it is still finishing, which is what keeps
# a six-second sequence from feeling like six separate events.
CHARGE = (0.00, 0.13)
IRIS = (0.10, 0.27)
GRID = (0.16, 0.42)
PANELS = (0.24, 0.60)
PANEL_SPAN = 0.16        # how much of the reveal one panel's own wipe takes
ORB = (0.48, 0.92)
IGNITION = (0.86, 1.00)

GRID_PITCH = 40          # matches _Backdrop's mesh in ui/hud/zones.py


def _smoothstep(window: tuple[float, float], value: float) -> float:
    lo, hi = window
    if value <= lo:
        return 0.0
    if value >= hi:
        return 1.0
    t = (value - lo) / (hi - lo)
    return t * t * (3.0 - 2.0 * t)


class _EntryOverlay(QWidget):
    """The entire entry animation: one widget, one property, one clock."""

    def __init__(self, window: QWidget, duration_ms: int, reentry: bool = False):
        super().__init__(window)
        self._window = window
        self._orb = None if reentry else getattr(window, "orb", None)
        self._reentry = reentry
        self._finished = False
        self._progress = 0.0
        self.duration_ms = duration_ms

        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setGeometry(window.rect())
        self._panels = self._panel_schedule()

        self.show()
        self.raise_()

        self._anim = QPropertyAnimation(self, b"progress", self)
        self._anim.setDuration(duration_ms)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setEasingCurve(QEasingCurve.Type.Linear)

    # -- schedule ----------------------------------------------------------

    def _panel_schedule(self) -> list[tuple[QRect, tuple[float, float]]]:
        """Each panel's rect in overlay coordinates plus its own slice of the
        assembly stage, ordered outward from the center of the screen."""
        if self._reentry:
            return []
        widgets = [
            w for w in getattr(self._window, "_boot_reveal_widgets", [])
            if w is not None and w.width() > 0 and w.height() > 0
        ]
        center = self._window.rect().center()

        def distance(widget: QWidget) -> float:
            middle = widget.mapTo(self._window, widget.rect().center())
            return math.hypot(middle.x() - center.x(), middle.y() - center.y())

        widgets.sort(key=distance)
        lo, hi = PANELS
        last_start = max(lo, hi - PANEL_SPAN)
        step = (last_start - lo) / max(1, len(widgets) - 1)
        schedule = []
        for i, widget in enumerate(widgets):
            top_left = widget.mapTo(self._window, widget.rect().topLeft())
            rect = QRect(top_left, widget.size())
            start = lo + i * step
            schedule.append((rect, (start, start + PANEL_SPAN)))
        return schedule

    # -- the one clock ------------------------------------------------------

    def start(self) -> None:
        self._anim.start()

    def getProgress(self) -> float:
        return self._progress

    def setProgress(self, value: float) -> None:
        self._progress = max(0.0, min(1.0, value))
        self._drive_orb()
        self.update()
        if self._progress >= 1.0:
            self.finish()

    progress = pyqtProperty(float, getProgress, setProgress)

    def _drive_orb(self) -> None:
        orb = self._orb
        if orb is None:
            return
        reveal = _smoothstep(ORB, self._progress)
        orb.bootReveal = reveal
        orb.bootScale = 0.94 + 0.06 * reveal
        # Rings hold still until the molecule has condensed — the drift
        # starting mid-assembly would fight the arrival.
        orb.boot_frozen = reveal < 1.0
        # A flash that swells and falls across the ignition window rather than
        # snapping on: sin() over the stage, not a ramp.
        ignition = _smoothstep(IGNITION, self._progress)
        orb.bootFlash = math.sin(math.pi * ignition) if ignition > 0.0 else 0.0

    def finish(self) -> None:
        """The single exit. Natural completion and cancellation both land
        here, so there is only one definition of 'finished'."""
        if self._finished:
            return
        self._finished = True
        self._anim.stop()

        orb = self._orb
        if orb is not None:
            orb.bootReveal = 1.0
            orb.bootScale = 1.0
            orb.bootFlash = 0.0
            orb.boot_frozen = False

        if getattr(self._window, "_entry_overlay", None) is self:
            self._window._entry_overlay = None
        self.hide()
        self.deleteLater()

    # -- painting -----------------------------------------------------------

    def paintEvent(self, _event) -> None:
        if self._finished:
            return
        painter = QPainter(self)
        if self._reentry:
            self._paint_reentry(painter)
            painter.end()
            return

        self._paint_cover(painter)
        self._paint_grid_ignition(painter)
        self._paint_panel_covers(painter)
        self._paint_shockwave(painter)
        painter.end()

    def _paint_cover(self, painter: QPainter) -> None:
        """Void over everything, splitting outward from the center seam."""
        iris = _smoothstep(IRIS, self._progress)
        if iris >= 1.0:
            return
        rect = self.rect()
        middle = rect.height() / 2.0
        gap = middle * iris
        painter.fillRect(QRectF(0, 0, rect.width(), middle - gap), tokens.VOID)
        painter.fillRect(QRectF(0, middle + gap, rect.width(), middle - gap), tokens.VOID)

        edge = QColor(tokens.CY_300)
        if iris > 0.0:
            edge.setAlphaF(max(0.0, 1.0 - iris))
            painter.setPen(QPen(edge, 2))
            painter.drawLine(0, int(middle - gap), rect.width(), int(middle - gap))
            painter.drawLine(0, int(middle + gap), rect.width(), int(middle + gap))
            return

        charge = _smoothstep(CHARGE, self._progress)
        if charge <= 0.0:
            return
        # Flicker: the line stutters as it charges, steadying as it completes.
        stutter = 0.55 + 0.45 * math.sin(charge * 46.0)
        edge.setAlphaF(min(1.0, 0.35 + 0.65 * charge) * (stutter if charge < 0.75 else 1.0))
        half = rect.width() / 2.0 * charge
        painter.setPen(QPen(edge, 2))
        painter.drawLine(
            int(rect.center().x() - half), int(middle),
            int(rect.center().x() + half), int(middle),
        )

    def _paint_grid_ignition(self, painter: QPainter) -> None:
        """The backdrop's own mesh, lit up brightly inside a front racing out
        from the center, fading as it goes — the grid coming online."""
        grid = _smoothstep(GRID, self._progress)
        if grid <= 0.0 or grid >= 1.0:
            return
        rect = self.rect()
        front = math.hypot(rect.width(), rect.height()) * 0.5 * grid
        color = QColor(tokens.CY_400)
        color.setAlphaF(0.5 * math.sin(math.pi * grid))

        painter.save()
        painter.setClipRect(QRectF(
            rect.center().x() - front, rect.center().y() - front, front * 2, front * 2
        ))
        painter.setPen(QPen(color, 1))
        for x in range(0, rect.width(), GRID_PITCH):
            painter.drawLine(x, 0, x, rect.height())
        for y in range(0, rect.height(), GRID_PITCH):
            painter.drawLine(0, y, rect.width(), y)
        painter.restore()

    def _paint_panel_covers(self, painter: QPainter) -> None:
        """Each panel is uncovered top-down by a retreating void, with a
        bright edge riding the wipe and brackets snapping in behind it."""
        for rect, window in self._panels:
            fraction = _smoothstep(window, self._progress)
            if fraction >= 1.0:
                continue
            covered = rect.height() * (1.0 - fraction)
            top = rect.bottom() - covered
            painter.fillRect(QRectF(rect.left(), top, rect.width(), covered), tokens.VOID)
            if fraction > 0.0:
                edge = QColor(tokens.CY_200)
                edge.setAlphaF(0.85)
                painter.setPen(QPen(edge, 1.5))
                painter.drawLine(int(rect.left()), int(top), int(rect.right()), int(top))
                tokens.corner_ticks(painter, rect, tokens.CY_300, alpha=int(200 * fraction))

    def _paint_shockwave(self, painter: QPainter) -> None:
        """One expanding ring out of the orb at ignition. Painted here rather
        than by its own widget: the overlay is already on top of everything
        and already cleans itself up, so the ring cannot outlive the entry."""
        ignition = _smoothstep(IGNITION, self._progress)
        if ignition <= 0.0 or ignition >= 1.0 or self._orb is None:
            return
        orb = self._orb
        side = min(orb.width(), orb.height())
        if side <= 0:
            return
        scale = side / orb.REF
        center = QPointF(orb.mapTo(self._window, orb.rect().topLeft())) + QPointF(
            (orb.width() - side) / 2 + orb.CENTER * scale,
            (orb.height() - side) / 2 + orb.CENTER * scale,
        )
        radius = orb.R_OUTER * scale * (1.0 + 0.9 * ignition)
        fade = 1.0 - ignition

        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        def stroke(pen: QPen) -> None:
            color = pen.color()
            color.setAlphaF(color.alphaF() * fade)
            pen.setColor(color)
            painter.setPen(pen)
            painter.drawEllipse(center, radius, radius)

        # 2 passes, not 3: the third costs ~0.9ms of a 60fps frame on a ring
        # this large, and it is indistinguishable on something that expands
        # and fades out inside a second.
        tokens.draw_glow(painter, stroke, tokens.CY_100, 2.5, passes=2)

    def _paint_reentry(self, painter: QPainter) -> None:
        """Re-summon: a bright band sweeping down over a veil that lifts. No
        stages, no orb involvement — nothing here can rebuild the HUD."""
        progress = self._progress
        rect = self.rect()
        veil = QColor(tokens.VOID)
        veil.setAlphaF(max(0.0, 0.55 * (1.0 - progress)))
        painter.fillRect(rect, veil)

        band_y = rect.height() * progress
        band = QColor(tokens.CY_200)
        band.setAlphaF(0.5 * (1.0 - progress))
        painter.setPen(QPen(band, 2))
        painter.drawLine(0, int(band_y), rect.width(), int(band_y))


def _start(window: QWidget, duration_ms: int, reentry: bool) -> None:
    if config.HUD_REDUCED_MOTION:
        return
    if getattr(window, "_entry_overlay", None) is not None:
        return  # already running — never stack two entries
    overlay = _EntryOverlay(window, duration_ms, reentry=reentry)
    window._entry_overlay = overlay
    overlay.setProgress(0.0)
    overlay.start()


def run_boot_sequence(window: QWidget) -> None:
    """The full power-on assembly. Runs once per launch; the window guards
    that with its own `_shown_once`, and this guards against overlapping."""
    _start(window, ENTRY_MS, reentry=False)


def run_reentry_flourish(window: QWidget) -> None:
    """The 250ms flourish for re-summoning an already-booted HUD."""
    _start(window, REENTRY_MS, reentry=True)


def cancel_entry_animation(window: QWidget) -> None:
    """Stop whatever is running and jump to the finished state. Safe to call
    when nothing is running, and safe to call twice."""
    overlay = getattr(window, "_entry_overlay", None)
    if overlay is not None:
        overlay.finish()
