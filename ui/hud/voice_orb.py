"""VoiceOrb — ODIN-HUD.md §5.3, the HUD's one signature, elaborate element.
Built fresh rather than extending ui/orb.py's ReactorOrb: that widget drives
the small always-on-top ambient OrbWindow (kept, unchanged, per the HUD
rebuild's own scope), repaints continuously as a lightweight background
presence, and answers to a different state set and a different visual
language (a particle swarm, not concentric rings + a launcher ring + a
triangular reactor core). They share only the state-color mapping in
ui/hud/tokens.py.

Not self-timed: `advance(dt)` is called by the owning window's one shared
~30fps loop (ODIN-HUD.md §10's "one shared requestAnimationFrame loop, not
one per widget") rather than this widget running its own QTimer.
"""
from __future__ import annotations

import math

from PyQt6.QtCore import QEasingCurve, QPointF, QPropertyAnimation, QRectF, Qt, QTimer, pyqtProperty, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPen, QRadialGradient
from PyQt6.QtWidgets import QWidget

from ui.molecule import MoleculeField

from . import tokens

STATES = ("idle", "listening", "thinking", "speaking", "learning", "error")

# The molecular field inside the orb (ui/molecule.py). One energy per state is
# the whole state mapping: it sets how hard the particles are kicked about and
# how far the cloud opens up, so a glance at the molecule reads as activity
# before the caption below the orb does. `listening` is computed from the mic
# level instead, since there the orb is metering something real.
FIELD_COUNT = 300
FIELD_ENERGY = {
    "idle": 0.16,
    "thinking": 0.95,
    "speaking": 0.60,
    "learning": 0.50,
    "error": 0.85,
}
FIELD_LISTENING_BASE = 0.34
FIELD_LISTENING_GAIN = 0.55
SPEAK_BEAT_PULSE = 0.55   # one word-beat's kick outward
IGNITION_PULSE = 1.2      # the core-ignition bloom (§8)

# The spectrum bezel — the outer ring, driven by the same band levels zone K's
# analyser draws (ui/hud/spectrum.py, forwarded each tick by TelemetryPresenter
# .advance_animation). Loopback hears system audio, so the ring dances with
# ODIN's own speech; while listening it meters the mic instead, since loopback
# is silent exactly when the user is talking.
# The entry animation's orb stage (ui/hud/boot.py): each ring owns a slice of
# the 0..1 reveal, so the orb assembles outside-in — one circle sweeping closed,
# then the next — instead of everything fading up together. The molecule's slice
# overlaps the last rings, so it condenses into an orb that's already there.
RING_REVEAL_WINDOWS = {
    "dash": (0.00, 0.20),
    "bezel": (0.12, 0.38),
    "launcher": (0.30, 0.58),
    "tick": (0.48, 0.72),
    "data": (0.62, 0.82),
    "molecule": (0.55, 1.00),
}


def _smoothstep(lo: float, hi: float, value: float) -> float:
    if value <= lo:
        return 0.0
    if value >= hi:
        return 1.0
    t = (value - lo) / (hi - lo)
    return t * t * (3.0 - 2.0 * t)


BEZEL_MIC_GAIN = 0.85
BEZEL_FLOOR = 0.06        # bars never fully disappear, even in dead silence

_STATUS_LABELS = {
    "idle": "IDLE",
    "listening": "LISTENING",
    "thinking": "PROCESSING",
    "speaking": "SPEAKING",
    "error": "ERROR",
}


def _lerp_color(a: QColor, b: QColor, t: float) -> QColor:
    t = max(0.0, min(1.0, t))
    return QColor(
        int(a.red() + (b.red() - a.red()) * t),
        int(a.green() + (b.green() - a.green()) * t),
        int(a.blue() + (b.blue() - a.blue()) * t),
    )


