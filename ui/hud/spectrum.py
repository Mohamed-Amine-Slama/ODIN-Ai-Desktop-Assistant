"""Spectrum — ODIN-HUD.md §5.6, zone K's audio analyser.

No AnalyserNode equivalent exists in Qt. Loopback capture is feasible
without any new heavy dependency: `sounddevice` (already optional, in
requirements.txt) wraps PortAudio, which supports WASAPI loopback on
Windows. A small FFT over the captured ring buffer (numpy, also already an
optional dependency of the voice path) stands in for AnalyserNode.

Falls back to a seeded, smoothly-varying pseudo-random animation when no
audio source is available — the spec's own explicit instruction (§5.6):
never show a dead flat analyser.
"""
from __future__ import annotations

import itertools
import math
import random
import threading
from collections import deque

from PyQt6.QtCore import Qt, QRectF
from PyQt6.QtGui import QPainter, QPainterPath
from PyQt6.QtWidgets import QWidget

import config
from . import tokens

BAR_COUNT = 48
CELL_H = 3.0
CELL_GAP = 1.0
FFT_WINDOW = 128
PEAK_DECAY_PER_TICK = 0.03
LEVEL_SMOOTHING = 0.5


class _AudioCapture:
    """Owns one sounddevice InputStream, feeding a small ring buffer from
    its own callback thread — same shape as core.audio.Microphone, but
    self-contained (the spectrum doesn't share ODIN's mic stream)."""

    def __init__(self, source: str):
        self._source = source
        self._stream = None
        self._np = None
        self._buffer: deque[float] = deque(maxlen=FFT_WINDOW * 4)
        self._lock = threading.Lock()

    def start(self) -> bool:
        try:
            import numpy as np
            import sounddevice as sd
        except ImportError:
            return False
        self._np = np
        try:
            if self._source == "loopback":
                device = sd.query_devices(kind="output")
                extra = sd.WasapiSettings(loopback=True)
                self._stream = sd.InputStream(
                    device=device["name"], channels=1, samplerate=44100,
                    dtype="float32", callback=self._on_block, extra_settings=extra,
                )
            else:  # "mic"
                self._stream = sd.InputStream(
                    channels=1, samplerate=44100, dtype="float32", callback=self._on_block,
                )
            self._stream.start()
        except Exception:  # noqa: BLE001 - no loopback support, no device, wrong platform: all "--"
            self._stream = None
            return False
        return True

    def _on_block(self, indata, frames, time_info, status) -> None:  # noqa: ARG002
        with self._lock:
            self._buffer.extend(indata[:, 0].tolist())

    def stop(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:  # noqa: BLE001 - best-effort teardown
                pass
            self._stream = None

    def read_bins(self, n: int) -> list[float]:
        with self._lock:
            if len(self._buffer) < FFT_WINDOW:
                return [0.0] * n
            # Only the newest FFT_WINDOW samples matter for the FFT below.
            # reversed() on a deque is O(1) to start and O(FFT_WINDOW) to
            # drain, vs. O(len(buffer)) (up to 4x more) for list(self._buffer)
            # — this runs on the UI thread's 30fps tick under a lock shared
            # with the realtime audio callback in _on_block, so keeping the
            # critical section short matters more than the sample count alone
            # would suggest.
            tail = list(itertools.islice(reversed(self._buffer), FFT_WINDOW))
        window = self._np.array(tail[::-1], dtype=self._np.float32)
        magnitudes = self._np.abs(self._np.fft.rfft(window))
        usable = magnitudes[: len(magnitudes) // 2 or 1]  # drop the top bins — mostly empty (§5.6)
        bars = []
        for i in range(n):
            lo = int(i / n * len(usable))
            hi = max(lo + 1, int((i + 1) / n * len(usable)))
            bars.append(float(self._np.mean(usable[lo:hi])))
        peak = max(bars) or 1.0
        return [min(1.0, b / peak) for b in bars]


class Spectrum(QWidget):
    """Driven by the owning window's shared ~30fps loop (§10) — call
    `.advance(dt)` each tick, same as VoiceOrb."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(28)
        self._levels = [0.0] * BAR_COUNT
        self._peaks = [0.0] * BAR_COUNT
        self._rng = random.Random(7)
        self._fake_phase = 0.0
        self._capture: _AudioCapture | None = None

    def start_capture(self) -> None:
        if config.HUD_SPECTRUM_SOURCE == "off":
            return
        capture = _AudioCapture(config.HUD_SPECTRUM_SOURCE)
        self._capture = capture if capture.start() else None

    def stop_capture(self) -> None:
        if self._capture is not None:
            self._capture.stop()
            self._capture = None

    def advance(self, dt: float) -> None:
        bins = self._capture.read_bins(BAR_COUNT) if self._capture is not None else self._fake_bins(dt)
        for i, target in enumerate(bins):
            level = max(0.0, min(1.0, target))
            self._levels[i] += (level - self._levels[i]) * LEVEL_SMOOTHING
            self._peaks[i] = max(self._levels[i], self._peaks[i] - PEAK_DECAY_PER_TICK)
        self.update()

    def _fake_bins(self, dt: float) -> list[float]:
        self._fake_phase += dt
        bins = []
        for i in range(BAR_COUNT):
            base = 0.3 + 0.22 * math.sin(self._fake_phase * 0.6 + i * 0.35)
            bins.append(max(0.0, min(1.0, base + self._rng.uniform(-0.05, 0.05))))
        return bins

    def paintEvent(self, _event) -> None:
        # Perf: this used to be one fillRect() native draw call per lit LED
        # cell — up to BAR_COUNT * max_cells (400-900+) of them every frame
        # at 30fps. Cells are batched into one QPainterPath per color tier
        # instead, so this is at most 4 fillPath calls (3 body tiers + 1
        # peak tier) regardless of how many cells are actually lit.
        painter = QPainter(self)
        rect = self.rect()
        n = BAR_COUNT
        bar_w = max(1.0, (rect.width() - CELL_GAP * (n - 1)) / n)
        period = CELL_H + CELL_GAP
        max_cells = max(1, int(rect.height() / period))

        low_path = QPainterPath()
        mid_path = QPainterPath()
        high_path = QPainterPath()
        peak_path = QPainterPath()

        for i in range(n):
            x = i * (bar_w + CELL_GAP)
            filled = int(self._levels[i] * max_cells)
            for c in range(filled):
                frac = c / max_cells
                path = low_path if frac < 0.6 else mid_path if frac < 0.85 else high_path
                y = rect.height() - (c + 1) * period
                path.addRect(QRectF(x, y, bar_w, CELL_H))

            peak_cell = int(self._peaks[i] * max_cells)
            if peak_cell > 0:
                y = rect.height() - peak_cell * period
                peak_path.addRect(QRectF(x, y, bar_w, CELL_H))

        painter.setPen(Qt.PenStyle.NoPen)
        for path, color in (
            (low_path, tokens.CY_400),
            (mid_path, tokens.CY_200),
            (high_path, tokens.CY_100),
            (peak_path, tokens.CY_100),
        ):
            if not path.isEmpty():
                painter.fillPath(path, color)
        painter.end()
