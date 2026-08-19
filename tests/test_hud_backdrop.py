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


# -- the hexagon field ------------------------------------------------------


def test_the_field_is_static_and_costs_nothing_per_frame(qapp):
    """Black, tiled, and still. It carries no information and nothing on it
    moves, so it is rendered once and blitted — it must never join the
    animation loop, where it would drag every panel above it into each frame."""
    backdrop = _Backdrop()
    backdrop.resize(800, 600)
    _render(backdrop)

    assert not hasattr(backdrop, "advance")
    assert not hasattr(backdrop, "lit_edges")


def test_the_field_is_black(qapp):
    from PyQt6.QtGui import QColor

    backdrop = _Backdrop()
    backdrop.resize(400, 300)
    pixmap = QPixmap(backdrop.size())
    pixmap.fill(QColor(255, 0, 0))     # so an unpainted pixel would be obvious
    backdrop.render(pixmap)
    image = pixmap.toImage()

    samples = [image.pixelColor(x, y) for x in range(5, 400, 37) for y in range(5, 300, 29)]
    assert all(c.red() < 40 and c.green() < 40 and c.blue() < 55 for c in samples)
    # The tiling has to be visible against it, not a flat fill.
    assert len({(c.red(), c.green(), c.blue()) for c in samples}) > 1
