"""Grid geometry — the native-Qt transcription of ODIN-HUD.md §4.

The zone table there is written in CSS-grid line-number notation (`7 / 19`
meaning "from column line 7 to column line 19", 1-indexed, end exclusive).
ZONE_GRID keeps that notation verbatim so it can be checked against the spec
by eye; `place()` does the one conversion to QGridLayout's row/col + span
convention (0-indexed, span counts) at the point of use.
"""
from __future__ import annotations

from PyQt6.QtWidgets import QGridLayout, QWidget

GRID_COLUMNS = 24
GRID_ROWS = 12
GRID_GAP = 14
# CSS `padding: 0 18px 18px` (top, right&left, bottom) -> Qt's
# setContentsMargins(left, top, right, bottom) order.
GRID_MARGINS = (18, 0, 18, 18)

# zone -> (col_start, col_end, row_start, row_end), 1-indexed, end-exclusive —
# copied straight from the §4 table.
ZONE_GRID: dict[str, tuple[int, int, int, int]] = {
    "A": (1, 25, 1, 2),    # header / tick ruler
    "B": (1, 6, 2, 4),     # CPU
    "C": (1, 6, 4, 6),     # memory
    "C2": (1, 6, 6, 7),    # storage
    "D": (7, 19, 2, 7),    # voice orb
    "E": (7, 19, 7, 8),    # transcript
    "E2": (7, 19, 8, 9),   # skill activity log
    "F": (20, 25, 2, 3),   # clock
    "G": (20, 25, 3, 6),   # weather
    "H": (20, 25, 6, 7),   # temps
    "I": (1, 6, 7, 9),     # network
    "J": (20, 25, 7, 9),   # knowledge base
    "K": (1, 6, 9, 11),    # audio spectrum
    "L": (20, 25, 9, 11),  # notes / reminders
    "M": (1, 25, 11, 13),  # dock
}


def place(grid: QGridLayout, widget: QWidget, zone: str) -> None:
    """Add `widget` at `zone`'s position, per the §4 table, in one call —
    so window.py's zone builders read like the spec's table instead of
    hand-computed row/col spans scattered through the code."""
    col0, col1, row0, row1 = ZONE_GRID[zone]
    grid.addWidget(widget, row0 - 1, col0 - 1, row1 - row0, col1 - col0)
