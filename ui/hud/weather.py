"""Structured weather for zone G (ODIN-HUD.md §6.6).

skills/web_skills.py's WeatherSkill hits wttr.in's plain-string endpoint
(`?format=3`) for conversational answers — it has no humidity, feels-like,
wind, pressure, sunrise/sunset, or forecast, which the HUD panel needs. This
uses wttr.in's richer JSON endpoint (`?format=j1`) instead, on its own
QThread polled slowly: it's a network call, and must never block the GUI
thread the way a synchronous request in a paintEvent or a QTimer callback
would.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field

from PyQt6.QtCore import QThread, pyqtSignal

import config

TIMEOUT_SECONDS = 8


@dataclass
class WeatherSample:
    temp_c: float | None
    condition: str | None
    humidity: float | None
    feels_like_c: float | None
    wind_kph: float | None
    pressure_mb: float | None
    sunrise: str | None
    sunset: str | None
    forecast: list[tuple[str, float, float]] = field(default_factory=list)  # (date, min_c, max_c)


def fetch_weather() -> WeatherSample | None:
    """None on any failure — the panel renders `--` throughout rather than
    a stale or fabricated reading (ODIN-HUD.md §10)."""
    import requests

    city = config.WEATHER_CITY.strip()
    url = f"https://wttr.in/{city}?format=j1" if city else "https://wttr.in/?format=j1"
    try:
        response = requests.get(url, timeout=TIMEOUT_SECONDS, headers={"User-Agent": "curl/8.0"})
        response.raise_for_status()
        data = response.json()

        current = data["current_condition"][0]
        today = data["weather"][0]
        forecast = [
            (day.get("date", ""), float(day["mintempC"]), float(day["maxtempC"]))
            for day in data["weather"][:3]
        ]
        return WeatherSample(
            temp_c=float(current["temp_C"]),
            condition=current["weatherDesc"][0]["value"],
            humidity=float(current["humidity"]),
            feels_like_c=float(current["FeelsLikeC"]),
            wind_kph=float(current["windspeedKmph"]),
            pressure_mb=float(current["pressure"]),
            sunrise=today["astronomy"][0]["sunrise"],
            sunset=today["astronomy"][0]["sunset"],
            forecast=forecast,
        )
    except Exception:  # noqa: BLE001 - network/parse errors both mean "--", not a crash
        return None


class WeatherWorker(QThread):
    weather_ready = pyqtSignal(object)  # WeatherSample | None

    def __init__(self, parent=None):
        super().__init__(parent)
        self._stop_event = threading.Event()

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        while not self._stop_event.is_set():
            self.weather_ready.emit(fetch_weather())
            self._stop_event.wait(config.HUD_WEATHER_POLL_SECONDS)
