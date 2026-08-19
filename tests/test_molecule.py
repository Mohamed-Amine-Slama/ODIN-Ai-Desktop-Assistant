"""ui/molecule.py — the shared 3D particle/bond field both orbs paint with.

Physics first: free, per-particle random drift that never escapes the sphere
and never goes non-finite, plus the two knobs the orbs steer it with (energy
and pulse).
"""
import numpy as np

from ui.molecule import MoleculeField


def test_particles_start_inside_the_unit_sphere():
    field = MoleculeField(count=200, seed=3)
    assert field.count == 200
    assert np.max(np.linalg.norm(field.positions, axis=1)) <= 1.0


def test_advance_drifts_each_particle_on_its_own_path():
    """Free floatation, not a rigid cloud sliding around: every particle moves,
    and they don't all move by the same displacement."""
    field = MoleculeField(count=64, seed=3)
    before = field.positions.copy()
    for _ in range(5):
        field.advance(1 / 30)

    displacement = field.positions - before
    assert np.all(np.linalg.norm(displacement, axis=1) > 0)
    assert np.std(displacement, axis=0).min() > 0


def test_particles_never_escape_the_sphere():
    field = MoleculeField(count=128, seed=5)
    field.set_energy(1.0)
    for _ in range(2000):
        field.advance(1 / 30)
    assert np.max(np.linalg.norm(field.positions, axis=1)) <= 1.0 + 1e-9


def test_long_run_stays_finite():
    """A 30fps HUD runs for hours; a drifting NaN would blank the orb."""
    field = MoleculeField(count=64, seed=7)
    for _ in range(5000):
        field.advance(1 / 30)
    assert np.all(np.isfinite(field.positions))
    assert np.all(np.isfinite(field.velocities))


def test_same_seed_gives_the_same_motion():
    a, b = MoleculeField(count=32, seed=9), MoleculeField(count=32, seed=9)
    for _ in range(20):
        a.advance(1 / 30)
        b.advance(1 / 30)
    assert np.allclose(a.positions, b.positions)


def test_higher_energy_expands_the_cloud():
    """Energy is the one state knob: tight when idle, open when working."""
    calm, busy = MoleculeField(count=64, seed=11), MoleculeField(count=64, seed=11)
    calm.set_energy(0.0)
    busy.set_energy(1.0)
    for _ in range(120):  # 4s, long enough for the eased scale to settle
        calm.advance(1 / 30)
        busy.advance(1 / 30)
    assert busy.radius_scale > calm.radius_scale


def test_energy_is_clamped_to_the_unit_range():
    field = MoleculeField(count=16, seed=1)
    field.set_energy(4.0)
    assert field.energy == 1.0
    field.set_energy(-2.0)
    assert field.energy == 0.0


def test_energy_change_eases_rather_than_snapping():
    field = MoleculeField(count=16, seed=1)
    field.set_energy(0.0)
    for _ in range(120):
        field.advance(1 / 30)
    settled = field.radius_scale

    field.set_energy(1.0)
    field.advance(1 / 30)
    assert settled < field.radius_scale < field.target_radius_scale


def test_pulse_swells_the_cloud_then_decays():
    """Speaking's word beat: a kick outward that settles back on its own."""
    field = MoleculeField(count=64, seed=13)
    for _ in range(60):
        field.advance(1 / 30)
    resting = field.radius_scale

    field.pulse(1.0)
    field.advance(1 / 30)
    assert field.radius_scale > resting

    for _ in range(60):
        field.advance(1 / 30)
    assert field.radius_scale < resting + 1e-3


def test_the_cloud_turns_in_three_dimensions():
    """Depth only reads if the whole field rotates; a static cloud looks flat."""
    field = MoleculeField(count=32, seed=17)
    before = field.yaw
    for _ in range(30):
        field.advance(1 / 30)
    assert field.yaw != before


# -- bonds: what makes the cloud read as a molecule rather than dust --------


def test_bonds_only_link_particles_within_the_cutoff():
    field = MoleculeField(count=200, seed=19)
    i, j, _ = field.bonds
    assert len(i) > 0
    distance = np.linalg.norm(field.positions[i] - field.positions[j], axis=1)
    assert np.all(distance <= MoleculeField.BOND_CUTOFF)


