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

# zone -> (col_start, col_end, row_start, row_end), 1-indexed, end-exclusive.
#
# Row spans deviate from ODIN-HUD.md §4's literal table. That table was
# written for a CSS layout where a panel can simply grow to fit its content;
# translated verbatim into this fixed 24x12 Qt grid, several single-row
# zones (storage, clock, thermals, transcript, skill log) got ~40px of real
# content height against 70-100px+ of fixed-height widgets — the exact
# overlapping/clipped text a live screenshot caught. These spans were
# rebalanced against the actual per-zone pixel budget (computed from
# GRID_GAP/GRID_MARGINS at 1920x1080) so every zone's content fits without
# overlap; the col spans and the left/center/right grouping are unchanged.
ZONE_GRID: dict[str, tuple[int, int, int, int]] = {
    "A": (1, 25, 1, 2),     # header / tick ruler
    "B": (1, 6, 2, 4),      # CPU
    "C": (1, 6, 4, 6),      # memory
    "C2": (1, 6, 6, 8),     # storage — was 1 row, needed 2
    "D": (7, 19, 2, 7),     # voice orb
    "E": (7, 19, 7, 9),     # transcript — was 1 row, needed 2
    "E2": (7, 19, 9, 11),   # skill activity log — was 1 row, needed 2
    "F": (20, 25, 2, 4),    # clock — was 1 row, needed 2
    "G": (20, 25, 4, 6),    # weather — was 3 rows, 2 is enough
    "H": (20, 25, 6, 8),    # temps — was 1 row, needed 2
    "I": (1, 6, 8, 10),     # network
    "J": (20, 25, 8, 10),   # knowledge base
    "K": (1, 6, 10, 11),    # audio spectrum — decorative, 1 row
    "L": (20, 25, 10, 11),  # notes / reminders — lower priority, 1 row
    "M": (1, 25, 11, 13),   # dock
}


def place(grid: QGridLayout, widget: QWidget, zone: str) -> None:
    """Add `widget` at `zone`'s position, per the §4 table, in one call —
    so window.py's zone builders read like the spec's table instead of
    hand-computed row/col spans scattered through the code."""
    col0, col1, row0, row1 = ZONE_GRID[zone]
    grid.addWidget(widget, row0 - 1, col0 - 1, row1 - row0, col1 - col0)
