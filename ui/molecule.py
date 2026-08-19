"""MoleculeField — the 3D particle-and-bond field that floats inside both orbs.

Deliberately knows nothing about color, state names, or which widget is
painting it: it owns positions, motion and bonds in a normalized unit sphere,
and the caller supplies the center, radius and accent at paint time. That's
what lets the HUD's VoiceOrb (ui/hud/voice_orb.py, strictly palette-bound via
ui/hud/tokens.py) and the small desktop ReactorOrb (ui/orb.py, its own older
palette) share one implementation without sharing a palette.

The motion is a damped random walk in a harmonic trap — each particle gets its
own gaussian kick every frame, is pulled gently back toward the nucleus, and is
clamped inside the sphere. No orbits, no fixed paths: the drift is genuinely
random and never repeats, which is what makes it read as floating rather than
as an animation on a loop.

Positions always fill the unit ball; `energy` scales the cloud at paint time
rather than moving the particles, so a state change resizes the molecule
immediately instead of waiting for diffusion to refill a larger shell.
"""
from __future__ import annotations

import numpy as np
from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPen, QPixmap, QRadialGradient

# Motion constants, all in normalized units (the sphere has radius 1) and per
# second. Tuned for a cloud that drifts visibly but never looks blown about.
CENTER_PULL = 0.03    # harmonic trap toward the nucleus — barely there, so the
                      # cloud fills the sphere instead of balling up at its center
                      # (a Gaussian clump reads as a blob; near-uniform reads as
                      # a molecule the ring actually contains)
DAMPING = 1.4         # velocity decay; with CENTER_PULL sets the drift's scale
NOISE = 0.21          # gaussian kick per sqrt(second), at energy 0 (~33px/s
                      # of drift in a 110px orb — a lazy float, not a jitter)
NOISE_ENERGY = 0.34   # extra kick at energy 1
SOFT_WALL = 0.90      # radius past which the sphere pushes back
WALL_PUSH = 9.0

SCALE_MIN = 0.90      # cloud size at energy 0 (tight), as a fraction of radius
SCALE_MAX = 1.0       # at energy 1 (open)
SCALE_EASE = 4.0      # per second, exponential approach to the target scale
PULSE_GAIN = 0.16     # how far one full-strength pulse swells the cloud
PULSE_DECAY = 6.0     # per second

YAW_SPEED = 0.16      # radians/s at energy 0 — one revolution per ~40s
YAW_ENERGY = 0.42     # extra radians/s at energy 1
PITCH_SPEED = 0.11
PITCH_TILT = 0.28     # radians of pitch wobble

MAX_DT = 0.1          # a stalled frame must not integrate a huge step

# Projection. FOCAL sets how strong the perspective is; positional
# perspective is normalized to its own maximum so the cloud can never project
# outside the sphere it lives in, however near the viewer a particle drifts.
FOCAL = 14.0          # gentle: strong perspective shrinks the cloud away
                      # from the ring that's meant to contain it
DOT_FRACTION = 0.034  # a particle's screen radius, as a fraction of the orb's
DOT_DEPTH = 0.55      # how much of that radius depth accounts for

ASSEMBLE_REACH = 0.85 # how far out a particle starts when assemble == 0, as a
                      # multiple of its settled radius. Kept small enough that
                      # even the outermost mote starts inside the caller's own
                      # bounds — a wider scatter clips against the widget rect
ASSEMBLE_WINDOW = 0.55  # fraction of the assemble ramp one particle's own
                        # arrival takes — the rest is its staggered head start

SPRITE_PX = 32        # source size of the cached particle sprite
SPRITE_CORE = 0.34    # fraction of it that's the white-hot center

# Alpha ranges. Bonds sit well under the particles so the links read as
# structure rather than as a cage drawn over the cloud.
DOT_ALPHA = (0.20, 1.0)
BOND_ALPHA = (0.10, 0.72)
MIN_ALPHA = 0.03      # below this a mote or link is invisible anyway, so it
                      # is dropped rather than drawn — which is also what keeps
                      # an unassembled cloud from leaving a ghost web behind
BOND_WIDTH = 0.014    # stroke width as a fraction of the orb's radius —
                      # under ~1px the antialiased line all but vanishes