def test_bonds_never_link_a_particle_to_itself():
    field = MoleculeField(count=120, seed=23)
    i, j, _ = field.bonds
    assert np.all(i != j)


def test_bond_weight_fades_with_distance():
    """Links dim as their two particles drift apart, so bonds appear and
    dissolve instead of popping in and out at full strength."""
    field = MoleculeField(count=200, seed=29)
    i, j, weight = field.bonds
    distance = np.linalg.norm(field.positions[i] - field.positions[j], axis=1)
    near, far = int(np.argmin(distance)), int(np.argmax(distance))
    assert weight[near] > weight[far]
    assert np.all((weight >= 0.0) & (weight <= 1.0))


def test_bond_count_is_capped():
    """A dense frame must not turn into thousands of stroked lines."""
    field = MoleculeField(count=400, seed=31)
    field.set_energy(0.0)
    for _ in range(90):
        field.advance(1 / 30)
    i, _, _ = field.bonds
    assert len(i) <= field.bond_cap


def test_bonds_are_recomputed_on_an_interval_not_every_frame():
    """Perf: the pairwise pass is the expensive part, and positions barely
    change between frames — the lines still follow their particles because
    they're drawn from live positions, only the pairing is cached."""
    field = MoleculeField(count=64, seed=37, bond_interval=3)
    first = field.bonds
    field.advance(1 / 30)
    assert field.bonds is first
    field.advance(1 / 30)
    field.advance(1 / 30)
    assert field.bonds is not first


# -- projection ------------------------------------------------------------


def test_near_particles_project_larger_than_far_ones():
    field = MoleculeField(count=120, seed=41)
    _, size, depth = field.project(100.0)
    assert size[int(np.argmax(depth))] > size[int(np.argmin(depth))]
    assert np.all((depth >= 0.0) & (depth <= 1.0))


def test_projection_stays_inside_the_orb():
    field = MoleculeField(count=200, seed=43)
    field.set_energy(1.0)
    for _ in range(120):
        field.advance(1 / 30)
    xy, size, _ = field.project(100.0)
    assert np.max(np.linalg.norm(xy, axis=1) + size) <= 100.0


def test_projection_shrinks_with_energy():
    calm, busy = MoleculeField(count=64, seed=47), MoleculeField(count=64, seed=47)
    calm.set_energy(0.0)
    busy.set_energy(1.0)
    for _ in range(120):
        calm.advance(1 / 30)
        busy.advance(1 / 30)
    assert np.max(np.linalg.norm(busy.project(100.0)[0], axis=1)) > np.max(
        np.linalg.norm(calm.project(100.0)[0], axis=1)
    )


# -- painting --------------------------------------------------------------


def test_every_particle_gets_one_fragment(qapp):
    from PyQt6.QtCore import QPointF

    field = MoleculeField(count=240, seed=53)
    assert len(field.particle_fragments(QPointF(0, 0), 100.0)) == field.count


def test_a_frame_is_a_handful_of_draw_calls_not_hundreds(qapp):
    """The perf contract: 240 particles are one batched blit, and every bond
    in the field is at most TIERS stroked paths. Filling that many antialiased
    ellipses individually costs ~10x as much per frame."""
    from PyQt6.QtCore import QPointF
    from PyQt6.QtGui import QColor, QPainter, QPixmap

    class Recorder(QPainter):
        def __init__(self, device):
            super().__init__(device)
            self.fragment_calls = 0
            self.path_calls = 0

        def drawPixmapFragments(self, fragments, pixmap):
            self.fragment_calls += 1
            super().drawPixmapFragments(fragments, pixmap)

        def drawPath(self, path):
            self.path_calls += 1
            super().drawPath(path)

    pixmap = QPixmap(240, 240)
    pixmap.fill(QColor(0, 0, 0))
    field = MoleculeField(count=240, seed=53)
    painter = Recorder(pixmap)
    field.paint(painter, QPointF(120, 120), 110.0, QColor(53, 200, 245))
    painter.end()

    assert painter.fragment_calls == 1
    assert 0 < painter.path_calls <= MoleculeField.TIERS


