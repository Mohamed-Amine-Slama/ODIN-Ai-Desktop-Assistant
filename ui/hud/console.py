"""ODIN CONSOLE — ODIN-HUD.md §6.10's typed-input overlay, the replacement
for the old chat-bubble feed. A convenience surface, not ODIN's primary
output: zone E's transcript ticker and the voice orb are driven
independently (UiBridge.text_chunk / VoiceListenWorker.heard) regardless of
whether this overlay is open.
"""
from __future__ import annotations

from collections import deque

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import QLineEdit, QVBoxLayout, QWidget

from . import tokens
from .widgets import Panel

SCROLLBACK_LINES = 6


class ConsoleOverlay(QWidget):
    """`submitted` fires for ordinary text (routed to Brain.ask());
    `slash_command` fires for a leading-`/` command, verbatim — same split
    the old chat HUD made in `_on_send_click`."""

    submitted = pyqtSignal(str)
    slash_command = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(760, 220)
        self.setVisible(False)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.panel = Panel("ODIN CONSOLE", self)
        outer.addWidget(self.panel)

        self._lines: deque[str] = deque(maxlen=SCROLLBACK_LINES)
        from PyQt6.QtWidgets import QLabel

        self._scrollback = QLabel("", self.panel.body)
        self._scrollback.setWordWrap(True)
        self._scrollback.setFont(tokens.font_data(tokens.T_BODY))
        self._scrollback.setStyleSheet(f"color: {tokens.CY_200.name()};")
        self.panel.body_layout.addWidget(self._scrollback, 1)

        self.input_field = QLineEdit(self.panel.body)
        self.input_field.setPlaceholderText("TYPE A COMMAND OR MESSAGE…")
        self.input_field.setFont(tokens.font_data(tokens.T_DATA))
        self.input_field.setStyleSheet(
            f"QLineEdit {{ background: transparent; border: none; border-top: 1px solid {tokens.CY_600.name()};"
            f" color: {tokens.CY_100.name()}; padding-top: 8px; }}"
        )
        self.input_field.returnPressed.connect(self._on_submit)
        self.panel.body_layout.addWidget(self.input_field, 0)

    def _on_submit(self) -> None:
        text = self.input_field.text().strip()
        if not text:
            return
        self.input_field.clear()
        self.echo(f"▸ {text}")
        if text.startswith("/"):
            self.slash_command.emit(text)
        else:
            self.submitted.emit(text)

    def echo(self, line: str) -> None:
        self._lines.append(line)
        self._scrollback.setText("\n".join(self._lines))

    def show_console(self) -> None:
        self.setVisible(True)
        self.raise_()
        self.input_field.setFocus()

    def hide_console(self) -> None:
        self.setVisible(False)

    def toggle(self) -> None:
        self.hide_console() if self.isVisible() else self.show_console()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.hide_console()
            return
        super().keyPressEvent(event)
