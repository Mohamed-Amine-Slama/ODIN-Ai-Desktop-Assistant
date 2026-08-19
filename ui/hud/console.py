"""ODIN CONSOLE — ODIN-HUD.md §6.10's typed-input overlay, the replacement
for the old chat-bubble feed. A convenience surface, not ODIN's primary
output: zone E's transcript ticker and the voice orb are driven
independently (UiBridge.text_chunk / VoiceListenWorker.heard) regardless of
whether this overlay is open.
"""
from __future__ import annotations

from collections import deque

from PyQt6.QtCore import QEvent, QObject, QPoint, Qt, pyqtSignal
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import QLineEdit, QVBoxLayout, QWidget

from . import tokens
from .widgets import Panel

SCROLLBACK_LINES = 6
# Matches Panel's own private _TITLE_H (widgets.py) — the band above the
# hairline rule where Panel paints its own background rather than a child
# widget, so it's the only region that can hand mouse events to us instead
# of the scrollback label or input field underneath.
_DRAG_HANDLE_HEIGHT = 20


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

        # Drag state: set on a press inside the title band (see eventFilter
        # below), cleared on release. None means "not dragging."
        self._drag_offset: QPoint | None = None
        self.panel.installEventFilter(self)

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

    @property
    def is_open(self) -> bool:
        """Whether the console has been opened, independent of whether the
        HUD around it happens to be on screen. Qt's isVisible() is False for
        any child of a hidden window, so keying the toggle off it made the
        console stick open whenever the HUD wasn't showing."""
        return not self.isHidden()

    def toggle(self) -> None:
        self.hide_console() if self.is_open else self.show_console()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.hide_console()
            return
        super().keyPressEvent(event)

    # -- dragging --------------------------------------------------------
    # This is a plain QWidget floating over the HUD's root widget, not an
    # OS-level window, so there's no title bar to drag by default — Qt gives
    # window-manager dragging for free only to real top-level windows.
    # Panel (self.panel) fills this widget's entire rect via a zero-margin
    # layout, so it — not self — is what actually receives mouse events; an
    # event filter on it is the only way to intercept the title band's
    # clicks before Panel (which has no mouse handling of its own) ignores
    # them.

    def eventFilter(self, obj: QObject, event) -> bool:
        if obj is self.panel:
            if (
                event.type() == QEvent.Type.MouseButtonPress
                and event.button() == Qt.MouseButton.LeftButton
                and event.position().y() <= _DRAG_HANDLE_HEIGHT
            ):
                self._drag_offset = event.position().toPoint()
                return True
            if event.type() == QEvent.Type.MouseMove and self._drag_offset is not None:
                if event.buttons() & Qt.MouseButton.LeftButton:
                    self._move_clamped(event.position().toPoint() - self._drag_offset)
                else:
                    self._drag_offset = None
                return True
            if event.type() == QEvent.Type.MouseButtonRelease and self._drag_offset is not None:
                self._drag_offset = None
                return True
        return super().eventFilter(obj, event)

    def _move_clamped(self, delta: QPoint) -> None:
        parent = self.parentWidget()
        target = self.pos() + delta
        if parent is not None:
            max_x = max(parent.width() - self.width(), 0)
            max_y = max(parent.height() - self.height(), 0)
            target.setX(min(max(target.x(), 0), max_x))
            target.setY(min(max(target.y(), 0), max_y))
        self.move(target)
