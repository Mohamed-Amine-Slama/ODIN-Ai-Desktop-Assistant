"""Boot sequence — ODIN-HUD.md §8's full orchestrated startup (~2.6s):

  0.0s  A hairline expands across the vertical center.
  0.4s  It splits vertically outward — an iris wipe revealing the grid
        underneath, rather than a flat opacity fade.
  0.7s  Every panel (and the four gauges flanking the orb) fades up with a
        3px rise, staggered outward from screen center.
  ~1.7s The orb scales in from 0.85x and fades in.
  ~2.1s The core ignites — a flash to white settling back to the idle
        gradient — and the rings begin rotating for the first time.

Skipped entirely under `config.HUD_REDUCED_MOTION`, per §8's own escape
hatch — the window is simply shown at its final state, nothing hidden or
offset first.
"""
from __future__ import annotations

import math
from typing import Callable

from PyQt6.QtCore import QEasingCurve, QPoint, QPropertyAnimation, QRectF, Qt, QTimer, pyqtProperty
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import QGraphicsOpacityEffect, QWidget

import config
from . import tokens

HAIRLINE_MS = 400
IRIS_MS = 300
PANEL_STAGGER_MS = 40
PANEL_REVEAL_MS = 320
ORB_REVEAL_MS = 420
FLASH_MS = 500


def _safe(fn: Callable) -> Callable:
    """Swallow 'wrapped C/C++ object has been deleted' from a callback
    whose widget can legitimately outlive it — the window closing mid-boot
    with staggered QTimer.singleShot callbacks or animation .finished
    signals still pending. Every stage-transition callback below runs
    later, asynchronously, well outside the try/except a normal caller
    could wrap this call in."""
    def wrapper(*args, **kwargs):
        try:
            fn(*args, **kwargs)
        except RuntimeError:
            pass
    return wrapper


