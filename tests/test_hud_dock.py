"""ui/hud/icons.py and the dock (ui/hud/widgets.py's DockButton, Dock).

The dock used to be ten identical rings of three-letter text with no state at
all: the two toggles among them (CON, HAND) looked exactly like the launchers,
and a click during a busy turn was swallowed in silence. These cover the
iconography, the states, and the magnifier's geometry.
"""
from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtGui import QPixmap

from ui.hud.icons import ICON_BOX, icon_path
from ui.hud.widgets import Dock, DockButton
from ui.hud.zones import DOCK_ITEMS


def _render(widget, size=None):
    if size:
        widget.resize(*size)
    pixmap = QPixmap(widget.size())
    widget.render(pixmap)
    return pixmap


# -- icons ------------------------------------------------------------------


def test_every_dock_entry_has_an_icon(qapp):
    for glyph, _label, _preset in DOCK_ITEMS:
        path = icon_path(glyph)
        assert not path.isEmpty(), f"no icon drawn for {glyph}"
        bounds = path.boundingRect()
        assert bounds.width() <= ICON_BOX and bounds.height() <= ICON_BOX


def test_icons_are_built_once_and_reused(qapp):
    """Perf: these are rebuilt on every dock repaint otherwise, and the dock
    repaints continuously while the magnifier is easing."""
    assert icon_path("WEB") is icon_path("WEB")


def test_an_unknown_glyph_falls_back_instead_of_crashing_the_dock(qapp):
    assert not icon_path("NOT-A-REAL-GLYPH").isEmpty()


# -- button states ----------------------------------------------------------


def test_a_toggle_button_shows_whether_it_is_on(qapp):
    button = DockButton("CON", "ODIN Console")
    assert button.is_active is False

    button.set_active(True)

    assert button.is_active is True
    _render(button)


def test_an_unavailable_button_does_not_fire(qapp):
    """A click during a turn is dropped by the window anyway — the button has
    to say so rather than looking live and doing nothing."""
    button = DockButton("EXP", "Explorer")
    fired = []
    button.clicked.connect(lambda: fired.append(1))

    button.set_available(False)
    button.click()
    assert fired == []
    _render(button)

    button.set_available(True)
    button.click()
    assert fired == [1]


def test_a_launched_button_flashes_and_settles(qapp):
    button = DockButton("WEB", "Browser")
    assert button.launch_flash == 0.0

    button.flash_launch()
    assert button.launch_flash > 0.0
    _render(button)

    for _ in range(180):
        button.advance(1 / 60)
    assert button.launch_flash == 0.0


def test_magnification_eases_toward_its_target(qapp):
    button = DockButton("MUS", "Music")
    button.set_magnification(1.4)
    assert button.magnification < 1.4

    for _ in range(180):
        button.advance(1 / 60)
    assert button.magnification == 1.4


# -- the magnifying dock ----------------------------------------------------


def _dock(qapp):
    dock = Dock()
    for glyph, label, _preset in DOCK_ITEMS:
        dock.add_button(DockButton(glyph, label, dock))
    dock.resize(1200, 140)
    return dock


def test_the_dock_swells_around_the_cursor(qapp):
    dock = _dock(qapp)
    dock.settle()
    middle = dock.buttons[4]

    dock.set_cursor_x(middle.geometry().center().x())
    dock.settle()

    scales = [b.magnification for b in dock.buttons]
    assert scales[4] == max(scales)
    assert scales[4] > scales[3] > scales[1]     # falls off with distance
    assert scales[0] < scales[3]


def test_the_dock_relaxes_when_the_cursor_leaves(qapp):
    dock = _dock(qapp)
    dock.set_cursor_x(dock.buttons[2].geometry().center().x())
    dock.settle()
    assert dock.buttons[2].magnification > 1.0

    dock.set_cursor_x(None)
    dock.settle()

    assert all(round(b.magnification, 3) == 1.0 for b in dock.buttons)


def test_magnified_buttons_never_overlap(qapp):
    """The whole point of laying the row out by hand: a grown button pushes
    its neighbours aside instead of covering them."""
    dock = _dock(qapp)
    dock.set_cursor_x(dock.buttons[6].geometry().center().x())
    dock.settle()

    for left, right in zip(dock.buttons, dock.buttons[1:]):
        assert left.geometry().right() <= right.geometry().left() + 1


def test_the_row_stays_centered_however_it_is_magnified(qapp):
    dock = _dock(qapp)
    dock.settle()
    rest = (dock.buttons[0].geometry().left() + dock.buttons[-1].geometry().right()) / 2

    dock.set_cursor_x(dock.buttons[0].geometry().center().x())
    dock.settle()
    magnified = (dock.buttons[0].geometry().left() + dock.buttons[-1].geometry().right()) / 2

    assert abs(rest - dock.width() / 2) <= 2
    assert abs(magnified - dock.width() / 2) <= 2


def test_a_settled_dock_stops_doing_layout_work(qapp):
    """Perf: the dock advances every frame with the rest of the HUD, and at
    rest that has to cost nothing."""
    dock = _dock(qapp)
    dock.settle()
    before = dock.layout_passes

    for _ in range(30):
        dock.advance(1 / 60)

    assert dock.layout_passes == before


def test_hovering_a_button_drives_the_magnifier(qapp):
    """Qt delivers mouse moves to the child under the cursor, so the dock
    never sees them itself — without forwarding, the magnifier only responded
    in the gaps between buttons."""
    from PyQt6.QtCore import QPointF
    from PyQt6.QtGui import QMouseEvent

    dock = _dock(qapp)
    dock.settle()
    target = dock.buttons[7]
    local = QPointF(target.width() / 2, target.height() / 2)
    event = QMouseEvent(
        QMouseEvent.Type.MouseMove, local, Qt.MouseButton.NoButton,
        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
    )

    dock.eventFilter(target, event)
    dock.settle()

    scales = [b.magnification for b in dock.buttons]
    assert scales[7] == max(scales)
    assert scales[7] > 1.0


def test_leaving_one_button_does_not_collapse_the_row_on_the_spot(qapp):
    """Gliding from one button to the next fires Leave on the first before
    Enter reaches the second — collapsing there would make the row stutter."""
    from PyQt6.QtCore import QEvent

    dock = _dock(qapp)
    dock.set_cursor_x(dock.buttons[3].geometry().center().x())
    dock.settle()

    dock.eventFilter(dock.buttons[3], QEvent(QEvent.Type.Leave))

    assert dock.cursor_x is not None


def test_the_row_relaxes_on_the_next_frame_once_nothing_is_hovered(qapp):
    from PyQt6.QtCore import QEvent

    dock = _dock(qapp)
    dock.set_cursor_x(dock.buttons[3].geometry().center().x())
    dock.settle()

    dock.eventFilter(dock.buttons[3], QEvent(QEvent.Type.Leave))
    dock.advance(1 / 60)          # nothing is under the mouse here
    dock.settle()

    assert dock.cursor_x is None
    assert all(round(b.magnification, 3) == 1.0 for b in dock.buttons)