class MoleculeField:
    """A cloud of `count` particles drifting freely inside a unit sphere."""

    TIERS = 8            # brightness buckets, and so draw calls, per layer
    BOND_CUTOFF = 0.22   # particles closer than this (of the unit sphere) bond

    def __init__(
        self,
        count: int = 240,
        seed: int = 11,
        bond_interval: int = 3,
        bond_cap: int | None = None,
    ):
        self._count = int(count)
        self._rng = np.random.default_rng(seed)

        # Uniform through the ball's volume (cube-root of the uniform draw),
        # not uniform in radius — the latter clumps everything at the center.
        direction = self._rng.normal(size=(self._count, 3))
        direction /= np.linalg.norm(direction, axis=1, keepdims=True)
        radius = np.cbrt(self._rng.random((self._count, 1))) * 0.92
        self._positions = direction * radius
        self._velocities = self._rng.normal(scale=0.05, size=(self._count, 3))

        self._energy = 0.35
        self._scale = self._target_scale()  # start settled, so there's no boot transient
        self._pulse = 0.0
        self._yaw = self._rng.random() * 2 * np.pi
        self._pitch_phase = self._rng.random() * 2 * np.pi

        # Bonds are the one O(n^2) thing here, and two frames apart the
        # pairing is all but identical — so it's recomputed every
        # `bond_interval` frames and the lines are drawn from live positions
        # in between, which is what makes them look continuous.
        self._bond_interval = max(1, int(bond_interval))
        self._bond_cap = int(bond_cap) if bond_cap is not None else int(self._count * 1.6)
        self._pair_i, self._pair_j = np.triu_indices(self._count, k=1)
        self._bond_countdown = self._bond_interval
        self._bonds = self._compute_bonds()

        self._sprite: QPixmap | None = None
        self._sprite_key: int | None = None

        # Assembly (the entry animation's finale, ui/hud/boot.py): 0 scatters
        # the cloud far outside the orb and 1 is its settled state. Each
        # particle gets its own head start, so the molecule condenses mote by
        # mote instead of shrinking as one rigid object.
        self._assemble = 1.0
        self._stagger = self._rng.random(self._count)

    # -- inspection --------------------------------------------------------

    @property
    def count(self) -> int:
        return self._count

    @property
    def positions(self) -> np.ndarray:
        return self._positions

    @property
    def velocities(self) -> np.ndarray:
        return self._velocities

    @property
    def energy(self) -> float:
        return self._energy

    @property
    def yaw(self) -> float:
        return self._yaw

    @property
    def radius_scale(self) -> float:
        """Current cloud size as a fraction of the caller's radius, pulse included."""
        return self._scale + self._pulse * PULSE_GAIN

    @property
    def target_radius_scale(self) -> float:
        return self._target_scale()

    @property
    def assemble(self) -> float:
        return self._assemble

    def set_assemble(self, fraction: float) -> None:
        """0 = scattered and invisible, 1 = settled. Drives the entry
        animation only; steady-state rendering leaves it at 1."""
        self._assemble = max(0.0, min(1.0, float(fraction)))

    def _arrival(self) -> np.ndarray | None:
        """Per-particle 0..1 arrival progress, or None once everything has
        landed — the common case, which then costs nothing."""
        if self._assemble >= 1.0:
            return None
        head_start = self._stagger * (1.0 - ASSEMBLE_WINDOW)
        return np.clip((self._assemble - head_start) / ASSEMBLE_WINDOW, 0.0, 1.0)

    @property
    def pulse_level(self) -> float:
        """How much undecayed kick is left from the last pulse() — what a
        caller watches to know a beat actually landed."""
        return self._pulse

    # -- control -----------------------------------------------------------

    def set_energy(self, value: float) -> None:
        """0..1: how agitated and how open the cloud is. One knob per state."""
        self._energy = max(0.0, min(1.0, float(value)))

    def pulse(self, strength: float = 1.0) -> None:
        """A kick outward that decays on its own — one speech beat, one flash."""
        self._pulse = min(1.5, self._pulse + max(0.0, float(strength)))

    # -- motion ------------------------------------------------------------

    def _target_scale(self) -> float:
        return SCALE_MIN + (SCALE_MAX - SCALE_MIN) * self._energy

    def advance(self, dt: float) -> None:
        dt = max(0.0, min(MAX_DT, float(dt)))
        if dt == 0.0:
            return

        self._scale += (self._target_scale() - self._scale) * min(1.0, SCALE_EASE * dt)
        self._pulse *= float(np.exp(-PULSE_DECAY * dt))

        pos, vel = self._positions, self._velocities
        radius = np.linalg.norm(pos, axis=1, keepdims=True)

        accel = -CENTER_PULL * pos - DAMPING * vel
        # Soft wall: only the particles that have wandered out past SOFT_WALL
        # feel it, so the rest drift as if the boundary weren't there.
        outside = radius > SOFT_WALL
        if outside.any():
            accel -= np.where(outside, WALL_PUSH * (radius - SOFT_WALL) * pos / np.maximum(radius, 1e-9), 0.0)

        noise = NOISE + NOISE_ENERGY * self._energy
        vel += accel * dt + self._rng.normal(scale=noise * np.sqrt(dt), size=pos.shape)
        pos += vel * dt

        # Hard backstop: whatever the tuning, nothing ever leaves the sphere.
        radius = np.linalg.norm(pos, axis=1, keepdims=True)
        escaped = (radius > 1.0).ravel()
        if escaped.any():
            pos[escaped] /= radius[escaped]
            # Drop the outward component so they slide along the wall rather
            # than pressing into it until the noise happens to turn them.
            normal = pos[escaped]
            outward = np.sum(vel[escaped] * normal, axis=1, keepdims=True)
            vel[escaped] -= normal * np.maximum(outward, 0.0)

        spin = YAW_SPEED + YAW_ENERGY * self._energy
        self._yaw = (self._yaw + spin * dt) % (2 * np.pi)
        self._pitch_phase = (self._pitch_phase + PITCH_SPEED * dt) % (2 * np.pi)

        self._bond_countdown -= 1
        if self._bond_countdown <= 0:
            self._bonds = self._compute_bonds()
            self._bond_countdown = self._bond_interval

    # -- bonds -------------------------------------------------------------

    @property
    def bond_cap(self) -> int:
        return self._bond_cap

    @property
    def bonds(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """(i, j, weight) for every currently linked pair. Weight is 1 for
        touching particles falling to 0 at BOND_CUTOFF, so links dissolve as
        their particles drift apart instead of blinking out."""
        return self._bonds

    def _compute_bonds(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        pos = self._positions
        square = np.einsum("ij,ij->i", pos, pos)
        # |a-b|^2 expanded, so this is one matmul rather than an (n, n, 3)
        # difference tensor — a third of the memory traffic at 240 particles.
        d2 = square[:, None] + square[None, :] - 2.0 * (pos @ pos.T)
        pair_d2 = np.maximum(d2[self._pair_i, self._pair_j], 0.0)

        within = np.flatnonzero(pair_d2 <= self.BOND_CUTOFF**2)
        if len(within) > self._bond_cap:
            # Keep the tightest pairs: a momentarily dense frame must not turn
            # into thousands of stroked lines.
            keep = np.argpartition(pair_d2[within], self._bond_cap)[: self._bond_cap]
            within = within[keep]

        distance = np.sqrt(pair_d2[within])
        weight = np.clip(1.0 - distance / self.BOND_CUTOFF, 0.0, 1.0)
        return self._pair_i[within], self._pair_j[within], weight

    # -- projection --------------------------------------------------------

    def project(self, radius: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Rotate the cloud, then flatten it to the caller's pixel space.

        Returns (xy, size, depth): offsets from the orb's center in pixels,
        each particle's screen radius, and 0 (farthest) .. 1 (nearest). The
        drawn extent — |xy| + size — never exceeds `radius`, so the molecule
        always stays inside the ring the caller drew around it.
        """
        pos = self._positions
        cy, sy = np.cos(self._yaw), np.sin(self._yaw)
        pitch = PITCH_TILT * np.sin(self._pitch_phase)
        cp, sp = np.cos(pitch), np.sin(pitch)

        x = pos[:, 0] * cy + pos[:, 2] * sy
        z1 = pos[:, 2] * cy - pos[:, 0] * sy
        y = pos[:, 1] * cp - z1 * sp
        z = pos[:, 1] * sp + z1 * cp

        depth = np.clip((z + 1.0) * 0.5, 0.0, 1.0)
        persp = (FOCAL / (FOCAL - z)) / (FOCAL / (FOCAL - 1.0))

        max_size = radius * DOT_FRACTION
        span = min(radius * self.radius_scale, radius - max_size)
        xy = np.column_stack((x * persp, -y * persp)) * span
        size = max_size * (1.0 - DOT_DEPTH + DOT_DEPTH * depth)

        arrival = self._arrival()
        if arrival is not None:
            # Straight out along each particle's own bearing, so they fly in
            # from every direction rather than converging from one plane.
            xy = xy * (1.0 + (1.0 - arrival) * ASSEMBLE_REACH)[:, None]
            size = size * (0.35 + 0.65 * arrival)
        return xy, size, depth

    # -- painting ----------------------------------------------------------

    def _tiers(self, alpha: np.ndarray) -> np.ndarray:
        return np.clip((alpha * self.TIERS).astype(np.int32), 0, self.TIERS - 1)

    def _tier_color(self, accent: QColor, tier: int, alpha: float) -> QColor:
        # Bonds nearer the viewer run hotter. Lightening the accent rather
        # than mixing toward white keeps this palette-agnostic: the field
        # never introduces a color the caller didn't hand it.
        fraction = tier / max(1, self.TIERS - 1)
        color = accent.lighter(100 + int(30 * fraction))
        color.setAlphaF(max(0.0, min(1.0, alpha)))
        return color

    def _dot_sprite(self, accent: QColor) -> QPixmap:
        """One soft dot, rendered once per accent and blitted per particle.

        A sprite rather than a path per particle: filling a few hundred
        antialiased ellipses costs roughly ten times as much per frame as one
        batched `drawPixmapFragments`, and the gradient gives each mote a
        white-hot center and a soft edge that a flat fill can't.
        """
        key = accent.rgb()
        if self._sprite is None or self._sprite_key != key:
            sprite = QPixmap(SPRITE_PX, SPRITE_PX)
            sprite.fill(Qt.GlobalColor.transparent)
            painter = QPainter(sprite)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            half = SPRITE_PX / 2
            gradient = QRadialGradient(QPointF(half, half), half)
            edge = QColor(accent)
            edge.setAlpha(0)
            gradient.setColorAt(0.0, accent.lighter(190))
            gradient.setColorAt(SPRITE_CORE, accent)
            gradient.setColorAt(1.0, edge)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(gradient)
            painter.drawEllipse(QRectF(0, 0, SPRITE_PX, SPRITE_PX))
            painter.end()
            self._sprite = sprite
            self._sprite_key = key
        return self._sprite

    def particle_fragments(
        self, center: QPointF, radius: float, opacity: float = 1.0
    ) -> list:
        """One QPainter.PixmapFragment per particle — position, scale and
        opacity only, so the whole cloud goes out in a single draw call."""
        xy, size, depth = self.project(radius)
        low, high = DOT_ALPHA
        alpha = (low + (high - low) * depth**1.3) * opacity
        arrival = self._arrival()
        if arrival is not None:
            alpha = alpha * arrival**1.5
        source = QRectF(0, 0, SPRITE_PX, SPRITE_PX)
        create = QPainter.PixmapFragment.create
        cx, cy = center.x(), center.y()

        fragments = []
        for index in np.flatnonzero(alpha >= MIN_ALPHA):
            scale = float(size[index]) * 2.0 / SPRITE_PX
            fragments.append(
                create(
                    QPointF(cx + xy[index, 0], cy + xy[index, 1]),
                    source, scale, scale, 0.0, float(alpha[index]),
                )
            )
        return fragments

    def bond_paths(
        self, center: QPointF, radius: float, accent: QColor, opacity: float = 1.0
    ) -> list[tuple[QColor, QPainterPath]]:
        """Same batching for the links, keyed on the pair's weight and depth
        so a bond fades both as it stretches and as it turns away."""
        i, j, weight = self._bonds
        xy, _, depth = self.project(radius)
        low, high = BOND_ALPHA
        strength = weight * (0.35 + 0.65 * 0.5 * (depth[i] + depth[j]))
        alpha = (low + (high - low) * strength) * opacity
        arrival = self._arrival()
        if arrival is not None:
            # Bonds only form once both ends have settled — links snapping
            # into place is the last thing that happens, and the clearest
            # signal that the molecule is complete. This scales the whole
            # alpha, floor included: scaling only `strength` left every link
            # sitting at the floor value, a ghost web across an empty orb.
            alpha = alpha * np.minimum(arrival[i], arrival[j]) ** 2

        visible = np.flatnonzero(alpha >= MIN_ALPHA)
        if len(visible) == 0:
            return []
        i, j, alpha = i[visible], j[visible], alpha[visible]
        tiers = np.clip((alpha / high * self.TIERS).astype(np.int32), 0, self.TIERS - 1)

        paths: list[QPainterPath | None] = [None] * self.TIERS
        cx, cy = center.x(), center.y()
        for index in range(len(i)):
            tier = int(tiers[index])
            path = paths[tier]
            if path is None:
                path = paths[tier] = QPainterPath()
            a, b = int(i[index]), int(j[index])
            path.moveTo(cx + xy[a, 0], cy + xy[a, 1])
            path.lineTo(cx + xy[b, 0], cy + xy[b, 1])

        return [
            (self._tier_color(accent, tier, high * (tier + 0.5) / self.TIERS), path)
            for tier, path in enumerate(paths)
            if path is not None
        ]

    def paint(
        self,
        painter: QPainter,
        center: QPointF,
        radius: float,
        accent: QColor,
        opacity: float = 1.0,
    ) -> None:
        """Bonds behind, particles in front, each layer back-to-front."""
        if opacity <= 0.0 or radius <= 0.0:
            return
        painter.save()
        pen = QPen()
        pen.setWidthF(max(0.7, radius * BOND_WIDTH))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for color, path in self.bond_paths(center, radius, accent, opacity):
            pen.setColor(color)
            painter.setPen(pen)
            painter.drawPath(path)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawPixmapFragments(
            self.particle_fragments(center, radius, opacity), self._dot_sprite(accent)
        )
        painter.restore()