def test_paint_draws_onto_a_real_painter(qapp):
    from PyQt6.QtCore import QPointF
    from PyQt6.QtGui import QColor, QPainter, QPixmap

    pixmap = QPixmap(240, 240)
    pixmap.fill(QColor(0, 0, 0))
    field = MoleculeField(count=120, seed=59)
    painter = QPainter(pixmap)
    field.paint(painter, QPointF(120, 120), 110.0, QColor(53, 200, 245))
    painter.end()

    image = pixmap.toImage()
    lit = sum(
        1
        for y in range(0, 240, 2)
        for x in range(0, 240, 2)
        if image.pixelColor(x, y).blue() > 30
    )
    assert lit > 0  # something actually landed on the canvas


def test_paint_at_zero_opacity_draws_nothing(qapp):
    """The boot sequence fades the field in; at 0 it must be fully invisible."""
    from PyQt6.QtCore import QPointF
    from PyQt6.QtGui import QColor, QPainter, QPixmap

    pixmap = QPixmap(240, 240)
    pixmap.fill(QColor(0, 0, 0))
    field = MoleculeField(count=120, seed=59)
    painter = QPainter(pixmap)
    field.paint(painter, QPointF(120, 120), 110.0, QColor(53, 200, 245), opacity=0.0)
    painter.end()

    image = pixmap.toImage()
    assert all(
        image.pixelColor(x, y).blue() == 0
        for y in range(0, 240, 4)
        for x in range(0, 240, 4)
    )


# -- assembly: the entry animation's finale, the cloud condensing into place --


def test_the_cloud_is_fully_condensed_by_default(qapp):
    field = MoleculeField(count=64, seed=67)
    assert field.assemble == 1.0


def test_assembling_from_zero_starts_the_cloud_scattered_outside_the_orb(qapp):
    field = MoleculeField(count=200, seed=71)
    home = np.linalg.norm(field.project(100.0)[0], axis=1).max()

    field.set_assemble(0.0)
    scattered = np.linalg.norm(field.project(100.0)[0], axis=1).max()

    # Comfortably outside the settled cloud, but still bounded — the caller
    # draws rings out there, and anything wider would clip on the widget edge.
    assert home * 1.5 < scattered < home * 2.0


def test_particles_condense_on_their_own_staggered_schedules(qapp):
    """A uniform shrink reads as one object being scaled; staggering it makes
    the cloud look like it's assembling out of separate motes."""
    field = MoleculeField(count=200, seed=73)
    home = np.linalg.norm(field.project(100.0)[0], axis=1)

    field.set_assemble(0.5)
    halfway = np.linalg.norm(field.project(100.0)[0], axis=1)

    ratio = halfway / np.maximum(home, 1e-9)
    assert ratio.std() > 0.05          # not one rigid scale factor
    assert ratio.min() < ratio.max()


def test_an_assembling_cloud_is_dimmer_than_a_settled_one(qapp):
    from PyQt6.QtCore import QPointF

    field = MoleculeField(count=120, seed=79)
    settled = [f.opacity for f in field.particle_fragments(QPointF(0, 0), 100.0)]

    field.set_assemble(0.2)
    arriving = [f.opacity for f in field.particle_fragments(QPointF(0, 0), 100.0)]

    assert sum(arriving) < sum(settled)


def test_assemble_is_clamped(qapp):
    field = MoleculeField(count=16, seed=83)
    field.set_assemble(2.0)
    assert field.assemble == 1.0
    field.set_assemble(-1.0)
    assert field.assemble == 0.0


def test_nothing_is_drawn_before_the_cloud_starts_arriving(qapp):
    """A bond's alpha floor used to apply even at zero arrival, leaving a
    faint web hanging in the orb through the whole entry animation."""
    from PyQt6.QtCore import QPointF
    from PyQt6.QtGui import QColor

    field = MoleculeField(count=200, seed=89)
    field.set_assemble(0.0)

    bonds = field.bond_paths(QPointF(0, 0), 100.0, QColor(53, 200, 245))
    fragments = field.particle_fragments(QPointF(0, 0), 100.0)

    assert bonds == []
    assert fragments == []
