"""The Jarvis HUD.

Two windows, as designed:

  OrbWindow   a small frameless always-on-top arc reactor that sits on the
              desktop. Click it (or the tray icon, or the global hotkey) to
              summon the HUD.
  JarvisMainWindow
              the full-screen cinematic HUD — orb, conversation, input, and the
              confirmation and undo affordances the tiered risk model needs.

The visual language is glass over a dark translucent backdrop: panels are
semi-opaque fills with a single hairline border, and the only saturated colour
in the frame is whatever the orb is currently doing. Confirmation is the one
exception — it is meant to interrupt, so it is allowed to shout in amber.
"""
from PyQt6.QtCore import QEasingCurve, QPoint, QPropertyAnimation, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (
    QAction,
    QBrush,
    QColor,
    QIcon,
    QLinearGradient,
    QPainter,
    QPen,
    QPixmap,
)
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QGraphicsDropShadowEffect,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QPushButton,
    QScrollArea,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

import config
from core.undo import get_journal
from ui.orb import STATE_COLOR, ReactorOrb
from ui.panels import KnowledgeDialog, SettingsDialog
from ui.workers import BrainWorker

STATUS_FOR_STATE = {
    "idle": "STANDING BY",
    "listening": "LISTENING",
    "thinking": "WORKING",
    "acting": "EXECUTING",
    "speaking": "SPEAKING",
    "confirm": "AWAITING AUTHORISATION",
}

HUD_STYLESHEET = """
QWidget {
    color: #dbeafe;
    font-family: 'Segoe UI', 'Inter', -apple-system, sans-serif;
    font-size: 14px;
}
QLabel#wordmark {
    font-family: 'Consolas', 'JetBrains Mono', monospace;
    font-size: 22px;
    font-weight: 700;
    letter-spacing: 9px;
    color: #e0f2fe;
}
QLabel#chrome {
    font-family: 'Consolas', 'JetBrains Mono', monospace;
    font-size: 11px;
    letter-spacing: 2px;
    color: #64748b;
}
QLabel#statusText {
    font-family: 'Consolas', 'JetBrains Mono', monospace;
    font-size: 12px;
    letter-spacing: 3px;
    color: #7dd3fc;
}
QFrame#glass {
    background-color: rgba(15, 23, 42, 0.55);
    border: 1px solid rgba(148, 197, 255, 0.18);
    border-radius: 18px;
}
QScrollArea, QWidget#feed {
    background: transparent;
    border: none;
}
QScrollBar:vertical {
    background: transparent; width: 6px; margin: 6px 2px 6px 0;
}
QScrollBar::handle:vertical {
    background: rgba(125, 211, 252, 0.35); border-radius: 3px; min-height: 30px;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QFrame#userBubble {
    background-color: rgba(56, 189, 248, 0.16);
    border: 1px solid rgba(56, 189, 248, 0.40);
    border-radius: 14px;
}
QFrame#jarvisBubble {
    background-color: rgba(148, 163, 184, 0.10);
    border: 1px solid rgba(148, 163, 184, 0.22);
    border-radius: 14px;
}
QFrame#actionCard {
    background-color: rgba(56, 189, 248, 0.07);
    border: 1px solid rgba(56, 189, 248, 0.28);
    border-radius: 12px;
}
QFrame#activityLog {
    background-color: rgba(15, 23, 42, 0.38);
    border-left: 2px solid rgba(248, 113, 113, 0.55);
    border-radius: 6px;
}
QFrame#confirmBanner {
    background-color: rgba(251, 146, 60, 0.13);
    border: 1px solid rgba(251, 146, 60, 0.65);
    border-radius: 14px;
}
QLineEdit#inputField {
    background: transparent;
    border: none;
    font-size: 16px;
    color: #f0f9ff;
    padding: 6px 2px;
}
QPushButton {
    background-color: rgba(148, 163, 184, 0.10);
    border: 1px solid rgba(148, 197, 255, 0.22);
    border-radius: 14px;
    color: #bae6fd;
    font-size: 12px;
    letter-spacing: 1px;
    padding: 7px 16px;
}
QPushButton:hover {
    background-color: rgba(56, 189, 248, 0.20);
    border: 1px solid rgba(56, 189, 248, 0.60);
    color: #f0f9ff;
}
QPushButton#approveBtn {
    background-color: rgba(251, 146, 60, 0.22);
    border: 1px solid rgba(251, 146, 60, 0.85);
    color: #ffedd5;
    font-weight: 700;
}
QPushButton#approveBtn:hover { background-color: rgba(251, 146, 60, 0.42); }
QPushButton#declineBtn {
    background-color: rgba(148, 163, 184, 0.10);
    border: 1px solid rgba(148, 163, 184, 0.45);
    color: #e2e8f0;
}
"""


