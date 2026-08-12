"""ui/hud/widgets.py — Panel, Readout, BarMeter, DockButton, TickRuler."""
from datetime import datetime

from PyQt6.QtGui import QPixmap

from ui.hud import tokens
from ui.hud.widgets import BarMeter, DockButton, Panel, Readout, TickRuler


def _render(widget, size=(200, 80)):
    widget.resize(*size)
    pixmap = QPixmap(widget.size())
    widget.render(pixmap)
    return pixmap


def test_panel_renders_with_and_without_a_status_pip(qapp):
    panel = Panel("CPU")
    _render(panel)
    panel.set_status("crit")
    _render(panel)  # must not raise with a status pip present


def test_panel_body_layout_accepts_children(qapp):
    panel = Panel("MEMORY")
    label = Readout("USED", "18.4 GB")
    panel.body_layout.addWidget(label)
    assert panel.body_layout.count() == 1


def test_readout_set_value_and_set_label_update_without_raising(qapp):
    row = Readout("FREQ", "3592 MHZ")
    _render(row)
    row.set_value("3600 MHZ")
    row.set_label("cpu freq")  # lowercase in, uppercase stored
    assert row._label == "CPU FREQ"
    _render(row)


def test_bar_meter_recolors_at_thresholds(qapp):
    bar = BarMeter("CPU")
    bar.set_value(0.40, "40%")
    assert tokens.threshold_color(bar._fraction) == tokens.CY_300
    bar.set_value(0.80, "80%")
    assert tokens.threshold_color(bar._fraction) == tokens.WARN
    bar.set_value(0.95, "95%")
    assert tokens.threshold_color(bar._fraction) == tokens.CRIT


def test_bar_meter_handles_none_fraction_as_zero(qapp):
    bar = BarMeter("GPU")
    bar.set_value(None, "--")
    assert bar._fraction == 0.0
    _render(bar)


def test_bar_meter_peak_tracks_and_decays(qapp):
    bar = BarMeter("NET")
    bar.set_value(0.9, "90%")
    assert bar._peak >= 0.9
    bar.set_value(0.1, "10%")
    # the peak marker should still be above the current fraction, decaying
    # rather than snapping straight down to it
    assert bar._peak > 0.1


def test_dock_button_emits_clicked(qapp):
    button = DockButton("SYS", "Task Manager")
    received = []
    button.clicked.connect(lambda: received.append(True))
    button.click()
    assert received == [True]


def test_dock_button_hover_and_focus_paint_without_raising(qapp):
    button = DockButton("FILES", "Explorer")
    _render(button)
    button.setFocus()
    _render(button)


def test_tick_ruler_caret_tracks_time_of_day(qapp):
    ruler = TickRuler()
    ruler.resize(480, 20)
    ruler._now = datetime(2026, 1, 1, 0, 0, 0)
    _render(ruler)
    midnight_pixmap = QPixmap(ruler.size())
    ruler.render(midnight_pixmap)

    ruler._now = datetime(2026, 1, 1, 12, 0, 0)
    ruler.update()
    noon_pixmap = QPixmap(ruler.size())
    ruler.render(noon_pixmap)

    # A caret at midnight (x=0) and noon (x=width/2) must land in visibly
    # different places — different images, not a strict pixel assertion
    # (font/AA rendering varies by platform).
    assert midnight_pixmap.toImage() != noon_pixmap.toImage()
