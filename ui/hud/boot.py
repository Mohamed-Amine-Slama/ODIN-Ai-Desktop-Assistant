"""Boot sequence — ODIN-HUD.md §8. A black cover with one expanding
hairline that then fades away over the already-built HUD, rather than the
full per-panel staggered choreography the spec's timing table lays out —
proportionate to this being explicitly the "least load-bearing" phase
(§9). Skipped entirely under `config.HUD_REDUCED_MOTION`, per §8's own
escape hatch — the window is simply shown at its final state.
"""
from __future__ import annotations

from PyQt6.QtCore import QEasingCurve, QPropertyAnimation, Qt, pyqtProperty
from PyQt6.QtGui import QPainter
from PyQt6.QtWidgets import QGraphicsOpacityEffect, QWidget

import config
from . import tokens


class _BootCover(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._line_frac = 0.0
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def getLineFrac(self) -> float:
        return self._line_frac

    def setLineFrac(self, value: float) -> None:
        self._line_frac = value
        self.update()

    lineFrac = pyqtProperty(float, getLineFrac, setLineFrac)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        rect = self.rect()
        painter.fillRect(rect, tokens.VOID)
        if self._line_frac > 0:
            half_width = rect.width() / 2 * self._line_frac
            cy = rect.height() / 2
            painter.setPen(tokens.CY_300)
            painter.drawLine(
                int(rect.center().x() - half_width), int(cy),
                int(rect.center().x() + half_width), int(cy),
            )
        painter.end()


def run_boot_sequence(window: QWidget) -> None:
    """`window`: the OdinHudWindow, already shown full-screen. Keeps its own
    animation objects alive via `window._boot_anims` so they aren't
    garbage-collected mid-flight."""
    if config.HUD_REDUCED_MOTION:
        return

    cover = _BootCover(window)
    cover.setGeometry(window.rect())
    cover.show()
    cover.raise_()

    line_anim = QPropertyAnimation(cover, b"lineFrac", cover)
    line_anim.setDuration(400)
    line_anim.setStartValue(0.0)
    line_anim.setEndValue(1.0)
    line_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    effect = QGraphicsOpacityEffect(cover)
    cover.setGraphicsEffect(effect)
    fade = QPropertyAnimation(effect, b"opacity", cover)
    fade.setDuration(900)
    fade.setStartValue(1.0)
    fade.setEndValue(0.0)
    fade.setEasingCurve(QEasingCurve.Type.InCubic)

    def cleanup() -> None:
        cover.hide()
        cover.deleteLater()
        window._boot_anims = None

    line_anim.finished.connect(fade.start)
    fade.finished.connect(cleanup)
    window._boot_anims = (cover, line_anim, fade)  # keep alive until cleanup()
    line_anim.start()
