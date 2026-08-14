"""The desktop orb.

OrbWindow: a small frameless always-on-top arc reactor that sits on the
desktop. Click it (or the tray icon, or the global hotkey) to summon the
full-screen HUD (ui/hud/window.py's OdinHudWindow).
"""
from PyQt6.QtCore import QPoint, Qt, pyqtSignal
from PyQt6.QtWidgets import QVBoxLayout, QWidget

import config
from ui.orb import ReactorOrb


class OrbWindow(QWidget):
    """The ambient presence: a small always-on-top orb, draggable, click to
    summon the HUD."""

    summoned = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(150, 150)
        self.setToolTip(f"{config.ASSISTANT_NAME} — click to open, drag to move")

        self.orb = ReactorOrb(self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.orb)

        self.orb.clicked.connect(self.summoned.emit)
        self._drag_from: QPoint | None = None

    def set_state(self, state: str) -> None:
        self.orb.state = state

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_from = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event) -> None:
        if self._drag_from is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_from)

    def mouseReleaseEvent(self, _event) -> None:
        self._drag_from = None
