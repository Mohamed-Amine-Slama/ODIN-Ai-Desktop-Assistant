"""OdinHudWindow — ODIN-HUD.md §4's full-screen instrument HUD, assembled
from the zones defined in ui/hud/layout.py. Replaces the old chat-bubble
JarvisMainWindow as ODIN's one HUD; the small always-on-top ambient
OrbWindow (ui/app_window.py) is unrelated and untouched.
"""
from __future__ import annotations

import getpass
import queue
import threading
import time
from collections import deque
from datetime import datetime

from PyQt6.QtCore import QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QColor, QIcon, QPainter, QPen, QPixmap, QRadialGradient
from PyQt6.QtWidgets import (
    QApplication,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QPushButton,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

import random

import config
from core import learning_status
from core.store import get_store
from core.undo import get_journal
from ui.hud import layout, tokens
from ui.hud.boot import run_boot_sequence
from ui.hud.confirm import ConfirmationBannerWidget
from ui.hud.console import ConsoleOverlay
from ui.hud.radial_gauge import RadialGauge
from ui.hud.sparkline import Sparkline
from ui.hud.spectrum import Spectrum
from ui.hud.telemetry import TelemetryFrame, TelemetryWorker
from ui.hud.voice_orb import VoiceOrb
from ui.hud.weather import WeatherSample, WeatherWorker
from ui.hud.widgets import BarMeter, DockButton, Panel, Readout, TickRuler
from ui.workers import BrainWorker, SkillLogEntry, UiBridge, VoiceListenWorker, VoiceSetupWorker

ANIMATION_FPS = 30
STATUS_FOR_STATE = {
    "idle": "STANDING BY",
    "listening": "LISTENING",
    "thinking": "PROCESSING",
    "speaking": "SPEAKING",
    "learning": "LEARNING",
    "error": "ERROR",
    "confirm": "AWAITING AUTHORISATION",
}

# (glyph, label, preset command) — None means "handled locally", never sent
# to Brain.ask() (ODIN-HUD.md §6.10).
DOCK_ITEMS = [
    ("EXP", "Explorer", "open file explorer"),
    ("WEB", "Browser", "open my default browser"),
    ("TERM", "Terminal", "open a terminal"),
    ("CODE", "Code", "open vs code"),
    ("MUS", "Music", "open spotify"),
    ("SET", "Settings", None),
    ("SYS", "Task Manager", "open task manager"),
    ("SNAP", "Screenshot", "take a screenshot"),
    ("CON", "ODIN Console", None),
]

# Orb ring launchers (§6.7) — a different 8-label set from the dock.
LAUNCHER_PRESETS = {
    "SYS": "open task manager",
    "FILES": "open file explorer",
    "WEB": "open my default browser",
    "CODE": "open vs code",
    "MUSIC": "open spotify",
    "VOL": "open the volume control",
    "LEARN": None,  # opens the knowledge dialog locally
    "PWR": "open the Windows power options",
}


class _CoreStrip(QWidget):
    """A compact multi-core load meter (§6.2's "per-core BarMeter stack",
    condensed to fit zone B's real footprint): one thin vertical bar per
    core, height-encoded, threshold-colored — not 16 full-width BarMeters,
    which would not fit the panel's allotted height."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._values: list[float] = []
        self.setFixedHeight(14)

    def set_values(self, values: list[float]) -> None:
        self._values = values
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        rect = self.rect()
        painter.fillRect(rect, tokens.CY_700)
        if not self._values:
            painter.end()
            return
        n = len(self._values)
        gap = 2
        bar_w = max(1.0, (rect.width() - gap * (n - 1)) / n)
        for i, v in enumerate(self._values):
            frac = max(0.0, min(1.0, v / 100.0))
            h = rect.height() * frac
            x = i * (bar_w + gap)
            painter.fillRect(QRectF(x, rect.height() - h, bar_w, h), tokens.threshold_color(frac))
        painter.end()


class _Backdrop(QWidget):
    """The background layer stack from ODIN-HUD.md §4: void fill, a radial
    vignette pulling the eye to the orb, a faint grid mesh, and scanlines.
    Previously only the flat void fill was implemented (a plain stylesheet
    background-color) — the missing three layers are most of what makes the
    reference imagery read as a dense instrument rather than flat black."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        rect = self.rect()

        painter.fillRect(rect, tokens.VOID)

        vignette = QRadialGradient(rect.width() * 0.5, rect.height() * 0.45, rect.width() * 0.62)
        vignette.setColorAt(0.0, QColor(10, 60, 90, 56))
        vignette.setColorAt(1.0, QColor(10, 60, 90, 0))
        painter.fillRect(rect, vignette)

        painter.setPen(QPen(QColor(11, 95, 135, 14), 1))
        for x in range(0, rect.width(), 40):
            painter.drawLine(x, 0, x, rect.height())
        for y in range(0, rect.height(), 40):
            painter.drawLine(0, y, rect.width(), y)

        painter.setPen(QPen(QColor(0, 0, 0, 56), 1))
        for y in range(0, rect.height(), 3):
            painter.drawLine(0, y, rect.width(), y)
        painter.end()


class _CircuitTraces(QWidget):
    """The background circuit-trace layer (§4 background stack, layer 5):
    ~12 hairline 45deg/90deg polylines, purely graphic. Positions are
    seeded-random rather than snapped to actual panel corners — the spec
    calls this "purely graphic" chrome, and it's the lowest-priority layer
    in the whole build order (§9 step 8)."""

    SEGMENT_COUNT = 12

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._segments: list[tuple[float, float, float, float]] = []

    def resizeEvent(self, event) -> None:
        rng = random.Random(11)
        w, h = self.width(), self.height()
        self._segments = []
        for _ in range(self.SEGMENT_COUNT):
            x0, y0 = rng.uniform(0.05, 0.95) * w, rng.uniform(0.08, 0.92) * h
            length = rng.uniform(40, 140)
            sign = rng.choice((-1, 1))
            if rng.random() < 0.5:
                x1, y1 = x0 + sign * length, y0
            else:
                x1, y1 = x0, y0 + sign * length
            self._segments.append((x0, y0, x1, y1))
        super().resizeEvent(event)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setPen(QPen(tokens.CY_600, 1))
        for x0, y0, x1, y1 in self._segments:
            painter.drawLine(int(x0), int(y0), int(x1), int(y1))
        painter.end()


def _relative_time(delta_seconds: float) -> str:
    """§6.9's `IN 12M` format. Negative deltas (overdue/fired) render as
    `3M AGO` instead."""
    overdue = delta_seconds < 0
    seconds = abs(delta_seconds)
    if seconds < 60:
        value, unit = int(seconds), "S"
    elif seconds < 3600:
        value, unit = int(seconds // 60), "M"
    elif seconds < 86400:
        value, unit = int(seconds // 3600), "H"
    else:
        value, unit = int(seconds // 86400), "D"
    return f"{value}{unit} AGO" if overdue else f"IN {value}{unit}"


class OdinHudWindow(QMainWindow):
    """The full-screen instrument HUD."""

    state_changed = pyqtSignal(str)  # mirrors the old JarvisMainWindow signal, for OrbWindow

    def __init__(self, brain, session, bridge: UiBridge, parent=None):
        super().__init__(parent)
        self.brain = brain
        self.session = session
        self.bridge = bridge

        self.current_worker: BrainWorker | None = None
        self._voice_setup_worker: VoiceSetupWorker | None = None
        self._voice_loop_worker: VoiceListenWorker | None = None
        self._skill_log: deque[SkillLogEntry] = deque(maxlen=6)
        self._confirm_banner: ConfirmationBannerWidget | None = None
        self._odin_reply_parts: list[str] = []
        self._mic_queue = None
        self._mic_timer: QTimer | None = None
        self._mic_smoothed = 0.0
        self._latest_frame: TelemetryFrame | None = None
        self._disk_bars: dict[str, BarMeter] = {}
        self._knowledge_rows: list[QWidget] = []
        self._notes_rows: list[QWidget] = []
        self._active_learning: tuple[str, str, float] | None = None
        self._boot_anims = None
        self._shown_once = False

        self.setWindowTitle(f"{config.ASSISTANT_NAME} HUD")
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setFixedSize(1920, 1080)
        # NoFocus is QWidget's default — without this, self.setFocus() in
        # show_and_activate() would silently do nothing and keyboard focus
        # would stay wherever Qt's initial auto-focus put it.
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self.telemetry = TelemetryWorker(self)
        self.telemetry.frame_ready.connect(self._on_frame)

        self.weather = WeatherWorker(self)
        self.weather.weather_ready.connect(self._on_weather)

        self._notes_timer = QTimer(self)
        self._notes_timer.timeout.connect(self._refresh_notes_panel)

        self._anim_timer = QTimer(self)
        self._anim_timer.timeout.connect(self._on_animation_tick)
        self._last_tick = time.monotonic()

        self._build_ui()
        self._init_tray()
        self._wire_bridge()
        self._refresh_knowledge_panel()
        self._refresh_notes_panel()

        learning_status.set_callback(self.bridge.report_learning_progress)

    # -- construction --------------------------------------------------

    def _build_ui(self) -> None:
        root = QWidget(self)
        self.setCentralWidget(root)

        self._backdrop = _Backdrop(root)
        self._backdrop.setGeometry(0, 0, self.width(), self.height())

        self._circuit_traces = _CircuitTraces(root)
        self._circuit_traces.setGeometry(0, 0, self.width(), self.height())
        # Stacking order, bottom to top: backdrop, then circuit traces, then
        # the grid of panels — lower() each in reverse so the last call
        # (backdrop) ends up at the very bottom.
        self._circuit_traces.lower()
        self._backdrop.lower()

        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._confirm_slot = QVBoxLayout()
        self._confirm_slot.setContentsMargins(*layout.GRID_MARGINS)
        outer.addLayout(self._confirm_slot)

        grid_host = QWidget(root)
        outer.addWidget(grid_host, 1)
        grid = QGridLayout(grid_host)
        grid.setContentsMargins(*layout.GRID_MARGINS)
        grid.setHorizontalSpacing(layout.GRID_GAP)
        grid.setVerticalSpacing(layout.GRID_GAP)
        for col in range(layout.GRID_COLUMNS):
            grid.setColumnStretch(col, 1)
        for row in range(layout.GRID_ROWS):
            grid.setRowStretch(row, 1)

        # Collected for the boot sequence's staggered per-panel reveal
        # (ui/hud/boot.py, ODIN-HUD.md §8) — every zone panel except D,
        # which is the orb column: the orb gets its own dedicated
        # scale-in + ignition animation, but the four gauges flanking it
        # join the normal panel stagger individually.
        self._boot_reveal_widgets: list[QWidget] = []
        for zone, builder in (
            ("A", self._build_zone_a),
            ("B", self._build_zone_b),
            ("C", self._build_zone_c),
            ("C2", self._build_zone_c2),
            ("E", self._build_zone_e),
            ("E2", self._build_zone_e2),
            ("F", self._build_zone_f),
            ("G", self._build_zone_g),
            ("H", self._build_zone_h),
            ("I", self._build_zone_i),
            ("J", self._build_zone_j),
            ("K", self._build_zone_k),
            ("L", self._build_zone_l),
            ("M", self._build_zone_m),
        ):
            widget = builder()
            self._boot_reveal_widgets.append(widget)
            layout.place(grid, widget, zone)

        layout.place(grid, self._build_zone_d(), "D")
        self._boot_reveal_widgets.extend(
            [self.gauge_cpu, self.gauge_ram, self.gauge_disk, self.gauge_gpu, self.orb_status_label]
        )

        self._root = root
        self.console = ConsoleOverlay(root)
        self.console.submitted.connect(self._launch_preset)
        self.console.slash_command.connect(self._handle_slash_command)
        self.console.move(
            (self.width() - self.console.width()) // 2,
            (self.height() - self.console.height()) // 2,
        )

    def _build_zone_a(self) -> QWidget:
        row = QWidget(self)
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(16)

        wordmark = QLabel(config.ASSISTANT_NAME.upper(), row)
        wordmark.setFont(tokens.font_display())
        wordmark.setStyleSheet(f"color: {tokens.CY_100.name()};")
        h.addWidget(wordmark, 0)

        version = QLabel("v2.0", row)
        version.setFont(tokens.font_label(tokens.T_MICRO))
        version.setStyleSheet(f"color: {tokens.CY_500.name()};")
        h.addWidget(version, 0)

        self.ruler = TickRuler(row)
        h.addWidget(self.ruler, 1)

        self.user_label = QLabel(f"USER: {getpass.getuser().upper()}", row)
        self.user_label.setFont(tokens.font_data(tokens.T_MICRO))
        self.user_label.setStyleSheet(f"color: {tokens.CY_500.name()};")
        h.addWidget(self.user_label, 0)

        self.uptime_label = QLabel("UP --", row)
        self.uptime_label.setFont(tokens.font_data(tokens.T_MICRO))
        self.uptime_label.setStyleSheet(f"color: {tokens.CY_500.name()};")
        h.addWidget(self.uptime_label, 0)

        self.link_pip = QLabel("●", row)
        self.link_pip.setStyleSheet(f"color: {tokens.CY_600.name()}; font-size: 10px;")
        h.addWidget(self.link_pip, 0)

        # A real, always-clickable way to hide the HUD. Esc does the same
        # thing, but a frameless always-on-top window can't be relied on to
        # always hold keyboard focus, so this must not be the only way out.
        close_btn = QPushButton("✕ HIDE", row)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setFont(tokens.font_label(tokens.T_MICRO))
        close_btn.setStyleSheet(
            f"QPushButton {{ background: rgba(53,200,245,20); border: 1px solid {tokens.CY_500.name()};"
            f" color: {tokens.CY_200.name()}; padding: 4px 12px; }}"
            f"QPushButton:hover {{ background: rgba(255,68,68,60); border: 1px solid {tokens.CRIT.name()};"
            f" color: {tokens.CY_100.name()}; }}"
        )
        close_btn.clicked.connect(self.dismiss)
        h.addWidget(close_btn, 0)
        return row

    def _build_zone_b(self) -> QWidget:
        panel = Panel("CPU", self)
        self.cpu_bar = BarMeter("CPU")
        panel.body_layout.addWidget(self.cpu_bar)
        self.cpu_freq = Readout("FREQ")
        panel.body_layout.addWidget(self.cpu_freq)
        self.core_strip = _CoreStrip(panel.body)
        panel.body_layout.addWidget(self.core_strip)
        self.cpu_processes = Readout("PROCESSES")
        panel.body_layout.addWidget(self.cpu_processes)
        self.cpu_top_rows = [Readout("--") for _ in range(1)]
        for row in self.cpu_top_rows:
            panel.body_layout.addWidget(row)
        panel.body_layout.addStretch(1)
        return panel

    def _build_zone_c(self) -> QWidget:
        panel = Panel("MEMORY", self)
        self.ram_bar = BarMeter("RAM")
        panel.body_layout.addWidget(self.ram_bar)
        self.ram_spark = Sparkline("%")
        panel.body_layout.addWidget(self.ram_spark, 1)
        self.swap_bar = BarMeter("SWAP")
        panel.body_layout.addWidget(self.swap_bar)
        return panel

    def _build_zone_c2(self) -> QWidget:
        panel = Panel("STORAGE", self)
        self._storage_panel = panel
        self.disk_io_read = Readout("READ")
        self.disk_io_write = Readout("WRITE")
        panel.body_layout.addWidget(self.disk_io_read)
        panel.body_layout.addWidget(self.disk_io_write)
        panel.body_layout.addStretch(1)
        return panel

    def _build_zone_d(self) -> QWidget:
        # The orb plus its four flanking corner gauges (§5.2's "four gauges
        # flanking the orb"), laid out as a 3-col grid: gauges in the outer
        # columns' top/bottom rows, the orb spanning the full height of the
        # center column.
        column = QWidget(self)
        grid = QGridLayout(column)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(10)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 3)
        grid.setColumnStretch(2, 1)
        for r in range(3):
            grid.setRowStretch(r, 1)

        self.gauge_cpu = RadialGauge("CPU")
        self.gauge_ram = RadialGauge("RAM")
        self.gauge_disk = RadialGauge("DISK")
        self.gauge_gpu = RadialGauge("GPU")
        grid.addWidget(self.gauge_cpu, 0, 0)
        grid.addWidget(self.gauge_disk, 0, 2)
        grid.addWidget(self.gauge_ram, 2, 0)
        grid.addWidget(self.gauge_gpu, 2, 2)

        self.orb = VoiceOrb(column)
        self.orb.status_changed.connect(self._on_orb_status)
        self.orb.launcher_clicked.connect(self._on_launcher_clicked)
        grid.addWidget(self.orb, 0, 1, 3, 1)

        self.orb_status_label = QLabel(STATUS_FOR_STATE["idle"], column)
        self.orb_status_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.orb_status_label.setFont(tokens.font_label(tokens.T_LABEL))
        self.orb_status_label.setStyleSheet(f"color: {tokens.CY_300.name()};")
        grid.addWidget(self.orb_status_label, 3, 0, 1, 3)
        return column

    def _build_zone_e(self) -> QWidget:
        panel = Panel("TRANSCRIPT", self)
        self.transcript_user = QLabel("", panel.body)
        self.transcript_user.setWordWrap(True)
        self.transcript_user.setFont(tokens.font_data(tokens.T_BODY))
        self.transcript_user.setStyleSheet(f"color: {tokens.CY_500.name()};")
        panel.body_layout.addWidget(self.transcript_user)

        self.transcript_odin = QLabel("", panel.body)
        self.transcript_odin.setWordWrap(True)
        self.transcript_odin.setFont(tokens.font_data(tokens.T_BODY))
        self.transcript_odin.setStyleSheet(f"color: {tokens.CY_200.name()};")
        self.transcript_odin.setProperty("aria-live", "polite")
        panel.body_layout.addWidget(self.transcript_odin)
        panel.body_layout.addStretch(1)
        return panel

    def _build_zone_e2(self) -> QWidget:
        panel = Panel("SKILL ACTIVITY", self)
        self._skill_log_panel = panel
        self._skill_log_rows: list[QLabel] = []
        return panel

    def _build_zone_f(self) -> QWidget:
        panel = Panel("CLOCK", self)
        self.clock_label = QLabel("--:--:--", panel.body)
        self.clock_label.setFont(tokens.font_data(tokens.T_XL))
        self.clock_label.setStyleSheet(f"color: {tokens.CY_100.name()};")
        panel.body_layout.addWidget(self.clock_label)
        self.date_label = QLabel("", panel.body)
        self.date_label.setFont(tokens.font_label(tokens.T_LABEL))
        self.date_label.setStyleSheet(f"color: {tokens.CY_500.name()};")
        panel.body_layout.addWidget(self.date_label)

        self._clock_timer = QTimer(self)
        self._clock_timer.timeout.connect(self._on_clock_tick)
        self._clock_timer.start(1000)
        self._on_clock_tick()
        return panel

    def _build_zone_g(self) -> QWidget:
        panel = Panel("WEATHER", self)
        self.weather_temp = QLabel("--°", panel.body)
        self.weather_temp.setFont(tokens.font_data(tokens.T_LG))
        self.weather_temp.setStyleSheet(f"color: {tokens.CY_100.name()};")
        panel.body_layout.addWidget(self.weather_temp)

        self.weather_condition = QLabel("--", panel.body)
        self.weather_condition.setFont(tokens.font_label(tokens.T_LABEL))
        self.weather_condition.setStyleSheet(f"color: {tokens.CY_500.name()};")
        panel.body_layout.addWidget(self.weather_condition)

        # Humidity/feels-like and wind/pressure are paired into one row each
        # — five separate Readouts didn't fit this panel's real budget
        # (§4's zone table gives it one row's worth of pixels; see
        # ui/hud/layout.py's rebalancing note).
        self.weather_humidity_feels = Readout("HUMID/FEELS")
        self.weather_wind_pressure = Readout("WIND/PRESS")
        self.weather_sun = Readout("SUN")
        for row in (self.weather_humidity_feels, self.weather_wind_pressure, self.weather_sun):
            panel.body_layout.addWidget(row)
        panel.body_layout.addStretch(1)
        return panel

    def _build_zone_h(self) -> QWidget:
        panel = Panel("THERMALS", self)
        self.temp_cpu = Readout("CPU TEMP")
        self.temp_gpu = Readout("GPU TEMP")
        self.temp_gpu_load = Readout("GPU LOAD")
        self.temp_vram = Readout("GPU VRAM")
        self.temp_fan = Readout("FAN")
        for row in (self.temp_cpu, self.temp_gpu, self.temp_gpu_load, self.temp_vram, self.temp_fan):
            panel.body_layout.addWidget(row)
        return panel

    def _build_zone_i(self) -> QWidget:
        panel = Panel("NETWORK", self)
        self.net_ip = Readout("IP")
        panel.body_layout.addWidget(self.net_ip)
        self.net_up_spark = Sparkline("KB/S")
        panel.body_layout.addWidget(self.net_up_spark, 1)
        self.net_down_spark = Sparkline("KB/S")
        panel.body_layout.addWidget(self.net_down_spark, 1)
        return panel

    def _build_zone_j(self) -> QWidget:
        panel = Panel("KNOWLEDGE BASE", self)
        self._knowledge_panel = panel
        return panel

    def _build_zone_k(self) -> QWidget:
        panel = Panel("AUDIO", self)
        self.spectrum = Spectrum(panel.body)
        panel.body_layout.addWidget(self.spectrum, 1)
        return panel

    def _build_zone_l(self) -> QWidget:
        panel = Panel("NOTES", self)
        self._notes_panel = panel
        return panel

    def _build_zone_m(self) -> QWidget:
        row = QWidget(self)
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(20)
        h.addStretch(1)
        for glyph, label, preset in DOCK_ITEMS:
            button = DockButton(glyph, label, row)
            button.clicked.connect(lambda checked=False, g=glyph, p=preset: self._on_dock_clicked(g, p))
            h.addWidget(button)
        h.addStretch(1)
        return row

    def _init_tray(self) -> None:
        self.tray_icon = QSystemTrayIcon(self)
        pixmap = QPixmap(32, 32)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(QPen(tokens.CY_300, 3))
        painter.drawEllipse(QRectF(4, 4, 24, 24))
        painter.setBrush(tokens.CY_100)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QRectF(12, 12, 8, 8))
        painter.end()
        self.tray_icon.setIcon(QIcon(pixmap))
        self.tray_icon.setToolTip(f"{config.ASSISTANT_NAME} — AI desktop assistant")

        menu = QMenu(self)
        for text, slot in (
            (f"Show {config.ASSISTANT_NAME}", self.show_and_activate),
            ("Toggle voice / text mode", self._toggle_mode),
            ("Clear conversation", self.trigger_reset),
        ):
            action = QAction(text, self)
            action.triggered.connect(slot)
            menu.addAction(action)
        menu.addSeparator()
        quit_action = QAction("Quit", self)
        quit_action.triggered.connect(QApplication.instance().quit)
        menu.addAction(quit_action)

        self.tray_icon.setContextMenu(menu)
        self.tray_icon.activated.connect(self._on_tray_activated)
        self.tray_icon.show()

    def _wire_bridge(self) -> None:
        self.bridge.text_chunk.connect(self._on_text_chunk)
        self.bridge.action_reported.connect(self._on_action_reported)
        self.bridge.confirm_requested.connect(self._on_confirm_requested)
        self.bridge.tool_started.connect(self._on_tool_started)
        self.bridge.tool_finished.connect(self._on_tool_finished)
        self.bridge.skill_logged.connect(self._on_skill_logged)
        self.bridge.mic_rms.connect(self.orb.set_mic_level)
        self.bridge.learning_progress.connect(self._on_learning_progress)
        self.bridge.kb_changed.connect(self._on_kb_changed)

    # -- window lifecycle --------------------------------------------------

    def _size_to_screen(self) -> None:
        """Match the real primary screen instead of the 1920x1080 fallback
        set at construction. setFixedSize() and showFullScreen() otherwise
        fight each other on any monitor that isn't exactly 1920x1080 — Qt
        can't actually resize a fixed-size window to fill the real screen,
        which left background layers (sized from the stale 1920x1080) out
        of sync with the window's real geometry. Safe to call repeatedly;
        it's a no-op once the size already matches."""
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        size = screen.geometry().size()
        if size == self.size():
            return
        self.setFixedSize(size)
        self._backdrop.setGeometry(0, 0, size.width(), size.height())
        self._circuit_traces.setGeometry(0, 0, size.width(), size.height())
        self.console.move((size.width() - self.console.width()) // 2, (size.height() - self.console.height()) // 2)

    def show_and_activate(self) -> None:
        self._size_to_screen()
        self.showFullScreen()
        self.raise_()
        self.activateWindow()
        # Qt hands keyboard focus to the first tab-focusable child (the
        # EXP dock button) the moment the window is shown, unless something
        # else claims it explicitly. Left that way, every keystroke —
        # including Esc — goes to that button instead of the window, which
        # is why Esc silently did nothing. Claiming focus on the window
        # itself here (not a specific child) is what makes Esc reach
        # keyPressEvent below.
        self.setFocus(Qt.FocusReason.ActiveWindowFocusReason)
        # Belt-and-suspenders: on Windows, activateWindow() on a frameless
        # always-on-top window launched from a terminal can lose a race with
        # the OS's foreground-lock — the window paints but doesn't actually
        # take OS-level keyboard focus until a beat later. A second attempt
        # after the event loop has spun costs nothing and closes that gap.
        QTimer.singleShot(150, lambda: (self.activateWindow(), self.setFocus(Qt.FocusReason.ActiveWindowFocusReason)))
        if not self.telemetry.isRunning():
            self.telemetry.start()
        if not self._anim_timer.isActive():
            self._last_tick = time.monotonic()
            self._anim_timer.start(int(1000 / ANIMATION_FPS))
        if not self.weather.isRunning():
            self.weather.start()
        if not self._notes_timer.isActive():
            self._notes_timer.start(20_000)
        self.spectrum.start_capture()
        if not self._shown_once:
            self._shown_once = True
            self.transcript_odin.setText(f"{config.ASSISTANT_NAME} online. Say the word, or open the console.")
            run_boot_sequence(self)

    def dismiss(self) -> None:
        self.telemetry.stop()
        self.telemetry.wait(2000)
        self.weather.stop()
        self.weather.wait(2000)
        self._notes_timer.stop()
        self._anim_timer.stop()
        self.spectrum.stop_capture()
        self._stop_mic_meter()
        self.hide()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            if self.console.isVisible():
                self.console.hide_console()
            else:
                self.dismiss()
            return
        super().keyPressEvent(event)

    def _on_tray_activated(self, reason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.dismiss() if self.isVisible() else self.show_and_activate()

    def closeEvent(self, event) -> None:
        if self.tray_icon is not None and self.tray_icon.isVisible():
            self.dismiss()
            event.ignore()
        else:
            event.accept()

    # -- shared ~30fps animation loop (ODIN-HUD.md §10) ---------------------

    def _on_animation_tick(self) -> None:
        now = time.monotonic()
        dt = now - self._last_tick
        self._last_tick = now
        self.orb.advance(dt)
        self.spectrum.advance(dt)

    # -- telemetry -----------------------------------------------------

    def _on_frame(self, frame: TelemetryFrame) -> None:
        self._latest_frame = frame

        self.cpu_bar.set_value(frame.cpu.percent / 100, f"{frame.cpu.percent:.0f}%")
        self.cpu_freq.set_value("--" if frame.cpu.freq_mhz is None else f"{frame.cpu.freq_mhz} MHZ")
        self.core_strip.set_values(frame.cpu.per_core)
        self.cpu_processes.set_value(str(frame.cpu.processes))
        for i, row in enumerate(self.cpu_top_rows):
            if i < len(frame.cpu.top):
                name, cpu = frame.cpu.top[i]
                row.set_label(name[:18])
                row.set_value(f"{cpu:.0f}%")
            else:
                row.set_label("--")
                row.set_value("--")

        self.ram_bar.set_value(frame.mem.percent / 100, f"{frame.mem.percent:.0f}%")
        self.ram_spark.push(frame.mem.percent)
        self.swap_bar.set_value(frame.mem.swap_percent / 100, f"{frame.mem.swap_percent:.0f}%")

        self.gauge_cpu.set_percent(frame.cpu.percent)
        self.gauge_ram.set_percent(frame.mem.percent)
        self.gauge_disk.set_percent(frame.disks[0].percent if frame.disks else None)
        self.gauge_gpu.set_percent(frame.thermals.gpu_load)

        self._update_disks(frame.disks)
        self.disk_io_read.set_value(f"{frame.disk_io.read_mbs:.1f} MB/S")
        self.disk_io_write.set_value(f"{frame.disk_io.write_mbs:.1f} MB/S")

        self.net_ip.set_value(frame.net.ip or "--")
        self.net_up_spark.push(frame.net.up_kbs)
        self.net_down_spark.push(frame.net.down_kbs)

        self.temp_cpu.set_value("--" if frame.thermals.cpu_c is None else f"{frame.thermals.cpu_c:.0f}°C")
        self.temp_gpu.set_value("--" if frame.thermals.gpu_c is None else f"{frame.thermals.gpu_c:.0f}°C")
        self.temp_gpu_load.set_value(
            "--" if frame.thermals.gpu_load is None else f"{frame.thermals.gpu_load:.0f}%"
        )
        self.temp_vram.set_value(
            "--" if frame.thermals.gpu_vram_percent is None else f"{frame.thermals.gpu_vram_percent:.0f}%"
        )
        self.temp_fan.set_value("--" if frame.thermals.fan_rpm is None else f"{frame.thermals.fan_rpm:.0f} RPM")

        d = int(frame.uptime_sec // 86400)
        h = int((frame.uptime_sec % 86400) // 3600)
        m = int((frame.uptime_sec % 3600) // 60)
        self.uptime_label.setText(f"UP {d}D {h}H {m}M")
        self.link_pip.setStyleSheet(f"color: {tokens.OK.name()}; font-size: 10px;")

        if self.orb.state not in ("thinking", "learning"):
            load = 0.5 * frame.cpu.percent / 100 + 0.3 * frame.mem.percent / 100 + 0.2 * min(
                (frame.disk_io.read_mbs + frame.disk_io.write_mbs) / 50, 1.0
            )
            self.orb.set_system_load(load)

    def _update_disks(self, disks) -> None:
        seen = set()
        for disk in disks:
            seen.add(disk.mount)
            bar = self._disk_bars.get(disk.mount)
            if bar is None:
                bar = BarMeter(disk.mount)
                self._disk_bars[disk.mount] = bar
                self._storage_panel.body_layout.insertWidget(len(self._disk_bars) - 1, bar)
            bar.set_value(disk.percent / 100, f"{disk.used_gb:.0f}/{disk.total_gb:.0f} GB")
        for mount in list(self._disk_bars):
            if mount not in seen:
                widget = self._disk_bars.pop(mount)
                widget.setParent(None)
                widget.deleteLater()

    def _on_clock_tick(self) -> None:
        now = datetime.now()
        self.clock_label.setText(now.strftime("%H:%M:%S"))
        self.date_label.setText(now.strftime("%A · %d %b %Y").upper())

    # -- weather ---------------------------------------------------------

    def _on_weather(self, sample: WeatherSample | None) -> None:
        if sample is None:
            self.weather_temp.setText("--°")
            self.weather_condition.setText("NO SIGNAL")
            self.weather_humidity_feels.set_value("--")
            self.weather_wind_pressure.set_value("--")
            self.weather_sun.set_value("--")
            return

        self.weather_temp.setText("--°" if sample.temp_c is None else f"{sample.temp_c:.0f}°C")
        self.weather_condition.setText((sample.condition or "--").strip().upper())

        humidity = "--" if sample.humidity is None else f"{sample.humidity:.0f}%"
        feels = "--" if sample.feels_like_c is None else f"{sample.feels_like_c:.0f}°C"
        self.weather_humidity_feels.set_value(f"{humidity} / {feels}")

        wind = "--" if sample.wind_kph is None else f"{sample.wind_kph:.0f}KM/H"
        pressure = "--" if sample.pressure_mb is None else f"{sample.pressure_mb:.0f}MB"
        self.weather_wind_pressure.set_value(f"{wind} / {pressure}")

        sun = f"{sample.sunrise} / {sample.sunset}" if sample.sunrise and sample.sunset else "--"
        self.weather_sun.set_value(sun)

    # -- notes / reminders -------------------------------------------------

    def _refresh_notes_panel(self) -> None:
        panel = self._notes_panel
        for row in self._notes_rows:
            row.setParent(None)
            row.deleteLater()
        self._notes_rows = []

        store = get_store()
        notes = store.list_notes()
        reminders = store.pending_reminders()
        now = time.time()

        if not notes and not reminders:
            empty = QLabel("NOTHING SAVED.", panel.body)
            empty.setFont(tokens.font_label(tokens.T_MICRO))
            empty.setStyleSheet(f"color: {tokens.CY_600.name()};")
            panel.body_layout.addWidget(empty)
            self._notes_rows.append(empty)
            return

        # This panel gets one grid row's worth of pixels (§4's spec table
        # gave it two; see ui/hud/layout.py's rebalancing note) — reminders
        # win the limited space since they're time-sensitive, notes fill
        # whatever's left.
        max_items = 2
        shown_reminders = reminders[:max_items]
        remaining_slots = max_items - len(shown_reminders)
        shown_notes = notes[-remaining_slots:] if remaining_slots > 0 else []

        for reminder in shown_reminders:
            remaining = reminder["fire_at"] - now
            overdue = remaining < 0
            text = f"{reminder['message']} — {_relative_time(remaining)}"
            label = QLabel(text, panel.body)
            label.setWordWrap(True)
            label.setFont(tokens.font_data(tokens.T_MICRO))
            label.setStyleSheet(f"color: {tokens.WARN.name() if overdue else tokens.CY_200.name()};")
            panel.body_layout.addWidget(label)
            self._notes_rows.append(label)

        for note in shown_notes:
            label = QLabel(note["text"], panel.body)
            label.setWordWrap(True)
            label.setFont(tokens.font_data(tokens.T_MICRO))
            label.setStyleSheet(f"color: {tokens.CY_500.name()};")
            panel.body_layout.addWidget(label)
            self._notes_rows.append(label)

    # -- orb / bridge event handling ----------------------------------------

    def _on_orb_status(self, text: str) -> None:
        self.orb_status_label.setText(text)
        color = tokens.CRIT if self.orb.state == "error" else tokens.CY_300
        self.orb_status_label.setStyleSheet(f"color: {color.name()};")

    def set_status(self, state: str) -> None:
        self.orb.state = "thinking" if state == "confirm" else state
        self.state_changed.emit(self.orb.state)

    def _on_text_chunk(self, sentence: str) -> None:
        self.set_status("speaking")
        self._odin_reply_parts.append(sentence)
        self.transcript_odin.setText(" ".join(self._odin_reply_parts))

    def _on_action_reported(self, skill_name: str, token: str, description: str) -> None:  # noqa: ARG002
        pass  # superseded by the skill-log panel; undo is still reachable via /undo

    def _on_tool_started(self, skill_name: str, _args_brief: str) -> None:  # noqa: ARG002
        if skill_name == "deep_learn":
            self.orb.state = "learning"

    def _on_tool_finished(self, skill_name: str, is_error: bool, _result_brief: str) -> None:  # noqa: ARG002
        if is_error:
            self.orb.flash_error()
        if skill_name == "deep_learn":
            self._active_learning = None
            self.orb.state = "thinking"

    def _on_skill_logged(self, entry: SkillLogEntry) -> None:
        self._skill_log.appendleft(entry)
        self._rebuild_skill_log_panel()

    def _rebuild_skill_log_panel(self) -> None:
        for row in self._skill_log_rows:
            row.setParent(None)
            row.deleteLater()
        self._skill_log_rows = []
        for entry in self._skill_log:
            ts = datetime.fromtimestamp(entry.ts).strftime("%H:%M:%S")
            status = "OK" if entry.ok else "FAIL"
            text = f"{ts} · {entry.skill.upper()} · {status} · {entry.ms:.0f}MS"
            label = QLabel(text, self._skill_log_panel.body)
            label.setFont(tokens.font_data(tokens.T_MICRO))
            label.setStyleSheet(f"color: {tokens.OK.name() if entry.ok else tokens.CRIT.name()};")
            self._skill_log_panel.body_layout.addWidget(label)
            self._skill_log_rows.append(label)

    def _on_learning_progress(self, topic: str, subtopic: str, progress: float) -> None:
        self.orb.set_learning_progress(subtopic, progress)
        self._active_learning = (topic, subtopic, progress)
        self._refresh_knowledge_panel()

    def _on_kb_changed(self) -> None:
        self._refresh_knowledge_panel()

    def _refresh_knowledge_panel(self) -> None:
        """§6.8: one row per learned topic, a thin bar for relative size,
        last-updated date; the active topic's row shows live progress
        instead while deep_learn is running. Empty state is an explicit
        instruction to act, not an apology (§6.8)."""
        panel = self._knowledge_panel
        for row in self._knowledge_rows:
            row.setParent(None)
            row.deleteLater()
        self._knowledge_rows = []

        rows = get_store().list_knowledge_topics()
        if not rows:
            empty = QLabel('NO TOPICS LEARNED — SAY "DEEP SEARCH ABOUT …" TO BEGIN.', panel.body)
            empty.setWordWrap(True)
            empty.setFont(tokens.font_label(tokens.T_MICRO))
            empty.setStyleSheet(f"color: {tokens.CY_600.name()};")
            panel.body_layout.addWidget(empty)
            self._knowledge_rows.append(empty)
            return

        total_chunks = sum(r["chunk_count"] for r in rows)
        header = QLabel(f"{len(rows)} TOPICS · {total_chunks} CHUNKS", panel.body)
        header.setFont(tokens.font_label(tokens.T_MICRO))
        header.setStyleSheet(f"color: {tokens.CY_500.name()};")
        panel.body_layout.addWidget(header)
        self._knowledge_rows.append(header)

        max_chunks = max((r["chunk_count"] for r in rows), default=1) or 1
        for row in rows[:3]:  # this panel's real budget fits ~3 bars; see layout.py's note
            topic = row["topic"]
            if self._active_learning and self._active_learning[0] == topic:
                _, subtopic, fraction = self._active_learning
                bar = BarMeter(f"{topic} · {subtopic}"[:28])
                bar.set_value(fraction, f"{fraction * 100:.0f}%")
            else:
                when = datetime.fromtimestamp(row["updated_at"]).strftime("%Y-%m-%d")
                bar = BarMeter(f"{topic} — {when}"[:28])
                bar.set_value(row["chunk_count"] / max_chunks, str(row["chunk_count"]))
            panel.body_layout.addWidget(bar)
            self._knowledge_rows.append(bar)

    def _on_confirm_requested(self, question: str) -> None:
        self.set_status("confirm")
        self.show_and_activate()
        banner = ConfirmationBannerWidget(question, self)
        self._confirm_banner = banner
        self._confirm_slot.addWidget(banner)

        def answered(approved: bool) -> None:
            self._confirm_slot.removeWidget(banner)
            banner.setParent(None)
            banner.deleteLater()
            self._confirm_banner = None
            self.bridge.answer(approved)
            self.set_status("thinking")

        banner.answered.connect(answered)

    # -- commands ------------------------------------------------------

    def _on_dock_clicked(self, glyph: str, preset: str | None) -> None:
        if glyph == "SET":
            from ui.panels import SettingsDialog

            SettingsDialog(self.brain, self).exec()
            return
        if glyph == "CON":
            self.console.toggle()
            return
        self._launch_preset(preset)

    def _on_launcher_clicked(self, label: str) -> None:
        preset = LAUNCHER_PRESETS.get(label)
        if label == "LEARN":
            from ui.panels import KnowledgeDialog

            KnowledgeDialog(self).exec()
            return
        if preset:
            self._launch_preset(preset)

    def _handle_slash_command(self, cmd: str) -> None:
        command = cmd.strip().lower()
        if command == "/undo":
            self.trigger_undo()
        elif command in ("/reset", "/forget"):
            self.trigger_reset()
        elif command == "/mode voice":
            self._switch_to_voice()
        elif command == "/mode text":
            self._switch_to_text()
        elif command in ("/quit", "/exit"):
            QApplication.instance().quit()
        else:
            self.console.echo(f"Unknown command '{cmd}'.")

    def trigger_undo(self) -> None:
        entry = get_journal().latest()
        if entry is None:
            self.console.echo("There's nothing to undo.")
            return
        try:
            self.console.echo(get_journal().undo(entry.token))
        except Exception as e:  # noqa: BLE001 - a failed undo is a message, not a crash
            self.console.echo(f"I couldn't undo that: {e}")

    def trigger_reset(self) -> None:
        if self.current_worker is not None:
            # Brain.ask() reads self.history into a local snapshot at the
            # start of a turn and only writes it back at the end — a reset
            # while a turn is still in flight would appear to work, then be
            # silently overwritten the moment that turn's worker thread
            # finishes and persists its own (pre-reset) snapshot back.
            self.console.echo("Wait for the current action to finish before resetting.")
            return
        self.brain.reset()
        self.console.echo("Conversation memory cleared. Notes and reminders kept.")

    def announce(self, text: str) -> None:
        """Boot-time informational messages (restored history, missed
        reminders, hotkey hint) — echoed into the console's scrollback
        rather than the transcript ticker, since none of them are
        something ODIN is currently saying."""
        self.console.echo(text)

    # -- turns -----------------------------------------------------------

    def _launch_preset(self, text: str) -> None:
        """Every dock button, orb launcher, and console submission funnels
        through here — identical to Brain.ask() being sent a typed message,
        per ODIN-HUD.md §7.3. Never call a skill directly: that would bypass
        the DANGEROUS-tier confirmation gate a typed equivalent still gets."""
        if not text or self.current_worker is not None:
            return
        self.transcript_user.setText(text)
        self._process_user_turn(text)

    def _process_user_turn(self, text: str) -> None:
        self.set_status("thinking")
        self._odin_reply_parts = []
        self.transcript_odin.setText("…")

        worker = BrainWorker(self.brain, text, parent=self)
        self.current_worker = worker
        worker.turn_finished.connect(self._on_turn_finished)
        worker.error_occurred.connect(self._on_turn_error)
        worker.start()

    def _on_turn_finished(self, reply: str) -> None:
        if reply and not self._odin_reply_parts:
            self.transcript_odin.setText(reply)
        # The transcript ticker (zone E) is ODIN's primary output, but the
        # console is a self-contained typed-input surface (console.py) —
        # without this, a reply only ever appeared elsewhere on the HUD,
        # so anyone watching just the console saw their message echoed
        # and then nothing.
        if self.transcript_odin.text():
            self.console.echo(self.transcript_odin.text())
        self._finish_turn()

    def _on_turn_error(self, message: str) -> None:
        self.transcript_odin.setText(message)
        self.console.echo(message)
        self._finish_turn()

    def _finish_turn(self) -> None:
        self.current_worker = None
        self.set_status("idle")
        if self._voice_loop_worker is not None:
            threading.Thread(target=self._resume_voice_after_speech, daemon=True).start()

    def _resume_voice_after_speech(self) -> None:
        self.session.speaker.wait(timeout=60)
        if self._voice_loop_worker is not None:
            self._voice_loop_worker.resume()

    # -- voice mode ------------------------------------------------------

    def _toggle_mode(self) -> None:
        if self.session.mode == "voice":
            self._switch_to_text()
        else:
            self._switch_to_voice()

    def _switch_to_text(self) -> None:
        self._stop_voice_loop()
        self.console.echo(self.session.set_mode("text"))

    def _switch_to_voice(self) -> None:
        if self._voice_setup_worker is not None or self._voice_loop_worker is not None:
            # Reachable via /mode voice typed twice, not just the dock
            # toggle (which already checks session.mode) — without this, a
            # second call here would overwrite _voice_loop_worker with a
            # new VoiceListenWorker while the first is still running,
            # leaking its thread and duplicating every transcription.
            return
        self.console.echo("Starting microphone and loading speech models…")
        worker = VoiceSetupWorker(self.session, self)
        self._voice_setup_worker = worker
        worker.finished_ok.connect(self._on_voice_ready)
        worker.failed.connect(self._on_voice_setup_failed)
        worker.start()

    def _on_voice_ready(self, message: str) -> None:
        self._voice_setup_worker = None
        self.console.echo(message)
        self._start_voice_loop()
        self._start_mic_meter()

    def _on_voice_setup_failed(self, message: str) -> None:
        self._voice_setup_worker = None
        self.console.echo(message)

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
        self._stop_mic_meter()

    def _on_voice_state(self, state: str) -> None:
        if self.session.mode == "voice" and self.current_worker is None:
            self.set_status(state)
            if state == "listening":
                self._start_mic_meter()
            else:
                self._stop_mic_meter()

    def _on_voice_heard(self, text: str) -> None:
        if self.session.mode != "voice" or self.current_worker is not None:
            # Mirrors _launch_preset's guard — without it, a voice-heard
            # turn and a dock/console-triggered turn arriving close together
            # could both start a BrainWorker, racing on self.brain's shared
            # history and the UiBridge's single confirm() Event.
            return
        self._stop_mic_meter()
        self.transcript_user.setText(text)
        self._process_user_turn(text)

    # -- mic amplitude, ~20Hz while listening (§5.3) -------------------------

    def _start_mic_meter(self) -> None:
        if self._mic_timer is not None or self.session.mic is None:
            return
        try:
            import numpy  # noqa: F401 - only imported to confirm it's actually available
        except ImportError:
            return
        self._mic_queue = self.session.mic.subscribe()
        self._mic_smoothed = 0.0
        self._mic_timer = QTimer(self)
        self._mic_timer.timeout.connect(self._on_mic_tick)
        self._mic_timer.start(50)

    def _stop_mic_meter(self) -> None:
        if self._mic_timer is not None:
            self._mic_timer.stop()
            self._mic_timer = None
        if self._mic_queue is not None and self.session.mic is not None:
            self.session.mic.unsubscribe(self._mic_queue)
        self._mic_queue = None

    def _on_mic_tick(self) -> None:
        if self._mic_queue is None:
            return
        import numpy as np

        from core.audio import rms

        level = None
        while True:
            try:
                block = self._mic_queue.get_nowait()
            except queue.Empty:
                break
            level = rms(block, np)
        if level is not None:
            self._mic_smoothed += (level - self._mic_smoothed) * 0.5  # ~60ms smoothing at 20Hz
            self.bridge.mic_rms.emit(min(1.0, self._mic_smoothed * 4))