class VoiceOrb(QWidget):
    launcher_clicked = pyqtSignal(str)  # one of LAUNCHER_LABELS
    status_changed = pyqtSignal(str)    # display text for the caption beneath the orb (§6.4)

    REF = 440.0
    CENTER = 220.0
    R_OUTER = 200.0
    R_BEZEL = 186.0   # the spectrum bezel's baseline; bars grow outward from it
    R_DASH = 209.0    # the one ring that still turns, outside everything else
    R_LAUNCHER = 170.0
    R_TICK = 145.0
    R_DATA = 118.0
    R_FIELD = 114.0  # the molecule's shell, just inside the data ring
    R_CORE = 34.0  # the nucleus: small, so the molecule reads as the subject

    BEZEL_SEGMENTS = 48   # matches ui/hud/spectrum.py's BAR_COUNT: no resampling loss
    BEZEL_TIERS = 8       # brightness buckets, and so draw calls, for the bars
    BEZEL_EXTENT = 18.0   # a full-scale bar's length, reaching R_OUTER

    LAUNCHER_LABELS = ("SYS", "FILES", "WEB", "CODE", "MUSIC", "VOL", "LEARN", "PWR")  # §6.7

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(220, 220)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        self._state = "idle"
        # The molecule: a few hundred particles drifting freely inside the
        # data ring, bonded to whichever neighbours they happen to be near.
        # Advanced from this widget's own advance(), so it freezes with the
        # rings during the error flash and the pre-ignition boot hold.
        self.field = MoleculeField(FIELD_COUNT)
        self._phase = 0.0       # outer-ring rotation, degrees
        self._tick_phase = 0.0  # tick-ring counter-rotation, degrees
        self._breathe_phase = 0.0  # seconds accumulator for sinusoidal motion

        # §5.3 listening: "a second bright arc sweeps the ring once per
        # second" — a distinct overlay on top of the base outer-ring
        # rotation, not a replacement for it.
        self._sweep_phase = 0.0  # degrees

        # §5.3 speaking: "outer segments light up in sequence, radiating
        # outward" driven by "a 180ms synthetic pulse per word" (no TTS
        # envelope is wired into the UI, so this is the spec's own
        # fallback rather than a real audio-driven signal).
        self._speak_pulse_ms = 180.0
        self._speak_elapsed = 0.0  # seconds since the last synthetic pulse
        self._speak_peak = 0.0     # degrees, current ripple origin

        self._mic_level = 0.0
        self._bands: list[float] | None = None
        self._data_value = 0.0
        self._learning_subtopic = ""
        self._hover_index: int | None = None
        self._flashing = False

        # Boot sequence hooks (ui/hud/boot.py): frozen holds the rings still
        # until the core-ignition beat, per ODIN-HUD.md §8 ("rings begin
        # rotating" only once the core ignites, not from frame one).
        # bootScale/bootFlash are separate from the state-driven properties
        # above so the boot animation never has to fight the idle breathing
        # or state-color logic — it's a pure multiply/blend on top.
        self._boot_frozen = False
        self._boot_reveal = 1.0
        self._boot_scale = 1.0
        self._boot_flash = 0.0

        self._data_anim = QPropertyAnimation(self, b"dataValue", self)
        self._data_anim.setDuration(tokens.DUR_VAL)
        self._data_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        self._error_timer = QTimer(self)
        self._error_timer.setSingleShot(True)
        self._error_timer.timeout.connect(self._clear_error_flash)

        # Perf: the tick ring is 120 hairlines recomputed from trig every
        # single frame at 30fps in the original implementation — by far the
        # most expensive thing this widget painted, since each is its own
        # native draw call. The geometry is fixed (only overall rotation
        # changes), so it's built exactly once here and just rotated with a
        # transform at paint time — 120 drawLine calls become one drawPath.
        self._tick_path = self._build_tick_path()

        # Perf: the bezel's per-segment angles are fixed, so their trig is
        # computed once (see _bezel_geometry) rather than 48 times a frame.
        self._bezel_trig: list[tuple[float, float]] = []

    # -- state ---------------------------------------------------------

    @property
    def state(self) -> str:
        return self._state

    @state.setter
    def state(self, value: str) -> None:
        value = value if value in STATES else "idle"
        if value == self._state:
            return
        self._state = value
        # Start each entry into these states from a clean beat/sweep origin
        # rather than resuming wherever the phase happened to be left last
        # time, so the first frame of the new state never looks mid-cycle.
        if value == "speaking":
            self._speak_elapsed = 0.0
            self._speak_peak = 0.0
        elif value == "listening":
            self._sweep_phase = 0.0
        self.status_changed.emit(self._status_text())
        self.update()

    def _status_text(self) -> str:
        if self._state == "learning":
            return f"LEARNING: {self._learning_subtopic}" if self._learning_subtopic else "LEARNING"
        return _STATUS_LABELS.get(self._state, self._state.upper())

    def set_mic_level(self, level: float) -> None:
        """0..1 smoothed RMS amplitude, ~20Hz while state == 'listening'."""
        self._mic_level = max(0.0, min(1.0, level))
        self.update()

    def set_bands(self, values) -> None:
        """Per-band audio levels (0..1) for the bezel — Spectrum.levels,
        handed over by the window's shared tick. No update() call: the same
        tick advances and repaints this widget anyway."""
        self._bands = list(values) if values else None

    def bezel_values(self) -> list[float]:
        """0..1 per bezel segment, whatever the audio situation.

        With bands, the ring is a meter. Without any audio source at all it
        falls back to the pre-bezel shimmer (and, while speaking, the §5.3
        word-beat chase) rather than sitting dead flat — the same rule
        ui/hud/spectrum.py follows for its own analyser.
        """
        n = self.BEZEL_SEGMENTS
        bands = self._bands
        if bands:
            count = len(bands)
            values = [
                max(0.0, min(1.0, bands[min(count - 1, i * count // n)]))
                for i in range(n)
            ]
        elif self._state == "speaking":
            values = [self._speak_wave(i * 360.0 / n) for i in range(n)]
        else:
            values = [
                0.5 + 0.5 * math.sin(math.radians(i * 360.0 / n) * 3 + self._breathe_phase)
                for i in range(n)
            ]

        if self._state == "listening" and self._mic_level > 0.0:
            floor = self._mic_level * BEZEL_MIC_GAIN
            values = [
                max(v, floor * (0.55 + 0.45 * math.sin(math.radians(i * 360.0 / n) * 4 + self._breathe_phase)))
                for i, v in enumerate(values)
            ]
        return [max(BEZEL_FLOOR, v) for v in values]

    def set_system_load(self, fraction: float) -> None:
        """0.5*cpu + 0.3*ram + 0.2*disk_io, from the latest TelemetryFrame —
        the data ring's normal (non-learning, non-thinking) source (§5.3)."""
        self._animate_data_to(fraction)

    def set_learning_progress(self, subtopic: str, fraction: float) -> None:
        changed = subtopic != self._learning_subtopic
        self._learning_subtopic = subtopic
        self._animate_data_to(fraction)
        if changed and self._state == "learning":
            self.status_changed.emit(self._status_text())

    def flash_error(self) -> None:
        """A transient flash, not a persistent mode (§5.3: 'flashes crit
        twice, 200ms; rings freeze for 600ms') — triggered on a failed tool
        call, layered on top of whatever `state` already is."""
        self._flashing = True
        self._error_timer.start(600)
        self.update()

    def _clear_error_flash(self) -> None:
        self._flashing = False
        self.update()

    def _animate_data_to(self, fraction: float) -> None:
        fraction = max(0.0, min(1.0, fraction))
        self._data_anim.stop()
        self._data_anim.setStartValue(self._data_value)
        self._data_anim.setEndValue(fraction)
        self._data_anim.start()

    def getDataValue(self) -> float:
        return self._data_value

    def setDataValue(self, value: float) -> None:
        self._data_value = value
        self.update()

    dataValue = pyqtProperty(float, getDataValue, setDataValue)

    @property
    def boot_frozen(self) -> bool:
        return self._boot_frozen

    @boot_frozen.setter
    def boot_frozen(self, value: bool) -> None:
        value = bool(value)
        if self._boot_frozen and not value:
            # Core ignition (ui/hud/boot.py): the molecule blooms outward with
            # the flash rather than just quietly starting to drift.
            self.field.pulse(IGNITION_PULSE)
        self._boot_frozen = value

    def ring_reveals(self, reveal: float) -> dict[str, float]:
        """0..1 per ring for a given point in the entry animation."""
        return {
            name: _smoothstep(lo, hi, reveal)
            for name, (lo, hi) in RING_REVEAL_WINDOWS.items()
        }

    def getBootReveal(self) -> float:
        return self._boot_reveal

    def setBootReveal(self, value: float) -> None:
        self._boot_reveal = max(0.0, min(1.0, value))
        self.field.set_assemble(_smoothstep(*RING_REVEAL_WINDOWS["molecule"], self._boot_reveal))
        self.update()

    bootReveal = pyqtProperty(float, getBootReveal, setBootReveal)

    def getBootScale(self) -> float:
        return self._boot_scale

    def setBootScale(self, value: float) -> None:
        self._boot_scale = value
        self.update()

    bootScale = pyqtProperty(float, getBootScale, setBootScale)

    def getBootFlash(self) -> float:
        return self._boot_flash

    def setBootFlash(self, value: float) -> None:
        self._boot_flash = value
        self.update()

    bootFlash = pyqtProperty(float, getBootFlash, setBootFlash)

    # -- driven by the shared animation loop (ODIN-HUD.md §10) --------------

    def advance(self, dt: float) -> None:
        # Held still by the boot sequence until the core-ignition beat
        # (ODIN-HUD.md §8: "rings begin rotating" there, not from frame
        # one) — the breathing/flash phase still needs to advance so the
        # ignition flash itself can animate smoothly.
        if not self.boot_frozen:
            if not self._flashing:
                self._phase = (self._phase + dt * self._ring_speed()) % 360.0
                self._tick_phase = (self._tick_phase - dt * 4.0) % 360.0
                if self._state == "listening":
                    self._sweep_phase = (self._sweep_phase + dt * 360.0) % 360.0
                elif self._state == "speaking":
                    self._advance_speak_pulse(dt)
                self.field.set_energy(self._field_energy())
                self.field.advance(dt)
        self._breathe_phase += dt
        self.update()

    def _field_energy(self) -> float:
        if self._state == "listening":
            return FIELD_LISTENING_BASE + FIELD_LISTENING_GAIN * self._mic_level
        return FIELD_ENERGY.get(self._state, 0.2)

    def _ring_speed(self) -> float:
        if self._state == "listening":
            return 360.0 / 24.0  # §5.3: speeds to a 24s revolution
        return 360.0 / 60.0      # §5.3: base 60s revolution

    def _advance_speak_pulse(self, dt: float) -> None:
        pulse_s = self._speak_pulse_ms / 1000.0
        self._speak_elapsed += dt
        if self._speak_elapsed >= pulse_s:
            self._speak_elapsed %= pulse_s
            self._speak_peak = (self._speak_peak + 45.0) % 360.0  # one launcher-segment step per word beat
            self.field.pulse(SPEAK_BEAT_PULSE)  # the molecule takes the same beat

    def _speak_wave(self, seg_angle: float) -> float:
        """0..1 brightness for one outer-ring segment under the speaking
        chase: a hot spot ignites at `_speak_peak` and, over the course of
        the beat, hands off to a ring of brightness expanding away from it
        — "light up, then radiate outward" rather than a static glow."""
        pulse_s = self._speak_pulse_ms / 1000.0
        t = min(1.0, self._speak_elapsed / pulse_s)
        delta = self._angular_delta(seg_angle, self._speak_peak)
        ripple = math.exp(-((delta - (10.0 + t * 55.0)) / 16.0) ** 2)
        core = math.exp(-(delta / 12.0) ** 2) * (1.0 - t)
        return max(ripple, core)

    @staticmethod
    def _angular_delta(a: float, b: float) -> float:
        return abs((a - b + 180.0) % 360.0 - 180.0)

    # -- launcher ring hit-testing --------------------------------------

    def _segment_at(self, x: float, y: float) -> int | None:
        side = min(self.width(), self.height())
        if side <= 0:
            return None
        scale = side / self.REF
        cx = (self.width() - side) / 2 + self.CENTER * scale
        cy = (self.height() - side) / 2 + self.CENTER * scale
        dx, dy = (x - cx) / scale, (y - cy) / scale
        radius = math.hypot(dx, dy)
        if not (self.R_LAUNCHER - 22 <= radius <= self.R_LAUNCHER + 22):
            return None
        angle_deg = math.degrees(math.atan2(-dy, dx)) % 360
        return round((90 - angle_deg) / 45) % 8

    def mouseMoveEvent(self, event) -> None:
        pos = event.position()
        index = self._segment_at(pos.x(), pos.y())
        if index != self._hover_index:
            self._hover_index = index
            self.setCursor(
                Qt.CursorShape.PointingHandCursor if index is not None else Qt.CursorShape.ArrowCursor
            )
            self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:
        if self._hover_index is not None:
            self._hover_index = None
            self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            pos = event.position()
            index = self._segment_at(pos.x(), pos.y())
            if index is not None:
                self.launcher_clicked.emit(self.LAUNCHER_LABELS[index])
        super().mousePressEvent(event)

    # -- core look -------------------------------------------------------

    def _core_radius(self) -> float:
        if self._state == "listening":
            return self.R_CORE + self._mic_level * 12  # §5.3's amp swell, to scale
        if self._flashing:
            return self.R_CORE
        breathe = 0.5 + 0.5 * math.sin(self._breathe_phase * (2 * math.pi / 4.0))
        if self._state == "idle":
            return self.R_CORE * (1.0 + 0.04 * breathe)
        if self._state == "speaking":
            return self.R_CORE * (1.0 + 0.10 * breathe)
        return self.R_CORE

    def _core_color(self) -> QColor:
        if self._flashing:
            blink = abs(math.sin(self._breathe_phase * 2 * math.pi * 5))
            return QColor(tokens.CRIT) if blink > 0.4 else QColor(tokens.CY_300)
        return tokens.orb_accent(self._state)

    # -- painting ----------------------------------------------------------

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        side = min(self.width(), self.height())
        if side <= 0:
            painter.end()
            return

        scale = side / self.REF
        painter.save()
        painter.translate((self.width() - side) / 2, (self.height() - side) / 2)
        painter.scale(scale, scale)
        if self._boot_scale != 1.0:
            # Boot sequence only (ui/hud/boot.py): rings scale in from 0.85
            # to 1.0 around the orb's own center, per ODIN-HUD.md §8.
            painter.translate(self.CENTER, self.CENTER)
            painter.scale(self._boot_scale, self._boot_scale)
            painter.translate(-self.CENTER, -self.CENTER)

        accent = self._core_color()
        ring_accent = tokens.THINKING if self._state == "thinking" else tokens.CY_300

        if self._boot_reveal >= 1.0:
            self._paint_halo(painter, accent)
            self._paint_dash_ring(painter, ring_accent)
            self._paint_bezel(painter, ring_accent)
            self._paint_listening_sweep(painter)
            self._paint_launcher_ring(painter)
            self._paint_tick_ring(painter)
            self._paint_data_ring(painter, accent)
            self._paint_core(painter, accent)
            self.field.paint(painter, QPointF(self.CENTER, self.CENTER), self.R_FIELD, accent)
        else:
            self._paint_assembling(painter, accent, ring_accent)

        painter.restore()
        painter.end()

    def _paint_assembling(self, painter: QPainter, accent: QColor, ring_accent: QColor) -> None:
        """Entry animation only (bootReveal < 1). Each ring is drawn inside a
        pie wedge that grows from the top clockwise, so it reads as being
        traced into place; the core and halo come up with the molecule."""
        reveals = self.ring_reveals(self._boot_reveal)

        def stage(fraction: float, draw) -> None:
            if fraction <= 0.0:
                return
            if fraction >= 1.0:
                draw()
                return
            painter.save()
            painter.setClipPath(self._reveal_wedge(fraction))
            painter.setOpacity(0.35 + 0.65 * fraction)
            draw()
            painter.restore()

        stage(reveals["dash"], lambda: self._paint_dash_ring(painter, ring_accent))
        stage(reveals["bezel"], lambda: self._paint_bezel(painter, ring_accent))
        stage(reveals["launcher"], lambda: self._paint_launcher_ring(painter))
        stage(reveals["tick"], lambda: self._paint_tick_ring(painter))
        stage(reveals["data"], lambda: self._paint_data_ring(painter, accent))

        molecule = reveals["molecule"]
        if molecule > 0.0:
            painter.save()
            painter.setOpacity(molecule)
            self._paint_halo(painter, accent)
            self._paint_core(painter, accent)
            painter.restore()
        # The field fades itself in through set_assemble, so it needs no
        # opacity of its own — its motes arrive individually.
        self.field.paint(painter, QPointF(self.CENTER, self.CENTER), self.R_FIELD, accent)

    def _reveal_wedge(self, fraction: float) -> QPainterPath:
        span = 360.0 * max(0.0, min(1.0, fraction))
        rect = QRectF(0.0, 0.0, self.REF, self.REF)
        path = QPainterPath()
        path.moveTo(self.CENTER, self.CENTER)
        path.arcTo(rect, 90.0, -span)
        path.closeSubpath()
        return path

    def _paint_halo(self, painter: QPainter, accent: QColor) -> None:
        r = self._core_radius() * 2.6
        glow = QRadialGradient(QPointF(self.CENTER, self.CENTER), r)
        near = QColor(accent)
        # Dimmer than the pre-molecule orb's halo: the glow used to be the
        # centerpiece, and at its old strength it washed the particle field out.
        near.setAlpha(44)
        far = QColor(accent)
        far.setAlpha(0)
        glow.setColorAt(0.0, near)
        glow.setColorAt(1.0, far)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(glow)
        painter.drawEllipse(QPointF(self.CENTER, self.CENTER), r, r)

    def _bezel_geometry(self) -> list[tuple[float, float]]:
        """(cos, sin) per bezel segment, computed once. Trig for 48 segments
        every frame at 60fps is pure waste — the angles never change."""
        if not self._bezel_trig:
            n = self.BEZEL_SEGMENTS
            self._bezel_trig = [
                (math.cos(math.radians(90 - i * 360.0 / n)), math.sin(math.radians(90 - i * 360.0 / n)))
                for i in range(n)
            ]
        return self._bezel_trig

    def _paint_bezel(self, painter: QPainter, accent: QColor) -> None:
        """The outer ring as a meter: one radial bar per audio band, growing
        outward from a fixed baseline. Anchored rather than rotating — a
        turning scale can't be read — with the rotation moved out to the
        dashed hairline beyond it."""
        center = QPointF(self.CENTER, self.CENTER)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(tokens.CY_700, 1.4))
        painter.drawEllipse(center, self.R_BEZEL, self.R_BEZEL)

        values = self.bezel_values()
        tiers = self.BEZEL_TIERS
        paths: list[QPainterPath | None] = [None] * tiers
        for (cos_a, sin_a), value in zip(self._bezel_geometry(), values):
            tier = min(tiers - 1, int(value * tiers))
            path = paths[tier]
            if path is None:
                path = paths[tier] = QPainterPath()
            outer = self.R_BEZEL + value * self.BEZEL_EXTENT
            path.moveTo(self.CENTER + self.R_BEZEL * cos_a, self.CENTER - self.R_BEZEL * sin_a)
            path.lineTo(self.CENTER + outer * cos_a, self.CENTER - outer * sin_a)

        pen = QPen(QColor(accent), 3.4)
        pen.setCapStyle(Qt.PenCapStyle.FlatCap)
        for tier, path in enumerate(paths):
            if path is None:
                continue
            color = QColor(accent)
            color.setAlphaF(0.30 + 0.70 * (tier + 0.5) / tiers)
            pen.setColor(color)
            painter.setPen(pen)
            painter.drawPath(path)

    def _paint_dash_ring(self, painter: QPainter, accent: QColor) -> None:
        """The one element that still turns at §5.3's 60s revolution, kept
        outside the bezel so the meter itself can stay still."""
        pen = QPen(QColor(accent.red(), accent.green(), accent.blue(), 130), 2.0)
        pen.setCapStyle(Qt.PenCapStyle.FlatCap)
        pen.setDashPattern([5.0, 3.5])
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(pen)
        painter.save()
        painter.translate(self.CENTER, self.CENTER)
        painter.rotate(-self._phase)
        painter.drawEllipse(QPointF(0, 0), self.R_DASH, self.R_DASH)
        painter.restore()

    def _paint_listening_sweep(self, painter: QPainter) -> None:
        """§5.3: "a second bright arc sweeps the ring once per second" — an
        overlay on top of the base 32-segment ring, only while listening."""
        if self._state != "listening":
            return
        span = 28.0
        rect = QRectF(self.CENTER - self.R_OUTER, self.CENTER - self.R_OUTER, self.R_OUTER * 2, self.R_OUTER * 2)

        def stroke(pen: QPen) -> None:
            painter.setPen(pen)
            painter.drawArc(rect, int(self._sweep_phase * 16), int(-span * 16))

        tokens.draw_glow(painter, stroke, tokens.CY_100, 3.0, passes=2)

    def _paint_launcher_ring(self, painter: QPainter) -> None:
        rect = QRectF(self.CENTER - self.R_LAUNCHER, self.CENTER - self.R_LAUNCHER, self.R_LAUNCHER * 2, self.R_LAUNCHER * 2)
        gap_deg = 6.0
        seg_span = 45.0 - gap_deg
        painter.setFont(tokens.font_label(tokens.T_LABEL, bold=True))
        for i, label in enumerate(self.LAUNCHER_LABELS):
            center_angle = 90 - i * 45
            start = center_angle + seg_span / 2
            hovered = i == self._hover_index

            if hovered:
                painter.setPen(Qt.PenStyle.NoPen)
                fill = QColor(tokens.CY_300)
                fill.setAlphaF(0.14)
                painter.setBrush(fill)
                painter.drawPie(rect, int(start * 16), int(-seg_span * 16))
                painter.setBrush(Qt.BrushStyle.NoBrush)

            pen = QPen(tokens.CY_100 if hovered else tokens.CY_400, 2.5)
            painter.setPen(pen)
            painter.drawArc(rect, int(start * 16), int(-seg_span * 16))

            ang = math.radians(center_angle)
            tx = self.CENTER + self.R_LAUNCHER * math.cos(ang)
            ty = self.CENTER - self.R_LAUNCHER * math.sin(ang)
            painter.setPen(tokens.CY_100 if hovered else tokens.CY_200)
            painter.drawText(QRectF(tx - 30, ty - 8, 60, 16), Qt.AlignmentFlag.AlignCenter, label)

    def _build_tick_path(self) -> QPainterPath:
        """The 120 tick marks' geometry is fixed — only their overall
        rotation changes frame to frame — so it's built once at a baseline
        orientation (tick_phase == 0) and just rotated at paint time
        instead of recomputing 120 trig-derived line segments every frame."""
        n = 120
        path = QPainterPath()
        for i in range(n):
            ang = math.radians(i * 360.0 / n)
            x0 = self.CENTER + (self.R_TICK - 6) * math.cos(ang)
            y0 = self.CENTER - (self.R_TICK - 6) * math.sin(ang)
            x1 = self.CENTER + self.R_TICK * math.cos(ang)
            y1 = self.CENTER - self.R_TICK * math.sin(ang)
            path.moveTo(x0, y0)
            path.lineTo(x1, y1)
        return path

    def _paint_tick_ring(self, painter: QPainter) -> None:
        color = QColor(tokens.CY_400)
        color.setAlphaF(0.55)
        painter.setPen(QPen(color, 1.2))
        painter.save()
        painter.translate(self.CENTER, self.CENTER)
        painter.rotate(-self._tick_phase)
        painter.translate(-self.CENTER, -self.CENTER)
        painter.drawPath(self._tick_path)
        painter.restore()

    def _paint_data_ring(self, painter: QPainter, accent: QColor) -> None:
        rect = QRectF(self.CENTER - self.R_DATA, self.CENTER - self.R_DATA, self.R_DATA * 2, self.R_DATA * 2)
        painter.setPen(QPen(tokens.CY_700, 3))
        painter.drawEllipse(rect)

        if self._state == "thinking":
            start = (self._phase * 3) % 360  # indeterminate, spins faster than the outer ring
            pen = QPen(accent, 3)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.drawArc(rect, int(start * 16), int(-90 * 16))  # 25% of the circle
            return

        def stroke(pen) -> None:
            painter.setPen(pen)
            painter.drawArc(rect, 90 * 16, int(-360 * self._data_value * 16))

        if self._data_value > 0.002:
            tokens.draw_glow(painter, stroke, accent, 3, passes=2)

    def _paint_core(self, painter: QPainter, accent: QColor) -> None:
        r = self._core_radius()
        if self._boot_flash > 0.0:
            # Boot sequence only (ui/hud/boot.py): core ignition — a flash
            # to white settling back to the idle gradient, per
            # ODIN-HUD.md §8.
            accent = _lerp_color(accent, QColor(255, 255, 255), self._boot_flash)
            r *= 1.0 + 0.5 * self._boot_flash
        gradient = QRadialGradient(QPointF(self.CENTER, self.CENTER), r)
        gradient.setColorAt(0.0, QColor(0xDF, 0xFB, 0xFF))
        gradient.setColorAt(0.55, accent)
        transparent = QColor(accent)
        transparent.setAlpha(0)
        gradient.setColorAt(1.0, transparent)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(gradient)
        painter.drawEllipse(QPointF(self.CENTER, self.CENTER), r, r)
