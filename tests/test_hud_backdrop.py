"""ui/hud/zones.py's background layers.

The backdrop paints a full-screen gradient plus a grid and ~360 scanlines.
None of that ever changes, but every translucent widget on the HUD dirties
the region beneath it, so it was being repainted twice a frame at ~72ms a
paint — by far the most expensive thing on the HUD, and the actual source of
the lag the panels were being blamed for. It is now rendered once into a
pixmap and blitted.
"""
from PyQt6.QtGui import QPixmap

from ui.hud.zones import _Backdrop, _CircuitTraces


def _render(widget):
    pixmap = QPixmap(widget.size())
    widget.render(pixmap)
    return pixmap


def test_backdrop_renders_its_layers_once_and_reuses_them(qapp):
    backdrop = _Backdrop()
    backdrop.resize(400, 300)

    _render(backdrop)
    cached = backdrop.cached_layers()
    assert cached is not None
    assert cached.size() == backdrop.size()

    _render(backdrop)
    assert backdrop.cached_layers() is cached      # not rebuilt per paint


def test_backdrop_rebuilds_its_cache_when_the_screen_size_changes(qapp):
    backdrop = _Backdrop()
    backdrop.resize(400, 300)
    _render(backdrop)
    first = backdrop.cached_layers()

    backdrop.resize(800, 600)
    _render(backdrop)

    assert backdrop.cached_layers() is not first
    assert backdrop.cached_layers().size() == backdrop.size()


def test_backdrop_survives_a_zero_size(qapp):
    backdrop = _Backdrop()
    backdrop.resize(0, 0)
    _render(backdrop)      # must not raise or build a null pixmap


def test_circuit_traces_are_cached_too(qapp):
    traces = _CircuitTraces()
    traces.resize(400, 300)

    _render(traces)
    cached = traces.cached_layers()
    assert cached is not None

    _render(traces)
    assert traces.cached_layers() is cached
