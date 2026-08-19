"""ui/hud/radial_gauge.py — arc math, threshold recolor, eased transitions."""
from PyQt6.QtGui import QPixmap
from PyQt6.QtTest import QTest

from ui.hud import tokens
from ui.hud.radial_gauge import RadialGauge


def _render(gauge, size=(100, 100)):
    gauge.resize(*size)
    pixmap = QPixmap(gauge.size())
    gauge.render(pixmap)
    return pixmap


def test_set_percent_targets_the_right_fraction(qapp):
    gauge = RadialGauge()
    gauge.set_percent(50)
    assert gauge._anim.endValue() == 0.5
    gauge.set_percent(100)
    assert gauge._anim.endValue() == 1.0
    gauge.set_percent(0)
    assert gauge._anim.endValue() == 0.0


def test_set_percent_none_targets_zero_and_shows_dashes(qapp):
    gauge = RadialGauge()
    gauge.set_percent(None)
    assert gauge._anim.endValue() == 0.0
    assert gauge._display is None
    _render(gauge)  # must render "--" without raising


def test_value_animation_reaches_its_target(qapp):
    gauge = RadialGauge()
    gauge.set_percent(80)
    QTest.qWait(tokens.DUR_VAL + 100)
    assert abs(gauge.value - 0.8) < 0.01


def test_threshold_recolor_matches_tokens(qapp):
    gauge = RadialGauge()
    gauge.setValue(0.5)
    assert tokens.threshold_color(gauge._value) == tokens.CY_300
    gauge.setValue(0.8)
    assert tokens.threshold_color(gauge._value) == tokens.WARN
    gauge.setValue(0.95)
    assert tokens.threshold_color(gauge._value) == tokens.CRIT


def test_critical_value_pulses_via_the_shared_animation_loop(qapp):
    """No private QTimer (ui/hud/radial_gauge.py) — the pulse rides the
    same shared advance(dt) loop as the orb/spectrum, one call per tick
    instead of up to four independent 30ms timers."""
    gauge = RadialGauge()
    gauge.set_percent(95)
    assert gauge._is_critical
    gauge.advance(0.1)
    assert gauge._pulse_phase > 0.0

    gauge.set_percent(50)
    assert not gauge._is_critical
    assert gauge._pulse_phase == 0.0
    gauge.advance(0.1)  # a no-op once not critical
    assert gauge._pulse_phase == 0.0


def test_renders_at_every_percent_without_raising(qapp):
    gauge = RadialGauge("%")
    for pct in (0, 1, 25, 50, 75, 90, 99, 100):
        gauge.setValue(pct / 100)
        _render(gauge)