def _bubble(text: str, object_name: str, rich: bool) -> QFrame:
    """One chat bubble. Model output is rendered as plain text on purpose —
    anything Jarvis reads off the user's disk could otherwise inject markup."""
    frame = QFrame()
    frame.setObjectName(object_name)
    label = QLabel(text, frame)
    label.setTextFormat(Qt.TextFormat.RichText if rich else Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(16, 11, 16, 11)
    layout.addWidget(label)
    frame.label = label
    frame.setMaximumWidth(680)
    return frame


_ACTIVITY_PENDING_STYLE = "color: #7dd3fc; font-family: 'Consolas', monospace; font-size: 11px;"
_ACTIVITY_DONE_STYLE = "color: #64748b; font-family: 'Consolas', monospace; font-size: 11px;"
_ACTIVITY_ERROR_STYLE = "color: #fca5a5; font-family: 'Consolas', monospace; font-size: 11px;"


class ActivityLogWidget(QFrame):
    """Live trace of the tool calls made during one turn.

    A compound request ('open X, find Y, message them') can take a dozen-plus
    tool calls to finish. Without this, that whole stretch is a silent
    spinner; with it, each call appears the moment it starts and resolves in
    place once it finishes — the multi-step chain is visible as it happens,
    not just summarised afterward.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("activityLog")
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(14, 8, 14, 8)
        self._layout.setSpacing(3)

    def add_row(self, text: str) -> QLabel:
        label = QLabel(text, self)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        label.setStyleSheet(_ACTIVITY_PENDING_STYLE)
        self._layout.addWidget(label)
        return label


class ActionCardWidget(QFrame):
    """Shown when a MODERATE action completes: what happened, and a way back.

    The undo button only appears when the skill genuinely recorded a reversal —
    offering one for a keystroke or a closed window would be a lie."""

    undo_requested = pyqtSignal(str)

    def __init__(self, skill_name: str, token: str, description: str, parent=None):
        super().__init__(parent)
        self.setObjectName("actionCard")
        self.token = token

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 9, 14, 9)
        layout.setSpacing(12)

        caption = f"▸ {skill_name}"
        if description:
            caption += f" — {description}"
        label = QLabel(caption, self)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        label.setStyleSheet("color: #7dd3fc; font-family: 'Consolas', monospace; font-size: 12px;")
        layout.addWidget(label, 1)

        if token:
            button = QPushButton("UNDO", self)
            button.setObjectName("undoButton")
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(self._on_undo_click)
            layout.addWidget(button, 0)
        else:
            note = QLabel("can't be undone", self)
            note.setStyleSheet("color: #64748b; font-size: 11px;")
            layout.addWidget(note, 0)

    def _on_undo_click(self) -> None:
        self.undo_requested.emit(self.token)


class ConfirmationBannerWidget(QFrame):
    """The DANGEROUS tier's gate. Nothing is refused here — it just stops until
    a human says yes, and treats every other outcome as no."""

    answered = pyqtSignal(bool)

    def __init__(self, question: str, parent=None):
        super().__init__(parent)
        self.setObjectName("confirmBanner")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(18, 12, 18, 12)
        layout.setSpacing(12)

        label = QLabel(question, self)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        label.setStyleSheet("color: #fed7aa; font-size: 14px;")
        layout.addWidget(label, 1)

        approve = QPushButton("AUTHORISE", self)
        approve.setObjectName("approveBtn")
        approve.setCursor(Qt.CursorShape.PointingHandCursor)
        approve.clicked.connect(lambda: self.answered.emit(True))
        layout.addWidget(approve)

        decline = QPushButton("CANCEL", self)
        decline.setObjectName("declineBtn")
        decline.setCursor(Qt.CursorShape.PointingHandCursor)
        decline.clicked.connect(lambda: self.answered.emit(False))
        layout.addWidget(decline)


class _Backdrop(QWidget):
    """The HUD's ground: a dark translucent wash with a faint grid and corner
    brackets. Painted rather than styled so it can stay semi-transparent over
    whatever the user was already looking at."""

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = self.rect()

        wash = QLinearGradient(0, 0, 0, rect.height())
        wash.setColorAt(0.0, QColor(4, 8, 18, 238))
        wash.setColorAt(0.5, QColor(7, 13, 28, 232))
        wash.setColorAt(1.0, QColor(3, 6, 14, 242))
        painter.fillRect(rect, QBrush(wash))

        painter.setPen(QPen(QColor(56, 189, 248, 14), 1))
        for x in range(0, rect.width(), 64):
            painter.drawLine(x, 0, x, rect.height())
        for y in range(0, rect.height(), 64):
            painter.drawLine(0, y, rect.width(), y)

        painter.setPen(QPen(QColor(125, 211, 252, 110), 2))
        span, inset = 46, 22
        for x, y, dx, dy in (
            (inset, inset, 1, 1),
            (rect.width() - inset, inset, -1, 1),
            (inset, rect.height() - inset, 1, -1),
            (rect.width() - inset, rect.height() - inset, -1, -1),
        ):
            painter.drawLine(x, y, x + span * dx, y)
            painter.drawLine(x, y, x, y + span * dy)
        painter.end()


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


class JarvisMainWindow(QMainWindow):
    """The summonable full-screen HUD."""

    state_changed = pyqtSignal(str)  # so the desktop orb can mirror the HUD

    def __init__(self, brain, session, bridge=None, parent=None):
        super().__init__(parent)
        self.brain = brain
        self.session = session
        self.bridge = bridge
        self.current_worker = None
        self.is_hud_always_on_top = True
        self._banner = None
        self._live_label = None
        self._live_text: list[str] = []
        self._activity_widget: ActivityLogWidget | None = None
        self._pending_activity_row: QLabel | None = None

        self.setWindowTitle("Jarvis — Personal AI Desktop Assistant")
        self.resize(1180, 760)
        self.setMinimumSize(720, 520)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        self._init_ui()
        self._init_tray()
        self.setStyleSheet(HUD_STYLESHEET)

        if self.bridge is not None:
            self.bridge.text_chunk.connect(self._on_chunk)
            self.bridge.action_reported.connect(self.append_action_card)
            self.bridge.confirm_requested.connect(self._on_confirm_requested)
            self.bridge.tool_started.connect(self._on_tool_started)
            self.bridge.tool_finished.connect(self._on_tool_finished)

        self.append_jarvis_message(
            f"{config.ASSISTANT_NAME} online. I have the run of this machine — "
            "files, windows, the shell, and the keyboard. Ask away."
        )

    # -- construction ------------------------------------------------------

    def _init_ui(self) -> None:
        central = _Backdrop(self)
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(38, 30, 38, 30)
        outer.setSpacing(18)

        outer.addLayout(self._build_header())

        body = QHBoxLayout()
        body.setSpacing(24)
        body.addLayout(self._build_orb_column(), 0)
        body.addLayout(self._build_conversation_column(), 1)
        outer.addLayout(body, 1)

    def _build_header(self) -> QHBoxLayout:
        header = QHBoxLayout()
        header.setSpacing(18)

        wordmark = QLabel(config.ASSISTANT_NAME.upper(), self)
        wordmark.setObjectName("wordmark")
        header.addWidget(wordmark, 0)

        subtitle = QLabel(f"// {config.MODEL}", self)
        subtitle.setObjectName("chrome")
        header.addWidget(subtitle, 0)
        header.addStretch(1)

        # The live status lives under the orb, where the eye already is. Up here
        # goes the thing that doesn't change per turn: how you're talking to it.
        self.status_badge = QLabel("● TEXT", self)
        self.status_badge.setObjectName("chrome")
        header.addWidget(self.status_badge, 0)

        close_btn = QPushButton("ESC", self)
        close_btn.setToolTip("Hide the HUD (Esc)")
        close_btn.clicked.connect(self.dismiss)
        header.addWidget(close_btn, 0)
        return header

    def _build_orb_column(self) -> QVBoxLayout:
        column = QVBoxLayout()
        column.setSpacing(14)

        column.setContentsMargins(0, 0, 0, 0)

        self.orb = ReactorOrb(self)
        self.orb.setFixedSize(300, 300)
        column.addWidget(self.orb, 0, Qt.AlignmentFlag.AlignHCenter)

        self.status_text = QLabel(STATUS_FOR_STATE["idle"], self)
        self.status_text.setObjectName("statusText")
        self.status_text.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        column.addWidget(self.status_text, 0)

        for label, slot, tip in (
            ("UNDO", self.trigger_undo, "Reverse the last undoable action"),
            ("RESET", self.trigger_reset, "Clear conversation memory"),
            ("VOICE", self._toggle_mode, "Switch between voice and text"),
            ("KNOWLEDGE", self.open_knowledge_panel, "Browse or grow what I've deep-learned"),
            ("SETTINGS", self.open_settings_panel, "Registered skills and behaviour toggles"),
            ("HELP", self.trigger_help, "List the commands"),
        ):
            button = QPushButton(label, self)
            button.setToolTip(tip)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(slot)
            column.addWidget(button, 0)
            if label == "VOICE":
                self.mode_btn = button

        column.addStretch(1)
        return column

    def _build_conversation_column(self) -> QVBoxLayout:
        column = QVBoxLayout()
        column.setSpacing(14)

        panel = QFrame(self)
        panel.setObjectName("glass")
        self._apply_depth(panel)
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(10, 10, 10, 10)

        self.scroll_area = QScrollArea(panel)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.chat_container = QWidget()
        self.chat_container.setObjectName("feed")
        self.chat_layout = QVBoxLayout(self.chat_container)
        self.chat_layout.setContentsMargins(12, 12, 12, 12)
        self.chat_layout.setSpacing(12)
        self.chat_layout.addStretch(1)
        self.scroll_area.setWidget(self.chat_container)
        panel_layout.addWidget(self.scroll_area)
        column.addWidget(panel, 1)

        self.confirm_container = QVBoxLayout()
        self.confirm_container.setSpacing(8)
        column.addLayout(self.confirm_container, 0)

        input_panel = QFrame(self)
        input_panel.setObjectName("glass")
        self._apply_depth(input_panel)
        input_layout = QHBoxLayout(input_panel)
        input_layout.setContentsMargins(20, 8, 12, 8)
        input_layout.setSpacing(10)

        prompt = QLabel("▸", input_panel)
        prompt.setStyleSheet("color: #38bdf8; font-size: 17px;")
        input_layout.addWidget(prompt, 0)

        self.input_field = QLineEdit(input_panel)
        self.input_field.setObjectName("inputField")
        self.input_field.setPlaceholderText(f"Ask {config.ASSISTANT_NAME} anything, or type a command…")
        self.input_field.returnPressed.connect(self._on_send_click)
        input_layout.addWidget(self.input_field, 1)

        self.send_btn = QPushButton("SEND", input_panel)
        self.send_btn.setObjectName("sendBtn")
        self.send_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.send_btn.clicked.connect(self._on_send_click)
        input_layout.addWidget(self.send_btn, 0)

        column.addWidget(input_panel, 0)
        return column

    def _init_tray(self) -> None:
        self.tray_icon = QSystemTrayIcon(self)
        pixmap = QPixmap(32, 32)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(QPen(QColor(56, 189, 248), 3))
        painter.drawEllipse(QRectF(4, 4, 24, 24))
        painter.setBrush(QColor(224, 242, 254))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QRectF(12, 12, 8, 8))
        painter.end()
        self.tray_icon.setIcon(QIcon(pixmap))
        self.tray_icon.setToolTip(f"{config.ASSISTANT_NAME} — AI desktop assistant")

        menu = QMenu(self)
        for label, slot in (
            (f"Show {config.ASSISTANT_NAME}", self.show_and_activate),
            ("Toggle voice / text mode", self._toggle_mode),
            ("Pin HUD on top", self._toggle_hud_mode),
            ("Clear conversation", self.trigger_reset),
        ):
            action = QAction(label, self)
            action.triggered.connect(slot)
            menu.addAction(action)
        menu.addSeparator()
        quit_action = QAction("Quit", self)
        quit_action.triggered.connect(QApplication.instance().quit)
        menu.addAction(quit_action)

        self.tray_icon.setContextMenu(menu)
        self.tray_icon.activated.connect(self._on_tray_activated)
        self.tray_icon.show()

    # -- window behaviour --------------------------------------------------

    def show_and_activate(self) -> None:
        self.showFullScreen()
        self.raise_()
        self.activateWindow()
        self.orb.start()
        self.input_field.setFocus()

    def dismiss(self) -> None:
        """Hide the HUD and stop repainting it. The orb stays on the desktop."""
        self.orb.stop()
        self.hide()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.dismiss()
            return
        super().keyPressEvent(event)

    def _on_tray_activated(self, reason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.dismiss() if self.isVisible() else self.show_and_activate()

    def closeEvent(self, event) -> None:
        """The close button hides rather than exits — Jarvis is meant to stay
        resident. Quit from the tray menu."""
        if self.tray_icon is not None and self.tray_icon.isVisible():
            self.dismiss()
            event.ignore()
        else:
            event.accept()

    def _toggle_hud_mode(self) -> None:
        self.is_hud_always_on_top = not self.is_hud_always_on_top
        flags = self.windowFlags()
        if self.is_hud_always_on_top:
            self.setWindowFlags(flags | Qt.WindowType.WindowStaysOnTopHint)
        else:
            self.setWindowFlags(flags & ~Qt.WindowType.WindowStaysOnTopHint)
        if self.isVisible():
            self.show()

    # -- status ------------------------------------------------------------

    def set_status(self, state: str) -> None:
        """state is one of the orb's states, or 'confirm'."""
        self.status_text.setText(STATUS_FOR_STATE.get(state, state.upper()))
        self.orb.state = "thinking" if state == "confirm" else state
        self.state_changed.emit(self.orb.state)
        colour = STATE_COLOR.get(self.orb.state, STATE_COLOR["idle"])
        self.status_text.setStyleSheet(
            f"color: rgb({colour.red()},{colour.green()},{colour.blue()});"
            " font-family: 'Consolas', monospace; font-size: 12px; letter-spacing: 3px;"
        )

    # -- chat feed ---------------------------------------------------------

    def _insert(self, widget: QWidget, align_right: bool, share: int = 4) -> None:
        # A word-wrapped QLabel reports a tiny width hint, so a bubble added at
        # stretch 0 collapses into a column of two-word lines. Give it a share
        # of the row and let maximumWidth stop it from running the full width.
        row = QHBoxLayout()
        if align_right:
            row.addStretch(6 - share)
            row.addWidget(widget, share)
        else:
            row.addWidget(widget, share)
            row.addStretch(6 - share)
        self.chat_layout.insertLayout(self.chat_layout.count() - 1, row)
        self._fade_in(widget)
        self._scroll_to_bottom()

    @staticmethod
    def _fade_in(widget: QWidget) -> None:
        """New feed entries ease in rather than snapping into place — a small
        touch, but it's what tells a fast-scrolling activity trace apart from
        a static log dump."""
        effect = QGraphicsOpacityEffect(widget)
        widget.setGraphicsEffect(effect)
        anim = QPropertyAnimation(effect, b"opacity", widget)
        anim.setDuration(220)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        widget._fade_anim = anim  # keep a reference alive until it finishes
        anim.start()

    @staticmethod
    def _apply_depth(widget: QWidget) -> None:
        """A soft drop shadow behind the glass panels, so they read as
        floating over the backdrop rather than flat-painted onto it."""
        shadow = QGraphicsDropShadowEffect(widget)
        shadow.setBlurRadius(36)
        shadow.setOffset(0, 8)
        shadow.setColor(QColor(2, 6, 16, 160))
        widget.setGraphicsEffect(shadow)

    def append_user_message(self, text: str) -> QLabel:
        bubble = _bubble(text, "userBubble", rich=False)
        self._insert(bubble, align_right=True)
        return bubble.label

    def append_jarvis_message(self, text: str, rich: bool = False) -> QLabel:
        bubble = _bubble(text, "jarvisBubble", rich=rich)
        self._insert(bubble, align_right=False)
        return bubble.label

    # Kept so older call sites and tests keep working.
    append_odin_message = append_jarvis_message

    def append_action_card(self, skill_name: str, token: str, description: str) -> None:
        card = ActionCardWidget(skill_name, token, description, self)
        card.undo_requested.connect(self._execute_undo_by_token)
        self._insert(card, align_right=False, share=5)

    def show_confirmation_banner(self, question: str) -> ConfirmationBannerWidget:
        banner = ConfirmationBannerWidget(question, self)
        self.confirm_container.addWidget(banner)
        return banner

    def _scroll_to_bottom(self) -> None:
        # Deferred by one event-loop turn so the new row has been laid out and
        # the scrollbar's maximum is the real one. Calling processEvents() here
        # instead would re-enter the loop from inside a paint.
        def scroll():
            bar = self.scroll_area.verticalScrollBar()
            bar.setValue(bar.maximum())

        QTimer.singleShot(0, scroll)

    # -- commands ----------------------------------------------------------

    def _on_send_click(self) -> None:
        text = self.input_field.text().strip()
        if not text or not self.input_field.isEnabled():
            return
        self.input_field.clear()

        if text.startswith("/"):
            self._handle_slash_command(text)
            return

        self.append_user_message(text)
        self._process_user_turn(text)

    def _handle_slash_command(self, cmd: str) -> None:
        command = cmd.strip().lower()
        if command == "/undo":
            self.trigger_undo()
        elif command in ("/reset", "/forget"):
            self.trigger_reset()
        elif command == "/help":
            self.trigger_help()
        elif command == "/mode voice":
            self.append_jarvis_message(self.session.set_mode("voice"))
            self._sync_mode_button()
        elif command == "/mode text":
            self.append_jarvis_message(self.session.set_mode("text"))
            self._sync_mode_button()
        elif command in ("/quit", "/exit"):
            QApplication.instance().quit()
        else:
            self.append_jarvis_message(f"I don't know the command '{cmd}'.")
            self.trigger_help()

    def trigger_undo(self) -> None:
        entry = get_journal().latest()
        if entry is None:
            self.append_jarvis_message("There's nothing to undo.")
            return
        self._execute_undo_by_token(entry.token)

    def _execute_undo_by_token(self, token: str) -> None:
        try:
            self.append_jarvis_message(get_journal().undo(token))
        except Exception as e:  # noqa: BLE001 - a failed undo is a message, not a crash
            self.append_jarvis_message(f"I couldn't undo that: {e}")

    def trigger_reset(self) -> None:
        self.brain.reset()
        self.append_jarvis_message("Conversation memory cleared. Notes and reminders kept.")

    def trigger_help(self) -> None:
        self.append_jarvis_message(
            "<b>Commands</b><br>"
            "<code>/undo</code> — reverse the last undoable action<br>"
            "<code>/reset</code> — clear conversation memory<br>"
            "<code>/mode voice</code> · <code>/mode text</code> — switch input<br>"
            "<code>/quit</code> — exit<br><br>"
            "<b>Esc</b> hides the HUD; the orb stays on your desktop.",
            rich=True,
        )

    def open_settings_panel(self) -> None:
        SettingsDialog(self.brain, self).exec()

    def open_knowledge_panel(self) -> None:
        KnowledgeDialog(self).exec()

    def _toggle_mode(self) -> None:
        target = "voice" if self.session.mode == "text" else "text"
        self.append_jarvis_message(self.session.set_mode(target))
        self._sync_mode_button()

    def _sync_mode_button(self) -> None:
        voice = self.session.mode == "voice"
        self.mode_btn.setText("TEXT" if voice else "VOICE")
        self.status_badge.setText("● VOICE" if voice else "● TEXT")

    # -- one turn ----------------------------------------------------------

    def _process_user_turn(self, text: str) -> None:
        self.set_status("thinking")
        self._set_input_enabled(False)

        self._live_text = []
        self._live_label = self.append_jarvis_message("…")
        self._activity_widget = None
        self._pending_activity_row = None

        worker = BrainWorker(self.brain, text, parent=self)
        self.current_worker = worker
        worker.turn_finished.connect(self._on_turn_finished)
        worker.error_occurred.connect(self._on_turn_error)
        worker.start()

    def _set_input_enabled(self, enabled: bool) -> None:
        # Both, not just the button: Enter in the field would otherwise start a
        # second turn on top of the first and interleave the two conversations.
        self.input_field.setEnabled(enabled)
        self.send_btn.setEnabled(enabled)

    def _on_chunk(self, sentence: str) -> None:
        if self._live_label is None:
            self._live_label = self.append_jarvis_message("")
        self.set_status("speaking")
        self._live_text.append(sentence)
        self._live_label.setText(" ".join(self._live_text))
        self._scroll_to_bottom()

    def _on_tool_started(self, skill_name: str, args_brief: str) -> None:
        if self._activity_widget is None:
            self._activity_widget = ActivityLogWidget(self)
            self._insert(self._activity_widget, align_right=False, share=5)
        label = f"→ {skill_name}({args_brief})" if args_brief else f"→ {skill_name}"
        self._pending_activity_row = self._activity_widget.add_row(label)
        self._scroll_to_bottom()
        self.set_status("acting")

    def _on_tool_finished(self, skill_name: str, is_error: bool, result_brief: str) -> None:
        if self._pending_activity_row is not None:
            mark = "✗" if is_error else "✓"
            text = f"{mark} {skill_name}"
            if result_brief:
                text += f" — {result_brief}"
            self._pending_activity_row.setText(text)
            self._pending_activity_row.setStyleSheet(
                _ACTIVITY_ERROR_STYLE if is_error else _ACTIVITY_DONE_STYLE
            )
            self._pending_activity_row = None
        self.set_status("thinking")

    def _on_confirm_requested(self, question: str) -> None:
        self.set_status("confirm")
        self.show_and_activate()  # a decision is waiting; don't hide it behind the orb
        banner = self.show_confirmation_banner(question)
        self._banner = banner

        def answered(approved: bool):
            banner.setParent(None)
            banner.deleteLater()
            self._banner = None
            if self.bridge is not None:
                self.bridge.answer(approved)
            self.set_status("thinking")
            if not approved:
                self.append_jarvis_message("Cancelled.")

        banner.answered.connect(answered)

    def _on_turn_finished(self, reply: str) -> None:
        if reply and not self._live_text and self._live_label is not None:
            self._live_label.setText(reply)
        self._finish_turn()

    def _on_turn_error(self, message: str) -> None:
        if self._live_label is not None:
            self._live_label.setText(message)
        self._finish_turn()

    def _finish_turn(self) -> None:
        self._live_label = None
        self._live_text = []
        self.set_status("idle")
        self._set_input_enabled(True)
        self.input_field.setFocus()


# The project was briefly called ODIN; keep the old name importable.
OdinMainWindow = JarvisMainWindow