class _BootCover(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._line_frac = 0.0
        self._reveal_frac = 0.0
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def getLineFrac(self) -> float:
        return self._line_frac

    def setLineFrac(self, value: float) -> None:
        self._line_frac = value
        self.update()

    lineFrac = pyqtProperty(float, getLineFrac, setLineFrac)

    def getRevealFrac(self) -> float:
        return self._reveal_frac

    def setRevealFrac(self, value: float) -> None:
        self._reveal_frac = value
        self.update()

    revealFrac = pyqtProperty(float, getRevealFrac, setRevealFrac)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        rect = self.rect()
        cy = rect.height() / 2.0

        if self._reveal_frac >= 1.0:
            painter.end()
            return

        if self._reveal_frac <= 0.0:
            painter.fillRect(rect, tokens.VOID)
        else:
            # The cover shrinks from one full-screen fill to two bands
            # receding to the top/bottom edges — an iris wipe splitting
            # outward from the hairline's seam at vertical center.
            gap = cy * self._reveal_frac
            painter.fillRect(QRectF(0, 0, rect.width(), cy - gap), tokens.VOID)
            painter.fillRect(QRectF(0, cy + gap, rect.width(), cy - gap), tokens.VOID)
            edge = QColor(tokens.CY_300)
            edge.setAlphaF(max(0.0, 1.0 - self._reveal_frac))
            painter.setPen(QPen(edge, 2))
            painter.drawLine(0, int(cy - gap), rect.width(), int(cy - gap))
            painter.drawLine(0, int(cy + gap), rect.width(), int(cy + gap))

        if self._line_frac > 0 and self._reveal_frac <= 0.0:
            half_width = rect.width() / 2 * self._line_frac
            painter.setPen(tokens.CY_300)
            painter.drawLine(
                int(rect.center().x() - half_width), int(cy),
                int(rect.center().x() + half_width), int(cy),
            )
        painter.end()


def run_boot_sequence(window: QWidget) -> None:
    """`window`: the OdinHudWindow, already shown full-screen. Every
    animation/effect object involved is kept alive in `window._boot_anims`
    for the whole ~2.6s sequence, cleared only once it's fully done."""
    if config.HUD_REDUCED_MOTION:
        return

    keepalive: list[object] = []
    window._boot_anims = keepalive

    orb = getattr(window, "orb", None)
    if orb is not None:
        orb.boot_frozen = True
        orb.bootScale = 0.85
        orb_effect = QGraphicsOpacityEffect(orb)
        orb_effect.setOpacity(0.0)
        orb.setGraphicsEffect(orb_effect)
        keepalive.append(orb_effect)

    cover = _BootCover(window)
    cover.setGeometry(window.rect())
    cover.show()
    cover.raise_()
    keepalive.append(cover)

    line_anim = QPropertyAnimation(cover, b"lineFrac", cover)
    line_anim.setDuration(HAIRLINE_MS)
    line_anim.setStartValue(0.0)
    line_anim.setEndValue(1.0)
    line_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
    keepalive.append(line_anim)

    reveal_anim = QPropertyAnimation(cover, b"revealFrac", cover)
    reveal_anim.setDuration(IRIS_MS)
    reveal_anim.setStartValue(0.0)
    reveal_anim.setEndValue(1.0)
    reveal_anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
    keepalive.append(reveal_anim)

    def finish() -> None:
        window._boot_anims = None

    def start_ignition() -> None:
        if orb is None:
            finish()
            return
        _ignite_core(orb, keepalive, _safe(finish))

    def start_orb_reveal() -> None:
        if orb is None:
            finish()
            return
        _reveal_orb(orb, keepalive, _safe(start_ignition))

    def start_panel_reveal() -> None:
        cover.hide()
        cover.deleteLater()
        _reveal_panels(window, keepalive, _safe(start_orb_reveal))

    line_anim.finished.connect(_safe(reveal_anim.start))
    reveal_anim.finished.connect(_safe(start_panel_reveal))
    line_anim.start()


def _reveal_panels(window: QWidget, keepalive: list[object], on_done: Callable) -> None:
    """Every zone panel (plus the four gauges flanking the orb) fades up
    with a small rise, staggered outward from screen center — the "panel
    brackets draw in" / "panel contents fade up" beats of §8, merged into
    one wave per panel since Panel itself draws its brackets and body in a
    single paintEvent (splitting that would mean changing how every panel
    on the HUD renders, for a boot-only flourish)."""
    widgets = [
        w for w in getattr(window, "_boot_reveal_widgets", [])
        if w is not None and w.width() > 0 and w.height() > 0
    ]
    if not widgets:
        on_done()
        return

    center = window.rect().center()

    def distance(w: QWidget) -> float:
        c = w.mapTo(window, w.rect().center())
        return math.hypot(c.x() - center.x(), c.y() - center.y())

    widgets.sort(key=distance)

    remaining = len(widgets)

    def one_done() -> None:
        nonlocal remaining
        remaining -= 1
        if remaining <= 0:
            on_done()

    for i, widget in enumerate(widgets):
        effect = QGraphicsOpacityEffect(widget)
        effect.setOpacity(0.0)
        widget.setGraphicsEffect(effect)
        keepalive.append(effect)

        natural_pos = widget.pos()
        start_pos = QPoint(natural_pos.x(), natural_pos.y() + 3)

        opacity_anim = QPropertyAnimation(effect, b"opacity", widget)
        opacity_anim.setDuration(PANEL_REVEAL_MS)
        opacity_anim.setStartValue(0.0)
        opacity_anim.setEndValue(1.0)
        opacity_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        keepalive.append(opacity_anim)

        pos_anim = QPropertyAnimation(widget, b"pos", widget)
        pos_anim.setDuration(PANEL_REVEAL_MS)
        pos_anim.setStartValue(start_pos)
        pos_anim.setEndValue(natural_pos)
        pos_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        keepalive.append(pos_anim)

        def cleanup(w=widget) -> None:
            w.setGraphicsEffect(None)
            one_done()

        opacity_anim.finished.connect(_safe(cleanup))

        def start(o=opacity_anim, p=pos_anim, w=widget, sp=start_pos) -> None:
            w.move(sp)
            o.start()
            p.start()

        QTimer.singleShot(i * PANEL_STAGGER_MS, _safe(start))


def _reveal_orb(orb, keepalive: list[object], on_done: Callable) -> None:
    """Rings scale in 0.85 -> 1.0 from the orb's own center, fading in at
    the same time (§8, ~1.6s beat)."""
    effect = orb.graphicsEffect()

    opacity_anim = QPropertyAnimation(effect, b"opacity", orb)
    opacity_anim.setDuration(ORB_REVEAL_MS)
    opacity_anim.setStartValue(0.0)
    opacity_anim.setEndValue(1.0)
    opacity_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
    keepalive.append(opacity_anim)

    scale_anim = QPropertyAnimation(orb, b"bootScale", orb)
    scale_anim.setDuration(ORB_REVEAL_MS)
    scale_anim.setStartValue(0.85)
    scale_anim.setEndValue(1.0)
    scale_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
    keepalive.append(scale_anim)

    def cleanup() -> None:
        orb.setGraphicsEffect(None)
        on_done()

    opacity_anim.finished.connect(_safe(cleanup))
    opacity_anim.start()
    scale_anim.start()


def _ignite_core(orb, keepalive: list[object], on_done: Callable) -> None:
    """White flash settling back to the idle gradient; the rings start
    rotating from here, not from frame one (§8, ~2.1s beat)."""
    orb.boot_frozen = False

    flash_anim = QPropertyAnimation(orb, b"bootFlash", orb)
    flash_anim.setDuration(FLASH_MS)
    flash_anim.setStartValue(1.0)
    flash_anim.setEndValue(0.0)
    flash_anim.setEasingCurve(QEasingCurve.Type.InCubic)
    keepalive.append(flash_anim)

    flash_anim.finished.connect(_safe(on_done))
    flash_anim.start()
