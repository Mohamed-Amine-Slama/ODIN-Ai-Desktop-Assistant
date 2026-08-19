"""ui/hud/instruments.py — the widgets the rebuilt side panels are made of.

All six advance off the window's one shared loop (ODIN-HUD.md §10) rather
than owning timers, and all of them are fed at telemetry's ~1Hz while being
drawn at frame rate — so the interesting behaviour is what happens *between*
readings.
"""
from PyQt6.QtGui import QPixmap

from ui.hud import tokens
from ui.hud.instruments import (
    BatteryMeter,
    ForecastStrip,
    HeroValue,
    MetricGraph,
    MiniArc,
    ProcessRows,
)


def _render(widget, size=(240, 90)):
    widget.resize(*size)
    pixmap = QPixmap(widget.size())
    widget.render(pixmap)
    return pixmap


# -- MetricGraph ------------------------------------------------------------


def test_graph_keeps_only_its_capacity_of_history(qapp):
    graph = MetricGraph(capacity=10)
    for i in range(40):
        graph.push(float(i))
    assert len(graph.samples) == 10
    assert graph.samples[-1] == 39.0


def test_graph_autoscales_to_what_it_has_seen(qapp):
    """CPU is a fixed 0..100, but network rates have no ceiling — an
    autoscaling graph is the only way those stay readable."""
    graph = MetricGraph(capacity=8)
    for value in (10.0, 400.0, 90.0):
        graph.push(value)
    assert graph.scale_max >= 400.0

    fixed = MetricGraph(capacity=8, maximum=100.0)
    fixed.push(5000.0)
    assert fixed.scale_max == 100.0


def test_graph_scrolls_between_readings(qapp):
    """Telemetry lands about once a second but the graph is drawn at frame
    rate: without sub-sample scrolling it would visibly step."""
    graph = MetricGraph(capacity=10, interval=1.0)
    graph.push(1.0)
    assert graph.scroll_phase == 0.0

    graph.advance(0.25)
    assert 0.2 < graph.scroll_phase < 0.3

    graph.push(2.0)
    assert graph.scroll_phase == 0.0  # a new reading resets the slide


def test_the_trace_spans_the_panel_from_the_first_readings(qapp):
    """It used to be laid out against the buffer's capacity, so a freshly
    opened HUD showed a trace crammed into the right third of its panel with
    a minute of blank to its left."""
    graph = MetricGraph(capacity=60)
    graph.resize(200, 60)
    for value in (10.0, 20.0, 15.0):
        graph.push(value)

    bounds = graph.trace_path().boundingRect()

    assert bounds.left() <= 1
    assert bounds.right() >= 199


def test_the_trace_only_scrolls_once_its_history_is_full(qapp):
    """While the buffer is still filling the trace grows into the panel;
    sliding it as well would make the whole line lurch sideways every tick."""
    graph = MetricGraph(capacity=4, interval=1.0)
    graph.resize(200, 60)
    graph.push(1.0)
    graph.push(2.0)
    graph.advance(0.5)
    assert graph.scroll_offset() == 0.0

    graph.push(3.0)
    graph.push(4.0)          # buffer now full
    graph.advance(0.5)
    assert graph.scroll_offset() < 0.0


def test_graph_scroll_phase_never_runs_past_a_full_step(qapp):
    """A stalled telemetry thread must not slide the trace off its own axis."""
    graph = MetricGraph(capacity=10, interval=1.0)
    graph.push(1.0)
    for _ in range(120):
        graph.advance(0.1)
    assert graph.scroll_phase <= 1.0


def test_graph_rebuilds_its_path_only_when_data_arrives(qapp):
    """Perf: the trace geometry changes once per reading, not once per frame
    — frames only translate what's already built."""
    graph = MetricGraph(capacity=10)
    graph.resize(200, 60)
    graph.push(1.0)
    path = graph.trace_path()
    graph.advance(1 / 60)
    assert graph.trace_path() is path

    graph.push(2.0)
    assert graph.trace_path() is not path


def test_graph_renders_empty_and_populated(qapp):
    graph = MetricGraph(capacity=30)
    _render(graph)
    for i in range(30):
        graph.push(i * 3.0)
    _render(graph)


# -- HeroValue --------------------------------------------------------------


