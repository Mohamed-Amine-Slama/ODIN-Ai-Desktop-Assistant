"""The Jarvis HUD.

Two windows, as designed:

  OrbWindow   a small frameless always-on-top arc reactor that sits on the
              desktop. Click it (or the tray icon, or the global hotkey) to
              summon the HUD.
  JarvisMainWindow
              the full-screen cinematic HUD — orb, conversation, input, and the
              confirmation and undo affordances the tiered risk model needs.

The visual language is an instrument panel, not a chat app with a coat of
paint: dark glass, chamfered (cut-corner) panels rather than rounded
rectangles, corner-tick brackets framing each one, and a cyan/teal glow as
the resting accent colour — the orb's state colour is the one thing allowed
to diverge from it, since that is the one signal meant to grab the eye.
Confirmation is the other exception — it is meant to interrupt, so it is
allowed to shout in amber.
"""
import threading

from PyQt6.QtCore import QEasingCurve, QPoint, QPropertyAnimation, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (
    QAction,
    QBrush,
    QColor,
    QFont,
    QFontMetrics,
    QIcon,
    QLinearGradient,
    QPainter,
    QPainterPath,
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
from ui.workers import BrainWorker, VoiceListenWorker, VoiceSetupWorker

# The resting accent — everywhere except the orb, which speaks for itself.
HUD_ACCENT = QColor(34, 211, 238)


def _corner_ticks(
    painter: QPainter, rect, accent: QColor, span: int = 14, inset: int = 8,
    alpha: int = 200, width: float = 1.4,
) -> None:
    """Small L-bracket ticks inset from each corner — the instrument-panel
    signature common to all three reference HUDs. Used for the screen edges
    (subtle, on _Backdrop) and reused per-panel (more visible, on HudFrame)."""
    pen = QPen(QColor(accent.red(), accent.green(), accent.blue(), alpha), width)
    painter.setPen(pen)
    for x, y, dx, dy in (
        (rect.left() + inset, rect.top() + inset, 1, 1),
        (rect.right() - inset, rect.top() + inset, -1, 1),
        (rect.left() + inset, rect.bottom() - inset, 1, -1),
        (rect.right() - inset, rect.bottom() - inset, -1, -1),
    ):
        painter.drawLine(int(x), int(y), int(x + span * dx), int(y))
        painter.drawLine(int(x), int(y), int(x), int(y + span * dy))


class HudFrame(QFrame):
    """A glass panel with chamfered corners instead of a rounded rectangle,
    plus corner-tick brackets — in place of plain 'QFrame#glass' rounded
    rects. Paints its own fill/border/brackets, so any layout can still be
    installed on it normally; content margins just need to clear the chamfer.
    """

    def __init__(self, parent=None, chamfer: int = 16, accent: QColor | None = None, fill_alpha: int = 140):
        super().__init__(parent)
        self._chamfer = chamfer
        self._accent = accent or HUD_ACCENT
        self._fill_alpha = fill_alpha

    def _panel_path(self) -> QPainterPath:
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        c = self._chamfer
        path = QPainterPath()
        path.moveTo(rect.left() + c, rect.top())
        path.lineTo(rect.right() - c, rect.top())
        path.lineTo(rect.right(), rect.top() + c)
        path.lineTo(rect.right(), rect.bottom() - c)
        path.lineTo(rect.right() - c, rect.bottom())
        path.lineTo(rect.left() + c, rect.bottom())
        path.lineTo(rect.left(), rect.bottom() - c)
        path.lineTo(rect.left(), rect.top() + c)
        path.closeSubpath()
        return path

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        path = self._panel_path()

        painter.fillPath(path, QColor(9, 16, 28, self._fill_alpha))
        a = self._accent
        painter.setPen(QPen(QColor(a.red(), a.green(), a.blue(), 110), 1))
        painter.drawPath(path)

        _corner_ticks(painter, self.rect(), a)
        painter.end()


class _TickStrip(QWidget):
    """A thin row of dashes — pure ornament, echoing the dotted technical
    rows in the reference HUD imagery without adding real information
    density to a chat interface that doesn't have any to show."""

    def __init__(self, parent=None, accent: QColor | None = None):
        super().__init__(parent)
        self._accent = accent or HUD_ACCENT
        self.setFixedHeight(6)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        a = self._accent
        pen = QPen(QColor(a.red(), a.green(), a.blue(), 70), 2)
        pen.setCapStyle(Qt.PenCapStyle.FlatCap)
        painter.setPen(pen)
        y = self.height() // 2
        dash, gap = 5, 5
        x = 0
        while x < self.width():
            painter.drawLine(x, y, min(x + dash, self.width()), y)
            x += dash + gap
        painter.end()


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
    color: #d6f5f3;
    font-family: 'Segoe UI', 'Inter', -apple-system, sans-serif;
    font-size: 14px;
}
QLabel#wordmark {
    font-family: 'Consolas', 'JetBrains Mono', monospace;
    font-size: 22px;
    font-weight: 700;
    letter-spacing: 9px;
    color: #cffafe;
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
    color: #67e8f9;
}
QScrollArea, QWidget#feed {
    background: transparent;
    border: none;
}
QScrollBar:vertical {
    background: transparent; width: 6px; margin: 6px 2px 6px 0;
}
QScrollBar::handle:vertical {
    background: rgba(34, 211, 238, 0.35); border-radius: 3px; min-height: 30px;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QFrame#userBubble {
    background-color: rgba(34, 211, 238, 0.14);
    border: 1px solid rgba(34, 211, 238, 0.40);
    border-radius: 3px;
}
QFrame#jarvisBubble {
    background-color: rgba(148, 163, 184, 0.10);
    border: 1px solid rgba(148, 163, 184, 0.22);
    border-radius: 3px;
}
QFrame#actionCard {
    background-color: rgba(34, 211, 238, 0.07);
    border: 1px solid rgba(34, 211, 238, 0.28);
    border-radius: 3px;
}
QFrame#activityLog {
    background-color: rgba(9, 16, 28, 0.38);
    border-left: 2px solid rgba(248, 113, 113, 0.55);
    border-radius: 3px;
}
QFrame#confirmBanner {
    background-color: rgba(251, 146, 60, 0.13);
    border: 1px solid rgba(251, 146, 60, 0.65);
    border-radius: 3px;
}
QLineEdit#inputField {
    background: transparent;
    border: none;
    font-size: 16px;
    color: #ecfeff;
    padding: 6px 2px;
}
QPushButton {
    background-color: rgba(148, 163, 184, 0.10);
    border: 1px solid rgba(34, 211, 238, 0.25);
    border-radius: 3px;
    color: #a5f3fc;
    font-size: 12px;
    letter-spacing: 1px;
    padding: 7px 16px;
}
QPushButton:hover {
    background-color: rgba(34, 211, 238, 0.20);
    border: 1px solid rgba(34, 211, 238, 0.65);
    color: #ecfeff;
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


_BUBBLE_MIN_WIDTH = 48
# Ceiling for a roomy window; the real cap applied at creation time is
# whatever's actually available (see JarvisMainWindow._bubble_max_width) —
# this is only the upper bound of that, and the fallback before the window
# has been laid out even once.
_BUBBLE_MAX_WIDTH = 680
# Left unreserved on the stretch side so a bubble never claims the row's full
# width — without this, a bubble capped at exactly the available width has no
# room left for the stretch that's supposed to push it to one side, and a
# "right-aligned" bubble ends up flush with the left edge instead.
_BUBBLE_SIDE_RESERVE = 60
# Below this, the scroll area hasn't been through a real layout pass yet —
# comfortably above Qt's small pre-layout placeholder size, comfortably below
# the smallest viewport the enforced window minimum size can ever produce.
_BUBBLE_NOT_YET_LAID_OUT = 200
_BUBBLE_MARGINS = (16, 11, 16, 11)

# Matches HUD_STYLESHEET's QWidget rule. A freshly built bubble isn't parented
# into the styled window yet — it's plain QFrame()/QLabel() until _insert()
# adds it to the layout — so its own .fontMetrics() still reflects Qt's
# platform default font, not the 14px this stylesheet actually renders it in.
# Measuring against that explicit font instead sidesteps the timing entirely.
_BUBBLE_FONT = QFont("Segoe UI")
_BUBBLE_FONT.setPixelSize(14)


def _bubble_width_for(text: str, max_width: int = _BUBBLE_MAX_WIDTH) -> int:
    """Content-hugging bubble width: measured directly from the text and
    clamped between a sensible floor and the wrap width long messages need,
    rather than a share of the row's width — a stretch-based width made a
    two-letter reply ("hi") stretch to match a paragraph's box, leaving most
    of it empty. A word-wrapped QLabel's sizeHint() alone isn't a usable
    substitute (it reports close to its narrowest word with no stretch).

    max_width must reflect what the window can actually show right now — a
    caller passing the bare _BUBBLE_MAX_WIDTH constant on a panel narrower
    than that (a smaller window, a lower-resolution or scaled display) is
    exactly what let a long message overflow straight past the panel's own
    edge instead of wrapping within it.
    """
    metrics = QFontMetrics(_BUBBLE_FONT)
    longest_line = max((metrics.horizontalAdvance(line) for line in text.splitlines()), default=0)
    padding = _BUBBLE_MARGINS[0] + _BUBBLE_MARGINS[2]
    ceiling = max(_BUBBLE_MIN_WIDTH, min(max_width, _BUBBLE_MAX_WIDTH))
    return max(_BUBBLE_MIN_WIDTH, min(longest_line + padding + 8, ceiling))


def _resize_bubble(label: QLabel, text: str, max_width: int = _BUBBLE_MAX_WIDTH) -> None:
    """Re-fit a bubble already on screen to new text.

    A streamed reply or a placeholder ("…") swapped for an error message both
    call QLabel.setText() on a bubble that was already sized for its ORIGINAL
    text at creation — without this, the frame stays pinned to whatever width
    fit the placeholder, and real content wraps into a column one word-
    fragment wide instead of resizing to fit.
    """
    frame = label.parentWidget()
    if frame is not None:
        frame.setFixedWidth(_bubble_width_for(text, max_width))


def _bubble(text: str, object_name: str, rich: bool, max_width: int = _BUBBLE_MAX_WIDTH) -> QFrame:
    """One chat bubble. Model output is rendered as plain text on purpose —
    anything Jarvis reads off the user's disk could otherwise inject markup."""
    frame = QFrame()
    frame.setObjectName(object_name)
    label = QLabel(text, frame)
    label.setTextFormat(Qt.TextFormat.RichText if rich else Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(*_BUBBLE_MARGINS)
    layout.addWidget(label)
    frame.label = label
    frame.setFixedWidth(_bubble_width_for(text, max_width))
    return frame


_ACTIVITY_PENDING_STYLE = "color: #67e8f9; font-family: 'Consolas', monospace; font-size: 11px;"
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
        label.setStyleSheet("color: #67e8f9; font-family: 'Consolas', monospace; font-size: 12px;")
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

        painter.setPen(QPen(QColor(34, 211, 238, 14), 1))
        for x in range(0, rect.width(), 64):
            painter.drawLine(x, 0, x, rect.height())
        for y in range(0, rect.height(), 64):
            painter.drawLine(0, y, rect.width(), y)

        _corner_ticks(painter, rect, HUD_ACCENT, span=46, inset=22, alpha=110, width=2)
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
        self._voice_setup_worker: VoiceSetupWorker | None = None
        self._voice_loop_worker: VoiceListenWorker | None = None
        self._shown_once = False

        self.setWindowTitle(f"{config.ASSISTANT_NAME} — Personal AI Desktop Assistant")
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

        # Deliberately NOT appended here: the scroll area's viewport has no
        # real geometry until the window has actually been shown at least
        # once (Qt does not lay out hidden widgets), so a bubble sized now
        # would use a fallback width wider than this app's viewport ever
        # legitimately is — the exact overflow bug _bubble_max_width exists
        # to prevent, just hitting the one bubble created before any show().
        # show_and_activate() sends it the first time the HUD is actually
        # shown, when its real width is known.

    # -- construction ------------------------------------------------------

    def _init_ui(self) -> None:
        central = _Backdrop(self)
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(38, 30, 38, 30)
        outer.setSpacing(18)

        outer.addLayout(self._build_header())
        outer.addWidget(_TickStrip(central))

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

        # Live session telemetry — token usage from the last turn. Purely
        # informational chrome, echoing the small live readouts in the
        # reference HUD imagery; empty until the first turn completes.
        self.telemetry_label = QLabel("", self)
        self.telemetry_label.setObjectName("chrome")
        header.addWidget(self.telemetry_label, 0)

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

        panel = HudFrame(self, chamfer=18)
        self._apply_depth(panel)
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(14, 14, 14, 14)

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

        input_panel = HudFrame(self, chamfer=12)
        self._apply_depth(input_panel)
        input_layout = QHBoxLayout(input_panel)
        input_layout.setContentsMargins(20, 8, 12, 8)
        input_layout.setSpacing(10)

        prompt = QLabel("▸", input_panel)
        prompt.setStyleSheet("color: #22d3ee; font-size: 17px;")
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
        painter.setPen(QPen(QColor(34, 211, 238), 3))
        painter.drawEllipse(QRectF(4, 4, 24, 24))
        painter.setBrush(QColor(207, 250, 254))
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
        if not self._shown_once:
            self._shown_once = True
            # showFullScreen() only requests the show; the resize/layout it
            # triggers is delivered through the event loop, not synchronously.
            # Without pumping it here, the scroll area still has no real
            # geometry yet at this point, same as before any show() at all.
            QApplication.processEvents()
            # Sent here rather than in __init__: only now does the scroll
            # area have real geometry to size the bubble against.
            self.append_jarvis_message(
                f"{config.ASSISTANT_NAME} online. I have the run of this machine — "
                "files, windows, the shell, and the keyboard. Ask away."
            )

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

    def _bubble_max_width(self) -> int:
        """The widest a bubble can be right now without overflowing the
        actual conversation panel.

        _BUBBLE_MAX_WIDTH is only ever an upper bound; on a window/screen
        where the panel is narrower than that (a smaller HUD window, a
        lower-resolution or scaled display), capping at the bare constant let
        a long message overflow straight past the panel's own edge instead of
        wrapping within it. Falls back to the constant before the scroll area
        has ever been through a real layout pass — a not-yet-shown widget
        reports some small placeholder size (not reliably 0), which is why
        this checks against a threshold rather than an exact value. The
        window's enforced setMinimumSize(720, 520) puts the smallest possible
        REAL viewport width well above this threshold, so nothing legitimate
        can be mistaken for "not laid out yet".
        """
        viewport_width = self.scroll_area.viewport().width()
        if viewport_width < _BUBBLE_NOT_YET_LAID_OUT:
            return _BUBBLE_MAX_WIDTH
        return max(_BUBBLE_MIN_WIDTH, viewport_width - _BUBBLE_SIDE_RESERVE)

    def append_user_message(self, text: str) -> QLabel:
        bubble = _bubble(text, "userBubble", rich=False, max_width=self._bubble_max_width())
        # share=0: the bubble is already fixed-width (see _bubble); giving a
        # fixed-size widget a nonzero stretch factor alongside a
        # heightForWidth (word-wrapped) child is what was producing the
        # corrupted, overlapping row heights.
        self._insert(bubble, align_right=True, share=0)
        return bubble.label

    def append_jarvis_message(self, text: str, rich: bool = False) -> QLabel:
        bubble = _bubble(text, "jarvisBubble", rich=rich, max_width=self._bubble_max_width())
        self._insert(bubble, align_right=False, share=0)
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
            self._switch_to_voice()
        elif command == "/mode text":
            self._switch_to_text()
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
        if self.session.mode == "voice":
            self._switch_to_text()
        else:
            self._switch_to_voice()

    def _switch_to_text(self) -> None:
        self._stop_voice_loop()
        self.append_jarvis_message(self.session.set_mode("text"))
        self._sync_mode_button()

    def _switch_to_voice(self) -> None:
        # set_mode("voice") can mean loading STT/wake-word models for the
        # first time — seconds to real, network-bound minutes. Doing that on
        # the GUI thread is what used to freeze the whole HUD.
        if self._voice_setup_worker is not None:
            return
        self.mode_btn.setEnabled(False)
        self.append_jarvis_message("Starting microphone and loading speech models…")

        worker = VoiceSetupWorker(self.session, self)
        self._voice_setup_worker = worker
        worker.finished_ok.connect(self._on_voice_ready)
        worker.failed.connect(self._on_voice_setup_failed)
        worker.start()

    def _on_voice_ready(self, message: str) -> None:
        self._voice_setup_worker = None
        self.mode_btn.setEnabled(True)
        self.append_jarvis_message(message)
        self._sync_mode_button()
        self._start_voice_loop()

    def _on_voice_setup_failed(self, message: str) -> None:
        self._voice_setup_worker = None
        self.mode_btn.setEnabled(True)
        self.append_jarvis_message(message)
        self._sync_mode_button()

    def _start_voice_loop(self) -> None:
        worker = VoiceListenWorker(self.session, self)
        self._voice_loop_worker = worker
        worker.heard.connect(self._on_voice_heard)
        worker.state_changed.connect(self._on_voice_state)
        worker.start()

    def _stop_voice_loop(self) -> None:
        if self._voice_loop_worker is not None:
            self._voice_loop_worker.stop()
            self._voice_loop_worker.wait(2000)
            self._voice_loop_worker = None

    def _on_voice_state(self, state: str) -> None:
        # A turn already drives the orb's status once something was heard;
        # this only reflects the idle wait-for-wake-word / recording cycle.
        if self.session.mode == "voice" and self.current_worker is None:
            self.set_status(state)

    def _on_voice_heard(self, text: str) -> None:
        if self.session.mode != "voice":
            return
        self.append_user_message(text)
        self._process_user_turn(text)

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
            # A fresh bubble starts a fresh chronological block: the next
            # tool call should open its own activity widget after this text,
            # not keep appending to one left over from before this bubble
            # existed (which would put it out of chronological order).
            self._activity_widget = None
        self.set_status("speaking")
        self._live_text.append(sentence)
        full_text = " ".join(self._live_text)
        self._live_label.setText(full_text)
        _resize_bubble(self._live_label, full_text, self._bubble_max_width())
        self._scroll_to_bottom()

    def _retire_live_bubble(self) -> None:
        """Close out the current streaming bubble so the next narration
        starts a new one instead of growing this one for the rest of the
        turn.

        Without this, a single bubble accumulated every sentence spoken
        across an ENTIRE multi-tool-call task — sometimes dozens of
        sentences, including the model narrating its own mistakes and
        corrections — into one unbounded wall of text with no chronological
        relationship to the activity log entries interleaved between them.
        A widget that can grow to hundreds of lines, resized repeatedly, in a
        translucent frameless window with fade-in effects on every new row,
        is exactly the kind of thing that produces a garbled, overlapping
        layout.
        """
        if self._live_label is not None and not self._live_text:
            # Nothing was ever said into it (still the "…" placeholder, or
            # blank) — collapse it rather than leave a stray empty bubble.
            frame = self._live_label.parentWidget()
            if frame is not None:
                frame.setVisible(False)
        self._live_label = None
        self._live_text = []

    def _on_tool_started(self, skill_name: str, args_brief: str) -> None:
        self._retire_live_bubble()
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
        if reply and not self._live_text:
            # _live_label can be None here — the turn's last tool call
            # retired it and nothing streamed afterward — so the final reply
            # needs its own fresh bubble rather than being dropped.
            if self._live_label is None:
                self._live_label = self.append_jarvis_message(reply)
            else:
                self._live_label.setText(reply)
                _resize_bubble(self._live_label, reply, self._bubble_max_width())
                # append_jarvis_message (the branch above) already scrolls via
                # _insert; setText growing an existing bubble doesn't, so a
                # long final reply could otherwise land partly out of view.
                self._scroll_to_bottom()
        self._finish_turn()

    def _on_turn_error(self, message: str) -> None:
        if self._live_label is None:
            self._live_label = self.append_jarvis_message(message)
        else:
            self._live_label.setText(message)
            _resize_bubble(self._live_label, message, self._bubble_max_width())
            self._scroll_to_bottom()
        self._finish_turn()

    def _finish_turn(self) -> None:
        self._live_label = None
        self._live_text = []
        self.set_status("idle")
        self._set_input_enabled(True)
        self.input_field.setFocus()
        self._update_telemetry()
        if self._voice_loop_worker is not None:
            # Resume listening only once playback finishes — otherwise the
            # open mic hears the reply through the speakers and treats it as
            # the next thing said. speaker.wait() blocks, so it runs off the
            # GUI thread; it only touches thread-safe Events past this point.
            threading.Thread(target=self._resume_voice_after_speech, daemon=True).start()

    def _resume_voice_after_speech(self) -> None:
        self.session.speaker.wait(timeout=60)
        if self._voice_loop_worker is not None:
            self._voice_loop_worker.resume()

    def _update_telemetry(self) -> None:
        """Reflect the last turn's token usage in the header chrome. brain
        may be a test double whose .last_usage is a bare MagicMock rather
        than a real usage object — the isinstance checks are what keep that
        harmless instead of a crash on an unformattable value."""
        usage = getattr(self.brain, "last_usage", None)
        if usage is None:
            return
        total = getattr(usage, "total_tokens", None)
        if not isinstance(total, (int, float)):
            inp = getattr(usage, "input_tokens", None) or getattr(usage, "prompt_tokens", None)
            out = getattr(usage, "output_tokens", None) or getattr(usage, "completion_tokens", None)
            if isinstance(inp, (int, float)) and isinstance(out, (int, float)):
                total = inp + out
            else:
                return
        self.telemetry_label.setText(f"◈ {int(total):,} TOK")


# The project was briefly called ODIN; keep the old name importable.
OdinMainWindow = JarvisMainWindow
