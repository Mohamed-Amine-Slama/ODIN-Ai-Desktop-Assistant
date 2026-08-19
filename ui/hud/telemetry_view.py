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


def _compact(value: float) -> str:
    """Thousands as `8.4K` — a five-digit context-switch rate would blow out
    a readout that shares its line with two others."""
    if value >= 1000:
        return f"{value / 1000:.1f}K"
    return f"{value:.0f}"


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
        # One FFT per tick, drawn twice: zone K's analyser and the orb's
        # bezel ring read the same band levels.
        window.orb.set_bands(window.spectrum.levels)
        window.gauge_cpu.advance(dt)
        window.gauge_ram.advance(dt)
        window.gauge_disk.advance(dt)
        window.gauge_gpu.advance(dt)
        # Every side-panel instrument, registered by its own zone builder
        # (ui/hud/zones.py's _register) — one list, so a newly added widget
        # can't be silently left frozen.
        for instrument in window._instruments:
            instrument.advance(dt)

    # -- telemetry -----------------------------------------------------

    def render_frame(self, frame: TelemetryFrame) -> None:
        window = self.window
        self._latest_frame = frame

        cpu = frame.cpu
        window.cpu_hero.set_value(cpu.percent)
        window.cpu_hero.set_caption(f"{cpu.processes} PROC")
        window.cpu_graph.push(cpu.percent)
        window.cpu_graph.set_accent(tokens.threshold_color(cpu.percent / 100))
        window.core_strip.set_values(cpu.per_core)
        window.cpu_freq.set_value("--" if cpu.freq_mhz is None else f"{cpu.freq_mhz} MHZ")
        window.cpu_split.set_value(f"{cpu.user_pct:.0f}/{cpu.system_pct:.0f}%")
        window.cpu_ctx.set_value(_compact(cpu.ctx_per_sec))
        window.cpu_procs.set_rows(cpu.top)

        mem = frame.mem
        window.ram_hero.set_value(mem.percent)
        window.ram_hero.set_caption(f"{mem.used_gb:.1f}/{mem.total_gb:.0f} GB")
        window.ram_graph.push(mem.percent)
        window.ram_graph.set_accent(tokens.threshold_color(mem.percent / 100))
        window.ram_avail.set_value(f"{mem.available_gb:.1f} GB FREE")
        window.ram_procs.set_rows(mem.top)
        window.swap_bar.set_value(mem.swap_percent / 100, f"{mem.swap_percent:.0f}%")

        window.gauge_cpu.set_percent(frame.cpu.percent)
        window.gauge_ram.set_percent(frame.mem.percent)
        window.gauge_disk.set_percent(frame.disks[0].percent if frame.disks else None)
        window.gauge_gpu.set_percent(frame.thermals.gpu_load)

        self.update_disks(frame.disks)
        window.disk_io_read.set_value(f"{frame.disk_io.read_mbs:.1f} MB/S")
        window.disk_io_write.set_value(f"{frame.disk_io.write_mbs:.1f} MB/S")
        window.disk_read_graph.push(frame.disk_io.read_mbs)
        window.disk_write_graph.push(frame.disk_io.write_mbs)

        net = frame.net
        window.net_ip.set_value(net.ip or "--")
        window.net_totals.set_value(f"{net.total_down_gb:.1f}/{net.total_up_gb:.1f} GB")
        window.net_hero_down.set_value(net.down_kbs)
        window.net_hero_down.set_caption(f"UP {net.up_kbs:.0f}")
        window.net_graph_down.push(net.down_kbs)
        window.net_graph_up.push(net.up_kbs)
        window.net_nics.set_rows([(nic.name, nic.down_kbs + nic.up_kbs) for nic in net.nics])

        thermals = frame.thermals
        window.temp_arc_cpu.set_value(thermals.cpu_c)
        window.temp_arc_gpu.set_value(thermals.gpu_c)
        window.temp_arc_load.set_value(thermals.gpu_load)
        window.temp_vram.set_value(
            "--" if thermals.gpu_vram_percent is None else f"{thermals.gpu_vram_percent:.0f}%"
        )
        window.temp_fan.set_value("--" if thermals.fan_rpm is None else f"{thermals.fan_rpm:.0f} RPM")
        window.battery.set_state(
            frame.battery.percent, frame.battery.plugged, frame.battery.secs_left
        )

        d = int(frame.uptime_sec // 86400)
        h = int((frame.uptime_sec % 86400) // 3600)
        m = int((frame.uptime_sec % 3600) // 60)
        window.uptime_label.setText(f"UP {d}D {h}H {m}M")
        window.clock_uptime.set_value(f"{d}D {h}H {m}M")
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
        elapsed = now.hour * 3600 + now.minute * 60 + now.second
        window.clock_day.set_value(elapsed / 86400, now.strftime("%H:%M"))

    # -- weather ---------------------------------------------------------

    def render_weather(self, sample: WeatherSample | None) -> None:
        window = self.window
        if sample is None:
            window.weather_temp.setText("--°")
            window.weather_condition.setText("NO SIGNAL")
            window.weather_forecast.set_forecast([])
            window.weather_humidity_feels.set_value("--")
            window.weather_wind_pressure.set_value("--")
            window.weather_sun.set_value("--")
            return

        window.weather_temp.setText("--°" if sample.temp_c is None else f"{sample.temp_c:.0f}°C")
        window.weather_condition.setText((sample.condition or "--").strip().upper())
        # The multi-day outlook has always been fetched; until now nothing drew it.
        window.weather_forecast.set_forecast(sample.forecast or [])

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
