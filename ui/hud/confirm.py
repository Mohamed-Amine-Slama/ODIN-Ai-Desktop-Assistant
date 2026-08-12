"""ConfirmationBannerWidget — the DANGEROUS-tier gate, carried over from
ui/app_window.py's chat HUD. Repainted to the instrument HUD's own sharp-
cornered, bracket-framed visual language (§3.4) instead of the old rounded-
QSS chat styling, but the behavioral contract is identical: a question,
AUTHORISE/CANCEL, `answered(bool)`. Nothing is refused here — it just stops
until a human answers, and treats every non-yes as no.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton

from . import tokens


class ConfirmationBannerWidget(QFrame):
    answered = pyqtSignal(bool)

    def __init__(self, question: str, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.NoFrame)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(20, 14, 16, 14)
        layout.setSpacing(14)

        label = QLabel(question, self)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        label.setFont(tokens.font_data(tokens.T_BODY))
        label.setStyleSheet(f"color: {tokens.WARN.name()};")
        layout.addWidget(label, 1)

        approve = QPushButton("AUTHORISE", self)
        approve.setCursor(Qt.CursorShape.PointingHandCursor)
        approve.setStyleSheet(self._button_style(tokens.WARN))
        approve.clicked.connect(lambda: self.answered.emit(True))
        layout.addWidget(approve, 0)

        decline = QPushButton("CANCEL", self)
        decline.setCursor(Qt.CursorShape.PointingHandCursor)
        decline.setStyleSheet(self._button_style(tokens.CY_400))
        decline.clicked.connect(lambda: self.answered.emit(False))
        layout.addWidget(decline, 0)

    @staticmethod
    def _button_style(accent: QColor) -> str:
        return (
            f"QPushButton {{ background: rgba({accent.red()},{accent.green()},{accent.blue()},35);"
            f" border: 1px solid {accent.name()}; color: {accent.name()};"
            f" font-family: 'Share Tech Mono'; letter-spacing: 1px; padding: 8px 18px; }}"
            f"QPushButton:hover {{ background: rgba({accent.red()},{accent.green()},{accent.blue()},70); }}"
        )

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = self.rect()
        painter.fillRect(rect, tokens.PANEL_SOLID)
        painter.setPen(QPen(tokens.WARN, 1))
        painter.drawRect(rect.adjusted(0, 0, -1, -1))
        tokens.corner_ticks(painter, rect, tokens.WARN, alpha=255)
        painter.end()