def test_hero_eases_toward_its_target(qapp):
    hero = HeroValue("CPU", "%")
    hero.set_value(0.0)
    hero.set_value(80.0)
    assert hero.displayed < 80.0

    for _ in range(200):
        hero.advance(1 / 60)
    assert round(hero.displayed) == 80.0


def test_hero_recolors_past_the_threshold(qapp):
    hero = HeroValue("CPU", "%", maximum=100.0)
    hero.set_value(20.0)
    for _ in range(200):
        hero.advance(1 / 60)
    calm = hero.accent()

    hero.set_value(97.0)
    for _ in range(200):
        hero.advance(1 / 60)
    assert hero.accent() != calm
    assert hero.accent() == tokens.CRIT


def test_hero_renders(qapp):
    hero = HeroValue("RAM", "%")
    hero.set_value(67.0)
    _render(hero)


# -- MiniArc ----------------------------------------------------------------


def test_mini_arc_eases_and_clamps(qapp):
    arc = MiniArc("CPU", "°C", minimum=20.0, maximum=100.0)
    arc.set_value(150.0)
    for _ in range(200):
        arc.advance(1 / 60)
    assert arc.fraction == 1.0

    arc.set_value(None)          # sensor unavailable
    assert arc.fraction == 0.0
    _render(arc, size=(90, 90))


def test_mini_arc_renders_at_every_level(qapp):
    arc = MiniArc("GPU", "°C", minimum=20.0, maximum=100.0)
    for value in (None, 20.0, 55.0, 85.0, 99.0):
        arc.set_value(value)
        for _ in range(60):
            arc.advance(1 / 60)
        _render(arc, size=(90, 90))


# -- ProcessRows ------------------------------------------------------------


def test_process_rows_show_the_busiest_first_and_tolerate_short_lists(qapp):
    rows = ProcessRows(count=3, unit="%")
    rows.set_rows([("chrome.exe", 18.4), ("python.exe", 9.1)])
    assert rows.rows[0][0] == "chrome.exe"
    assert len(rows.rows) == 2
    _render(rows)

    rows.set_rows([])            # nothing to report yet
    _render(rows)


def test_process_rows_scale_their_bars_to_the_largest_row(qapp):
    rows = ProcessRows(count=3, unit="GB")
    rows.set_rows([("a.exe", 4.0), ("b.exe", 1.0)])
    assert rows.row_fraction(0) == 1.0
    assert rows.row_fraction(1) == 0.25


# -- ForecastStrip ----------------------------------------------------------


def test_forecast_strip_renders_days_and_survives_missing_data(qapp):
    strip = ForecastStrip()
    _render(strip, size=(300, 60))          # nothing fetched yet

    strip.set_forecast([("2026-08-20", 19.0, 29.0), ("2026-08-21", 20.0, 31.0)])
    assert len(strip.days) == 2
    _render(strip, size=(300, 60))


def test_forecast_bars_span_the_whole_range_seen(qapp):
    strip = ForecastStrip()
    strip.set_forecast([("2026-08-20", 10.0, 20.0), ("2026-08-21", 15.0, 30.0)])
    assert strip.range_c == (10.0, 30.0)


# -- BatteryMeter -----------------------------------------------------------


def test_battery_meter_reports_charge_state(qapp):
    meter = BatteryMeter()
    meter.set_state(percent=54.0, plugged=False, secs_left=5400)
    assert meter.caption == "1H 30M LEFT"
    _render(meter, size=(200, 40))

    meter.set_state(percent=100.0, plugged=True, secs_left=None)
    assert meter.caption == "CHARGED"
    _render(meter, size=(200, 40))

    meter.set_state(percent=72.0, plugged=True, secs_left=None)
    assert meter.caption == "CHARGING"


def test_battery_meter_without_a_battery_says_so(qapp):
    meter = BatteryMeter()
    meter.set_state(percent=None, plugged=None, secs_left=None)
    assert meter.caption == "NO BATTERY"
    _render(meter, size=(200, 40))


def test_hero_without_a_ceiling_never_recolors(qapp):
    """Network rates have no meaningful maximum, so threshold colouring would
    be meaningless — the value stays on the primary accent however big it gets."""
    hero = HeroValue("DOWN", "KB/S", maximum=None)
    hero.set_value(48_000.0)
    for _ in range(200):
        hero.advance(1 / 60)
    assert hero.accent() == tokens.CY_300
    _render(hero)
