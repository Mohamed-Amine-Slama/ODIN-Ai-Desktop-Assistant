"""Design tokens — the native-Qt transcription of ODIN-HUD.md §3.

Every color, glow, font, and duration used anywhere under ui/hud/ comes from
here. Never construct a raw QColor from a hex literal outside this file —
that's the one rule §3 asks for, just enforced by convention instead of a
CSS lint since Python has no equivalent tool for "no magic colors."
"""
from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont, QFontDatabase, QPainter, QPen

# --- Field ------------------------------------------------------------
VOID = QColor(0x00, 0x03, 0x0A)
PANEL = QColor(6, 28, 48, 87)  # rgba(6,28,48,0.34) -> 0.34*255 ≈ 87
PANEL_SOLID = QColor(0x04, 0x18, 0x24)

# --- The cyan ramp — this is the whole palette -------------------------
CY_100 = QColor(0xE4, 0xFB, 0xFF)  # headline numerals, peak values
CY_200 = QColor(0x9B, 0xE8, 0xFF)  # primary readouts
CY_300 = QColor(0x35, 0xC8, 0xF5)  # PRIMARY ACCENT
CY_400 = QColor(0x12, 0x8F, 0xC4)  # secondary strokes
CY_500 = QColor(0x0B, 0x5F, 0x87)  # labels, muted text
CY_600 = QColor(0x07, 0x3B, 0x55)  # hairlines, ticks, brackets
CY_700 = QColor(0x04, 0x21, 0x2F)  # gauge track, empty bar segments

# --- The background field ------------------------------------------------
# The hex backdrop's tiling. Near-black by design: it sits behind everything,
# encodes nothing, and must never compete with the instruments on top of it.
HEX_FILL = QColor(0x08, 0x0A, 0x12)
HEX_EDGE = QColor(0x1C, 0x20, 0x30)

# --- State — used sparingly, only to mean something ---------------------
OK = QColor(0x17, 0xE9, 0xA0)
WARN = QColor(0xFF, 0xB0, 0x20)
CRIT = QColor(0xFF, 0x44, 0x44)
THINKING = QColor(0xB0, 0x6C, 0xFF)  # the orb's processing state, nowhere else

# Threshold fractions shared by every meter/gauge that recolors on load.
WARN_THRESHOLD = 0.75
CRIT_THRESHOLD = 0.90

# --- Motion (ms) --------------------------------------------------------
DUR_FAST = 140
DUR_VAL = 600
DUR_SLOW = 1200

# Widgets fed at one rate and drawn at another (telemetry lands ~1/s, the HUD
# paints at frame rate) ease toward their target instead of snapping. One
# definition, so everything on the HUD moves at the same pace.
EASE_RATE = 7.0        # per second, exponential approach
EASE_EPSILON = 0.001   # close enough to a target to land on it


def ease_toward(current: float, target: float, dt: float) -> float:
    moved = current + (target - current) * min(1.0, EASE_RATE * dt)
    return target if abs(target - moved) < EASE_EPSILON else moved


# --- Type scale (px, fixed — this is a fixed-resolution HUD) -------------
T_MICRO = 9
T_LABEL = 11
T_BODY = 13
T_DATA = 16
T_LG = 28
T_XL = 54
T_HERO = 72

# --- Line and shape -------------------------------------------------------
HAIRLINE_WIDTH = 1.0
STROKE_WIDTH = 2.0
GAUGE_STROKE_WIDTH = 4.0
CORNER_BRACKET_SPAN = 14


def _available_family(*candidates: str) -> str:
    """First installed font family from `candidates`, else the last one
    (a generic fallback name Qt will substitute on its own)."""
    installed = set(QFontDatabase.families())
    for name in candidates[:-1]:
        if name in installed:
            return name
    return candidates[-1]


# Faces resolved once, lazily, the first time a font() helper below is
# called — QFontDatabase needs a QApplication to already exist.
_FACE_DISPLAY: str | None = None
_FACE_LABEL: str | None = None
_FACE_DATA: str | None = None


def _faces() -> tuple[str, str, str]:
    global _FACE_DISPLAY, _FACE_LABEL, _FACE_DATA
    if _FACE_DISPLAY is None:
        _FACE_DISPLAY = _available_family("Michroma", "Segoe UI")
        _FACE_LABEL = _available_family("Saira Condensed", "Roboto Condensed", "Segoe UI")
        _FACE_DATA = _available_family("Share Tech Mono", "JetBrains Mono", "Consolas")
    return _FACE_DISPLAY, _FACE_LABEL, _FACE_DATA


