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

import math

from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QPainter, QPen, QPixmap, QRadialGradient
from PyQt6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

import config
from . import layout, tokens
from .console import ConsoleOverlay
from .instruments import (
    BatteryMeter,
    ForecastStrip,
    HeroValue,
    MetricGraph,
    MiniArc,
    ProcessRows,
)
from .radial_gauge import RadialGauge
from .spectrum import Spectrum
from .voice_orb import VoiceOrb
from .widgets import BarMeter, Dock, DockButton, Panel, Readout, TickRuler

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

if config.ENABLE_GESTURE_CONTROL:
    # Handled locally (glyph == "HAND" in _on_dock_clicked), same as SET/CON
    # above — an instant toggle, no LLM confirmation, mirroring the tray
    # menu's "Toggle hand control" entry. Only shown when the feature is on.
    DOCK_ITEMS.append(("HAND", "Hand Control", None))

GRAPH_MAX_H = 58   # a history trace's ceiling; slack past it goes to the panel

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


class _CachedLayer(QWidget):
    """A background layer whose content depends only on its size.

    Painted once into a pixmap and blitted thereafter. Every widget on the
    HUD is translucent, so any one of them repainting dirties the background
    beneath it — which meant these layers were being redrawn twice a frame.
    The backdrop alone measured ~72ms per paint at 1920x1080 (a full-screen
    radial gradient plus ~360 scanline drawLine calls), which was the real
    cost behind the HUD feeling heavy.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._cache: QPixmap | None = None

    def cached_layers(self) -> QPixmap | None:
        return self._cache

    def _render_layers(self, painter: QPainter, width: int, height: int) -> None:
        raise NotImplementedError

    def resizeEvent(self, event) -> None:
        self._cache = None
        self._glow_cache = getattr(self, "_glow_cache", None) and None
        super().resizeEvent(event)

    def paintEvent(self, _event) -> None:
        width, height = self.width(), self.height()
        if width <= 0 or height <= 0:
            return
        if self._cache is None or self._cache.size() != self.size():
            cache = QPixmap(self.size())
            cache.fill(Qt.GlobalColor.transparent)
            builder = QPainter(cache)
            self._render_layers(builder, width, height)
            builder.end()
            self._cache = cache
        painter = QPainter(self)
        painter.drawPixmap(0, 0, self._cache)
        painter.end()


class _Backdrop(_CachedLayer):
    """The background: a black hexagon field.

    Rendered once into a pixmap and blitted. It is deliberately inert — it
    encodes nothing and nothing on it moves, so it stays out of the animation
    loop entirely. Anything that repaints here drags every translucent panel
    above it into the same frame, which is what made the previous backdrop
    cost ~72ms a paint.
    """

    HEX_R = 46.0   # circumradius; flat-top orientation

    @classmethod
    def _hex_points(cls, cx: float, cy: float) -> list[tuple[float, float]]:
        return [
            (cx + cls.HEX_R * math.cos(math.radians(60 * i)),
             cy + cls.HEX_R * math.sin(math.radians(60 * i)))
            for i in range(6)
        ]

    def _centres(self, width: int, height: int):
        step_x = self.HEX_R * 1.5
        step_y = self.HEX_R * math.sqrt(3)
        col = 0
        x = -self.HEX_R
        while x < width + self.HEX_R:
            offset = (step_y / 2) if col % 2 else 0.0
            y = -self.HEX_R + offset
            while y < height + self.HEX_R:
                yield x, y
                y += step_y
            x += step_x
            col += 1

    def _render_layers(self, painter: QPainter, width: int, height: int) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.fillRect(QRectF(0, 0, width, height), tokens.VOID)

        rng = random.Random(7)
        painter.setPen(QPen(tokens.HEX_EDGE, 1.2))
        for cx, cy in self._centres(width, height):
            shade = rng.uniform(0.55, 1.6)
            fill = QColor(
                min(255, int(tokens.HEX_FILL.red() * shade)),
                min(255, int(tokens.HEX_FILL.green() * shade)),
                min(255, int(tokens.HEX_FILL.blue() * shade)),
            )
            painter.setBrush(fill)
            painter.drawPolygon(*[QPointF(x, y) for x, y in self._hex_points(cx, cy)])


class _CircuitTraces(_CachedLayer):
    """The background circuit-trace layer (§4 background stack, layer 5):
    ~12 hairline 45deg/90deg polylines, purely graphic. Positions are
    seeded-random rather than snapped to actual panel corners — the spec
    calls this "purely graphic" chrome, and it's the lowest-priority layer
    in the whole build order (§9 step 8)."""

    SEGMENT_COUNT = 12

    def _render_layers(self, painter: QPainter, width: int, height: int) -> None:
        rng = random.Random(11)
        painter.setPen(QPen(tokens.CY_600, 1))
        for _ in range(self.SEGMENT_COUNT):
            x0, y0 = rng.uniform(0.05, 0.95) * width, rng.uniform(0.08, 0.92) * height
            length = rng.uniform(40, 140)
            sign = rng.choice((-1, 1))
            if rng.random() < 0.5:
                x1, y1 = x0 + sign * length, y0
            else:
                x1, y1 = x0, y0 + sign * length
            painter.drawLine(int(x0), int(y0), int(x1), int(y1))


class ZoneBuilderMixin:
    """Mixed into OdinHudWindow — see module docstring."""

    def _register(self, *instruments):
        """Collect everything the shared loop has to advance (§10). Panels
        register their own instruments as they build them, so adding a widget
        can't silently leave it frozen — there's one list, not a hand-written
        call per widget in the presenter."""
        self._instruments.extend(instruments)
        return instruments[0] if len(instruments) == 1 else instruments

    @staticmethod
    def _strip(parent, *widgets) -> QWidget:
        """One horizontal line of readouts, evenly shared — panels are short
        on vertical space, and three facts on one line beats three lines."""
        row = QWidget(parent)
        box = QHBoxLayout(row)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(10)
        for widget in widgets:
            box.addWidget(widget, 1)
        return row

    def _build_ui(self) -> None:
        self._instruments: list[QWidget] = []
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
        self.cpu_hero = self._register(HeroValue("LOAD", "%"))
        panel.body_layout.addWidget(self.cpu_hero)

        self.cpu_graph = self._register(MetricGraph(capacity=60, maximum=100.0))
        self.cpu_graph.setMaximumHeight(GRAPH_MAX_H)
        panel.body_layout.addWidget(self.cpu_graph, 1)

        # One row, not three: the question a glance asks is "what's eating it",
        # and the answer is the top entry. The per-core strip and the clock
        # speed went with it — neither changed a decision anyone was making.
        self.cpu_procs = self._register(ProcessRows(count=1, unit="%", decimals=1))
        panel.body_layout.addWidget(self.cpu_procs)
        panel.body_layout.addStretch(1)
        return panel

    def _build_zone_c(self) -> QWidget:
        panel = Panel("MEMORY", self)
        self.ram_hero = self._register(HeroValue("USED", "%"))
        panel.body_layout.addWidget(self.ram_hero)

        self.ram_graph = self._register(MetricGraph(capacity=60, maximum=100.0))
        self.ram_graph.setMaximumHeight(GRAPH_MAX_H)
        panel.body_layout.addWidget(self.ram_graph, 1)
        panel.body_layout.addStretch(1)
        return panel

    def _build_zone_c2(self) -> QWidget:
        panel = Panel("STORAGE", self)
        self._storage_panel = panel  # update_disks() inserts one bar per mount here
        # Read and write on one line, and no trace: throughput is something you
        # check, not something you watch.
        self.disk_io = Readout("I/O")
        panel.body_layout.addWidget(self.disk_io)
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

        panel.body_layout.addStretch(1)

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
        self.weather_forecast = ForecastStrip(panel.body)
        self.weather_forecast.setFixedHeight(58)
        panel.body_layout.addWidget(self.weather_forecast)

        panel.body_layout.addStretch(1)
        return panel

    def _build_zone_h(self) -> QWidget:
        panel = Panel("THERMALS · POWER", self)
        # Two arcs, not three: GPU load is already one of the four radial
        # gauges flanking the orb, and VRAM had a readout of its own there too.
        self.temp_arc_cpu = self._register(MiniArc("CPU", "°", minimum=20.0, maximum=100.0))
        self.temp_arc_gpu = self._register(MiniArc("GPU", "°", minimum=20.0, maximum=100.0))
        arcs = self._strip(panel.body, self.temp_arc_cpu, self.temp_arc_gpu)
        arcs.setFixedHeight(78)
        panel.body_layout.addWidget(arcs)

        self.battery = self._register(BatteryMeter(panel.body))
        panel.body_layout.addWidget(self.battery)
        panel.body_layout.addStretch(1)
        return panel

    def _build_zone_i(self) -> QWidget:
        panel = Panel("NETWORK", self)
        self.net_ip = Readout("IP")
        panel.body_layout.addWidget(self.net_ip)

        self.net_hero_down = self._register(HeroValue("DOWN", "KB/S", maximum=None))
        panel.body_layout.addWidget(self.net_hero_down)

        self.net_graph_down = self._register(MetricGraph(capacity=60))
        self.net_graph_down.setMaximumHeight(GRAPH_MAX_H)
        panel.body_layout.addWidget(self.net_graph_down, 1)
        panel.body_layout.addStretch(1)
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
        # A Dock rather than a QHBoxLayout of buttons: the magnifier sizes
        # each button from the cursor's distance, so the row has to be laid
        # out by hand (see ui/hud/widgets.py's Dock).
        self.dock = Dock(self)
        for glyph, label, preset in DOCK_ITEMS:
            # preset is None for the locally handled entries (§6.10), which
            # is exactly the set that must stay live during a turn.
            button = DockButton(glyph, label, self.dock, dispatches=preset is not None)
            button.clicked.connect(lambda checked=False, g=glyph, p=preset: self._on_dock_clicked(g, p))
            self.dock.add_button(button)
        self._register(self.dock)
        return self.dock
