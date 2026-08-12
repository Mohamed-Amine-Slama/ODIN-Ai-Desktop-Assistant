"""Shared coordinate mapping between the last screenshot and the real screen.

see_screen downscales its capture for token cost (MAX_EDGE in vision_skills),
so the image the model actually looks at is smaller than the real display.
The model reads click/scroll coordinates straight off that image — it has no
way to know it was scaled, or by how much — so a coordinate handed straight to
pyautogui in image-space pixels lands in the wrong place on any display where
the screenshot didn't ship 1:1 (i.e. almost always). This is the root cause
behind clicks/scrolls silently missing their target while the tool call itself
reports success.

vision_skills records the transform after every capture; input_skills applies
it before every click/scroll so the model can keep reasoning purely in the
coordinates of the image it was just shown.
"""
import threading

_lock = threading.Lock()
_mapping: dict | None = None


def record(scale: float, origin_x: int, origin_y: int) -> None:
    """Called after every screenshot capture.

    scale: real screen pixels per image pixel (1.0 if the capture was not
    downscaled). origin_x/y: the capture's top-left corner in real screen
    coordinates (a monitor's origin, or an active window's, which need not be
    (0, 0) on a multi-monitor setup).
    """
    global _mapping
    with _lock:
        _mapping = {"scale": scale, "origin_x": origin_x, "origin_y": origin_y}


def to_real(x: float, y: float) -> tuple[int, int]:
    """Map an (x, y) given in the most recent screenshot's image space to real
    screen pixels.

    Identity when no screenshot has been recorded yet, so a click issued
    without one first (or in tests that stub pyautogui directly) still lands
    where its caller asked.
    """
    with _lock:
        mapping = _mapping
    if mapping is None:
        return int(round(x)), int(round(y))
    return (
        int(round(mapping["origin_x"] + x * mapping["scale"])),
        int(round(mapping["origin_y"] + y * mapping["scale"])),
    )


def clear() -> None:
    """Reset hook, mainly for tests."""
    global _mapping
    with _lock:
        _mapping = None