def font_display(size: int = T_DATA) -> QFont:
    """Wordmarks and the ODIN lockup only — never below 14px, never more
    than ~6 words (§3.3)."""
    face, _, _ = _faces()
    f = QFont(face)
    f.setPixelSize(max(size, 14))
    f.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 112)
    return f


def font_label(size: int = T_LABEL, bold: bool = False) -> QFont:
    """Panel titles, axis labels, ring segments, buttons — always uppercase
    at the call site, wide-tracked (§3.3)."""
    _, face, _ = _faces()
    f = QFont(face)
    f.setPixelSize(size)
    f.setWeight(QFont.Weight.DemiBold if bold else QFont.Weight.Medium)
    f.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 122)
    return f


def font_data(size: int = T_DATA) -> QFont:
    """Every number, clock, IP, percentage, byte count — tabular figures
    so digits don't jitter as values change (§3.3)."""
    _, _, face = _faces()
    f = QFont(face)
    f.setPixelSize(size)
    f.setStyleStrategy(QFont.StyleStrategy.PreferQuality)
    return f


def orb_accent(state: str) -> QColor:
    """The centerpiece's one color departure from the ramp: the `thinking`
    violet is reserved exclusively for it (§3.1). Deliberately not the same
    palette as ui/orb.py's ReactorOrb — that widget answers to a different,
    pre-existing state set for the small ambient desktop orb; this is the
    spec-compliant mapping for the new full HUD centerpiece."""
    if state == "thinking" or state == "learning":
        return THINKING if state == "thinking" else CY_200
    if state == "error":
        return CRIT
    return CY_300


def threshold_color(fraction: float) -> QColor:
    """0..1 load fraction -> the accent it should render in, per the
    75%/90% recolor rule repeated across RadialGauge, BarMeter, VoiceOrb."""
    if fraction >= CRIT_THRESHOLD:
        return CRIT
    if fraction >= WARN_THRESHOLD:
        return WARN
    return CY_300


def draw_glow(
    painter: QPainter,
    draw_fn: Callable[[QPen], None],
    color: QColor,
    width: float,
    passes: int = 3,
) -> None:
    """Cheap QPainter stand-in for an SVG `feGaussianBlur`+`feMerge` glow
    filter (§3.2) — a handful of progressively wider, fainter strokes under
    one crisp final pass, instead of an offscreen blur. Cheap enough to run
    across every gauge/orb frame; `QGraphicsDropShadowEffect` is not (a real
    render-to-texture pass per widget), which is why nothing here uses it.

    `draw_fn(pen)` must perform exactly one stroke/paint call using `pen` —
    called `passes` times for the glow halo, then once more at full opacity.
    """
    for i in range(passes, 0, -1):
        extra = i * width * 0.9
        alpha = max(8, int(70 / i))
        halo = QColor(color.red(), color.green(), color.blue(), alpha)
        pen = QPen(halo, width + extra)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        draw_fn(pen)
    core_pen = QPen(color, width)
    core_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    draw_fn(core_pen)


def corner_ticks(
    painter: QPainter,
    rect,
    accent: QColor,
    span: int = CORNER_BRACKET_SPAN,
    inset: int = 8,
    alpha: int = 200,
    width: float = HAIRLINE_WIDTH,
) -> None:
    """Four L-shaped corner brackets, open sides — the Panel signature
    (§5.1). Shared by every bracket-framed widget in ui/hud/ rather than
    reimplemented per component."""
    pen = QPen(QColor(accent.red(), accent.green(), accent.blue(), alpha), width)
    painter.setPen(pen)
    for x, y, dx, dy in (
        (rect.left() + inset, rect.top() + inset, 1, 1),
        (rect.right() - inset, rect.top() + inset, -1, 1),
        (rect.left() + inset, rect.bottom() - inset, 1, -1),
        (rect.right() - inset, rect.bottom() - inset, -1, -1),
    ):
        painter.drawLine(int(x), int(y), int(x + span * dx), int(y))
        painter.drawLine(int(x), int(y), int(x), int(y + span * dy))
