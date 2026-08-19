"""ui/hud/console.py's ConsoleOverlay — title-band dragging.

ConsoleOverlay is a plain QWidget floating over the HUD's root widget, not a
real top-level window, so it gets none of a window manager's drag-by-title
behavior for free. Dragging is implemented by hand via an event filter on
`self.panel` (see console.py's own comment on why the filter has to sit
there rather than on ConsoleOverlay itself), so these call `eventFilter`
directly rather than going through QApplication's full event dispatch —
same directness as test_hud_orb.py calling `mousePressEvent` straight.
"""
from PyQt6.QtCore import QPoint, QPointF, Qt
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import QWidget

from ui.hud.console import ConsoleOverlay


def _press(pos: QPointF) -> QMouseEvent:
    return QMouseEvent(
        QMouseEvent.Type.MouseButtonPress, pos,
        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
    )


def _move(pos: QPointF) -> QMouseEvent:
    return QMouseEvent(
        QMouseEvent.Type.MouseMove, pos,
        Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
    )


def _release(pos: QPointF) -> QMouseEvent:
    return QMouseEvent(
        QMouseEvent.Type.MouseButtonRelease, pos,
        Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
    )


def test_drag_from_title_band_moves_the_console(qapp):
    parent = QWidget()
    parent.resize(1000, 800)
    console = ConsoleOverlay(parent)
    console.move(100, 100)
    start = console.pos()

    assert console.eventFilter(console.panel, _press(QPointF(50, 5))) is True
    assert console.eventFilter(console.panel, _move(QPointF(80, 15))) is True
    assert console.pos() == start + QPoint(30, 10)

    assert console.eventFilter(console.panel, _release(QPointF(80, 15))) is True
    assert console._drag_offset is None

    # Further movement without a new press must not drag it again.
    console.eventFilter(console.panel, _move(QPointF(200, 200)))
    assert console.pos() == start + QPoint(30, 10)


def test_click_below_the_title_band_does_not_start_a_drag(qapp):
    parent = QWidget()
    parent.resize(1000, 800)
    console = ConsoleOverlay(parent)
    console.move(100, 100)
    start = console.pos()

    # y=100 lands in the body (scrollback/input field), well past the
    # title band — must fall through unconsumed, same as any other widget
    # eventFilter that isn't interested in this event.
    assert console.eventFilter(console.panel, _press(QPointF(50, 100))) is False
    assert console._drag_offset is None

    console.eventFilter(console.panel, _move(QPointF(300, 300)))
    assert console.pos() == start


def test_drag_clamps_to_the_parent_widget_bounds(qapp):
    parent = QWidget()
    parent.resize(900, 700)  # smaller than the console's fixed 760x220 in both margins
    console = ConsoleOverlay(parent)
    console.move(50, 50)

    console.eventFilter(console.panel, _press(QPointF(10, 5)))
    console.eventFilter(console.panel, _move(QPointF(5000, 5000)))

    assert console.pos() == QPoint(parent.width() - console.width(), parent.height() - console.height())


def test_toggle_works_even_before_the_hud_is_shown(qapp):
    """isVisible() is False for a child of a hidden window however many times
    you show it, so keying the toggle off it made the console stick open.
    is_open reflects the console's own state instead."""
    from PyQt6.QtWidgets import QWidget

    parent = QWidget()          # deliberately never shown
    console = ConsoleOverlay(parent)
    assert console.is_open is False

    console.toggle()
    assert console.is_open is True

    console.toggle()
    assert console.is_open is False
