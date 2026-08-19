"""Line-art icons for the dock, drawn as QPainterPaths.

No image assets and no icon-font dependency: each icon is a handful of path
operations in a fixed 24x24 box, stroked by the caller at whatever size it
needs. That keeps them crisp at any dock magnification, recolourable from
ui/hud/tokens.py like everything else, and free of the licensing and
packaging questions a bundled icon set brings.

Built once per name and cached — the dock repaints continuously while the
magnifier eases, and rebuilding ten paths per frame would be pure waste.
"""
from __future__ import annotations

import math

from PyQt6.QtCore import QPointF, QRectF
from PyQt6.QtGui import QPainterPath

ICON_BOX = 24.0
_MID = ICON_BOX / 2

_CACHE: dict[str, QPainterPath] = {}


def _folder() -> QPainterPath:
    path = QPainterPath()
    path.moveTo(3, 19)
    path.lineTo(3, 6)
    path.lineTo(9.5, 6)
    path.lineTo(11.5, 8.5)
    path.lineTo(21, 8.5)
    path.lineTo(21, 19)
    path.closeSubpath()
    return path


def _globe() -> QPainterPath:
    path = QPainterPath()
    path.addEllipse(QPointF(_MID, _MID), 9.0, 9.0)
    path.addEllipse(QPointF(_MID, _MID), 3.6, 9.0)   # the meridians, edge-on
    path.moveTo(3, _MID)
    path.lineTo(21, _MID)
    path.moveTo(4.4, 8.0)
    path.lineTo(19.6, 8.0)
    path.moveTo(4.4, 16.0)
    path.lineTo(19.6, 16.0)
    return path


def _terminal() -> QPainterPath:
    path = QPainterPath()
    path.addRoundedRect(QRectF(3, 4.5, 18, 15), 1.5, 1.5)
    path.moveTo(6.5, 9.5)
    path.lineTo(10, 12.5)
    path.lineTo(6.5, 15.5)
    path.moveTo(12, 15.5)
    path.lineTo(17, 15.5)
    return path


def _code() -> QPainterPath:
    path = QPainterPath()
    path.moveTo(9, 7)
    path.lineTo(4, 12)
    path.lineTo(9, 17)
    path.moveTo(15, 7)
    path.lineTo(20, 12)
    path.lineTo(15, 17)
    path.moveTo(13.2, 5.5)
    path.lineTo(10.8, 18.5)
    return path


def _note() -> QPainterPath:
    path = QPainterPath()
    path.moveTo(10, 17.5)
    path.lineTo(10, 5.5)
    path.lineTo(18, 7.8)
    path.lineTo(18, 11.2)
    path.lineTo(10, 8.9)
    path.addEllipse(QPointF(7.6, 17.4), 2.6, 2.2)
    return path


def _gear() -> QPainterPath:
    # Body, hub and short teeth outside the body — bare radial spokes around a
    # single circle read as a sun rather than a gear.
    path = QPainterPath()
    path.addEllipse(QPointF(_MID, _MID), 7.2, 7.2)
    path.addEllipse(QPointF(_MID, _MID), 3.0, 3.0)
    for i in range(8):
        angle = math.radians(i * 45)
        cos_a, sin_a = math.cos(angle), math.sin(angle)
        path.moveTo(_MID + 7.0 * cos_a, _MID + 7.0 * sin_a)
        path.lineTo(_MID + 10.0 * cos_a, _MID + 10.0 * sin_a)
    return path


def _monitor() -> QPainterPath:
    path = QPainterPath()
    path.addRoundedRect(QRectF(3, 4.5, 18, 13), 1.5, 1.5)
    path.moveTo(9, 20)
    path.lineTo(15, 20)
    path.moveTo(12, 17.5)
    path.lineTo(12, 20)
    for x, top in ((7.5, 13.5), (11, 9.5), (14.5, 11.5), (18, 7.5)):
        path.moveTo(x, 15)
        path.lineTo(x, top)
    return path


def _camera() -> QPainterPath:
    path = QPainterPath()
    path.addRoundedRect(QRectF(3, 7, 18, 12.5), 1.5, 1.5)
    path.moveTo(8.5, 7)
    path.lineTo(10, 4.5)
    path.lineTo(14, 4.5)
    path.lineTo(15.5, 7)
    path.addEllipse(QPointF(_MID, 13.2), 3.6, 3.6)
    return path


def _console() -> QPainterPath:
    path = QPainterPath()
    path.moveTo(6, 8)
    path.lineTo(10.5, 12)
    path.lineTo(6, 16)
    path.moveTo(12.5, 16)
    path.lineTo(19, 16)
    return path


def _hand() -> QPainterPath:
    path = QPainterPath()
    # Four fingers of staggered length, then a palm arc under them.
    for x, top in ((8.0, 8.0), (11.0, 5.5), (14.0, 6.2), (16.8, 9.2)):
        path.moveTo(x, 13.5)
        path.lineTo(x, top)
    path.moveTo(6.6, 12.0)
    path.lineTo(6.6, 15.5)
    path.arcTo(QRectF(6.6, 12.5, 11.6, 8.0), 180, 180)
    path.lineTo(18.2, 12.0)
    return path


_BUILDERS = {
    "EXP": _folder,
    "WEB": _globe,
    "TERM": _terminal,
    "CODE": _code,
    "MUS": _note,
    "SET": _gear,
    "SYS": _monitor,
    "SNAP": _camera,
    "CON": _console,
    "HAND": _hand,
}

ICON_NAMES = frozenset(_BUILDERS)


def _placeholder() -> QPainterPath:
    """Anything unrecognised gets a plain ring. A dock entry added without a
    matching icon should look unfinished, never take the HUD down."""
    path = QPainterPath()
    path.addEllipse(QPointF(_MID, _MID), 6.0, 6.0)
    return path


def icon_path(name: str) -> QPainterPath:
    """The named icon in a 24x24 box, built once and shared. Callers scale it
    with a transform rather than asking for a size."""
    path = _CACHE.get(name)
    if path is None:
        path = _CACHE[name] = _BUILDERS.get(name, _placeholder)()
    return path
