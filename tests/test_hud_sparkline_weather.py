"""ui/hud/sparkline.py and ui/hud/weather.py."""
from unittest.mock import MagicMock

from PyQt6.QtGui import QPixmap

from ui.hud.sparkline import MAX_SAMPLES, Sparkline
from ui.hud.weather import fetch_weather


def test_sparkline_push_updates_the_scale_target(qapp):
    spark = Sparkline("%")
    spark.push(10)
    spark.push(90)
    # 15% headroom over the rolling window's max (§5.5)
    assert spark._anim.endValue() >= 90 * 1.15 - 0.01


def test_sparkline_caps_its_rolling_window(qapp):
    spark = Sparkline()
    for i in range(MAX_SAMPLES + 20):
        spark.push(i)
    assert len(spark._samples) == MAX_SAMPLES


def test_sparkline_renders_with_zero_one_and_many_samples(qapp):
    spark = Sparkline("MB/S")
    spark.resize(160, 48)

    def render():
        pixmap = QPixmap(spark.size())
        spark.render(pixmap)

    render()  # zero samples
    spark.push(5)
    render()  # one sample
    for v in (1, 4, 2, 8, 3):
        spark.push(v)
    render()  # several samples


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
