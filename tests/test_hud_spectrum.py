"""ui/hud/spectrum.py — the fake-bins fallback path and paintEvent render
without raising, batched into per-color QPainterPath fills rather than one
fillRect() per lit LED cell (a real, measured 30fps cost with BAR_COUNT *
up-to-~20 cells all potentially lit at once)."""
from PyQt6.QtGui import QPixmap

from ui.hud.spectrum import BAR_COUNT, Spectrum


def _render(widget, size=(480, 40)):
    widget.resize(*size)
    pixmap = QPixmap(widget.size())
    widget.render(pixmap)
    return pixmap


def test_fake_bins_render_without_a_capture_source(qapp):
    spectrum = Spectrum()
    for _ in range(5):
        spectrum.advance(1 / 30)
        _render(spectrum)
    assert len(spectrum._levels) == BAR_COUNT
    assert all(0.0 <= v <= 1.0 for v in spectrum._levels)


def test_fully_lit_bars_still_render(qapp):
    """Forces every cell in every bar to be lit — the densest case for the
    batched-path rendering, and the shape that used to mean hundreds of
    individual fillRect() calls in one frame."""
    spectrum = Spectrum()
    spectrum._levels = [1.0] * BAR_COUNT
    spectrum._peaks = [1.0] * BAR_COUNT
    _render(spectrum, size=(480, 120))  # taller widget -> more lit cells per bar


def test_zero_level_bars_render_without_error(qapp):
    spectrum = Spectrum()
    spectrum._levels = [0.0] * BAR_COUNT
    spectrum._peaks = [0.0] * BAR_COUNT
    _render(spectrum)
