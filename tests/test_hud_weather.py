"""ui/hud/weather.py."""
from unittest.mock import MagicMock

from ui.hud.weather import fetch_weather


def test_fetch_weather_returns_none_on_network_failure(monkeypatch):
    def _raise(*_args, **_kwargs):
        raise ConnectionError("no network")

    monkeypatch.setattr("requests.get", _raise)
    assert fetch_weather() is None


def test_fetch_weather_returns_none_on_malformed_json(monkeypatch):
    response = MagicMock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"unexpected": "shape"}
    monkeypatch.setattr("requests.get", lambda *a, **k: response)
    assert fetch_weather() is None


def test_fetch_weather_parses_a_well_formed_response(monkeypatch):
    payload = {
        "current_condition": [{
            "temp_C": "21", "FeelsLikeC": "20", "humidity": "55",
            "windspeedKmph": "12", "pressure": "1012",
            "weatherDesc": [{"value": "Partly cloudy"}],
        }],
        "weather": [
            {
                "date": "2026-08-12", "mintempC": "15", "maxtempC": "25",
                "astronomy": [{"sunrise": "06:00 AM", "sunset": "08:00 PM"}],
            },
            {"date": "2026-08-13", "mintempC": "14", "maxtempC": "24", "astronomy": [{}]},
            {"date": "2026-08-14", "mintempC": "13", "maxtempC": "23", "astronomy": [{}]},
        ],
    }
    response = MagicMock()
    response.raise_for_status.return_value = None
    response.json.return_value = payload
    monkeypatch.setattr("requests.get", lambda *a, **k: response)

    sample = fetch_weather()
    assert sample is not None
    assert sample.temp_c == 21.0
    assert sample.condition == "Partly cloudy"
    assert sample.sunrise == "06:00 AM"
    assert len(sample.forecast) == 3
