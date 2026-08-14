"""Zone builders for OdinHudWindow — one `_build_zone_x` per ODIN-HUD.md §4
grid zone, plus `_build_ui`, moved verbatim out of ui/hud/window.py so that
god-class isn't the only place this ~330 lines of pure widget construction
can live. Every method still assigns onto `self.xxx` exactly as before —
OdinHudWindow mixes this in (`class OdinHudWindow(ZoneBuilderMixin,
QMainWindow)`), so every other method's `self.transcript_odin`,
`self.temp_gpu`, etc. keep resolving normally.
"""
from __future__ import annotations

import getpass
import random

from PyQt6.QtCore import QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QPainter, QPen, QRadialGradient
from PyQt6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

import config
from . import layout, tokens
from .console import ConsoleOverlay
from .radial_gauge import RadialGauge
from .sparkline import Sparkline
from .spectrum import Spectrum
from .voice_orb import VoiceOrb
from .widgets import BarMeter, DockButton, Panel, Readout, TickRuler

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

STATUS_FOR_STATE = {
    "idle": "STANDING BY",
    "listening": "LISTENING",
    "thinking": "PROCESSING",
    "speaking": "SPEAKING",
    "learning": "LEARNING",
    "error": "ERROR",
    "confirm": "AWAITING AUTHORISATION",
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


class ZoneBuilderMixin:
    """Mixed into OdinHudWindow — see module docstring."""

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
