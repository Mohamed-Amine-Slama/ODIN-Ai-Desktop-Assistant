"""TelemetryPresenter — renders TelemetryWorker/WeatherWorker output (plus
the notes/reminders panel and the shared ~30fps animation tick) onto
OdinHudWindow's zone widgets. Pulled out of window.py verbatim; kept as a
lazy back-reference to the window (`self.window`), not a registry of
individual widget refs, because ui/hud/zones.py's _build_zone_f calls
window._on_clock_tick() synchronously during _build_ui() to paint the clock
once before its 1s timer's first tick — this presenter has to exist before
any zone widget it touches has been built. Same pattern ui/hud/boot.py's
run_boot_sequence(window) already uses.
"""
from __future__ import annotations

import time
from datetime import datetime

from PyQt6.QtWidgets import QLabel

from core.store import get_store
from . import tokens
from .telemetry import TelemetryFrame
from .weather import WeatherSample
from .widgets import BarMeter


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


class TelemetryPresenter:
    def __init__(self, window: "OdinHudWindow") -> None:  # noqa: F821 - see module docstring
        self.window = window
        self._latest_frame: TelemetryFrame | None = None
        self._disk_bars: dict[str, BarMeter] = {}
        self._notes_rows: list[QLabel] = []
        self._last_tick = time.monotonic()

    def reset_animation_clock(self) -> None:
        self._last_tick = time.monotonic()

    # -- shared ~30fps animation loop (ODIN-HUD.md §10) ---------------------

    def advance_animation(self) -> None:
        window = self.window
        now = time.monotonic()
        dt = now - self._last_tick
        self._last_tick = now
        window.orb.advance(dt)
        window.spectrum.advance(dt)

    # -- telemetry -----------------------------------------------------

    def render_frame(self, frame: TelemetryFrame) -> None:
        window = self.window
        self._latest_frame = frame

        window.cpu_bar.set_value(frame.cpu.percent / 100, f"{frame.cpu.percent:.0f}%")
        window.cpu_freq.set_value("--" if frame.cpu.freq_mhz is None else f"{frame.cpu.freq_mhz} MHZ")
        window.core_strip.set_values(frame.cpu.per_core)
        window.cpu_processes.set_value(str(frame.cpu.processes))
        for i, row in enumerate(window.cpu_top_rows):
            if i < len(frame.cpu.top):
                name, cpu = frame.cpu.top[i]
                row.set_label(name[:18])
                row.set_value(f"{cpu:.0f}%")
            else:
                row.set_label("--")
                row.set_value("--")

        window.ram_bar.set_value(frame.mem.percent / 100, f"{frame.mem.percent:.0f}%")
        window.ram_spark.push(frame.mem.percent)
        window.swap_bar.set_value(frame.mem.swap_percent / 100, f"{frame.mem.swap_percent:.0f}%")

        window.gauge_cpu.set_percent(frame.cpu.percent)
        window.gauge_ram.set_percent(frame.mem.percent)
        window.gauge_disk.set_percent(frame.disks[0].percent if frame.disks else None)
        window.gauge_gpu.set_percent(frame.thermals.gpu_load)

        self.update_disks(frame.disks)
        window.disk_io_read.set_value(f"{frame.disk_io.read_mbs:.1f} MB/S")
        window.disk_io_write.set_value(f"{frame.disk_io.write_mbs:.1f} MB/S")

        window.net_ip.set_value(frame.net.ip or "--")
        window.net_up_spark.push(frame.net.up_kbs)
        window.net_down_spark.push(frame.net.down_kbs)

        window.temp_cpu.set_value("--" if frame.thermals.cpu_c is None else f"{frame.thermals.cpu_c:.0f}°C")
        window.temp_gpu.set_value("--" if frame.thermals.gpu_c is None else f"{frame.thermals.gpu_c:.0f}°C")
        window.temp_gpu_load.set_value(
            "--" if frame.thermals.gpu_load is None else f"{frame.thermals.gpu_load:.0f}%"
        )
        window.temp_vram.set_value(
            "--" if frame.thermals.gpu_vram_percent is None else f"{frame.thermals.gpu_vram_percent:.0f}%"
        )
        window.temp_fan.set_value("--" if frame.thermals.fan_rpm is None else f"{frame.thermals.fan_rpm:.0f} RPM")

        d = int(frame.uptime_sec // 86400)
        h = int((frame.uptime_sec % 86400) // 3600)
        m = int((frame.uptime_sec % 3600) // 60)
        window.uptime_label.setText(f"UP {d}D {h}H {m}M")
        window.link_pip.setStyleSheet(f"color: {tokens.OK.name()}; font-size: 10px;")

        if window.orb.state not in ("thinking", "learning"):
            load = 0.5 * frame.cpu.percent / 100 + 0.3 * frame.mem.percent / 100 + 0.2 * min(
                (frame.disk_io.read_mbs + frame.disk_io.write_mbs) / 50, 1.0
            )
            window.orb.set_system_load(load)

    def update_disks(self, disks) -> None:
        window = self.window
        seen = set()
        for disk in disks:
            seen.add(disk.mount)
            bar = self._disk_bars.get(disk.mount)
            if bar is None:
                bar = BarMeter(disk.mount)
                self._disk_bars[disk.mount] = bar
                window._storage_panel.body_layout.insertWidget(len(self._disk_bars) - 1, bar)
            bar.set_value(disk.percent / 100, f"{disk.used_gb:.0f}/{disk.total_gb:.0f} GB")
        for mount in list(self._disk_bars):
            if mount not in seen:
                widget = self._disk_bars.pop(mount)
                widget.setParent(None)
                widget.deleteLater()

    def render_clock(self) -> None:
        window = self.window
        now = datetime.now()
        window.clock_label.setText(now.strftime("%H:%M:%S"))
        window.date_label.setText(now.strftime("%A · %d %b %Y").upper())

    # -- weather ---------------------------------------------------------

    def render_weather(self, sample: WeatherSample | None) -> None:
        window = self.window
        if sample is None:
            window.weather_temp.setText("--°")
            window.weather_condition.setText("NO SIGNAL")
            window.weather_humidity_feels.set_value("--")
            window.weather_wind_pressure.set_value("--")
            window.weather_sun.set_value("--")
            return

        window.weather_temp.setText("--°" if sample.temp_c is None else f"{sample.temp_c:.0f}°C")
        window.weather_condition.setText((sample.condition or "--").strip().upper())

        humidity = "--" if sample.humidity is None else f"{sample.humidity:.0f}%"
        feels = "--" if sample.feels_like_c is None else f"{sample.feels_like_c:.0f}°C"
        window.weather_humidity_feels.set_value(f"{humidity} / {feels}")

        wind = "--" if sample.wind_kph is None else f"{sample.wind_kph:.0f}KM/H"
        pressure = "--" if sample.pressure_mb is None else f"{sample.pressure_mb:.0f}MB"
        window.weather_wind_pressure.set_value(f"{wind} / {pressure}")

        sun = f"{sample.sunrise} / {sample.sunset}" if sample.sunrise and sample.sunset else "--"
        window.weather_sun.set_value(sun)

    # -- notes / reminders -------------------------------------------------

    def refresh_notes_panel(self) -> None:
        panel = self.window._notes_panel
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
