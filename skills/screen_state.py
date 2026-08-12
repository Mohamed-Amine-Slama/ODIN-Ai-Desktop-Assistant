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

The mapping carries a recorded_at timestamp so a click issued long after the
screenshot it was read off — the screen may have changed, a window moved, a
monitor been unplugged — can be refused rather than silently aimed at
whatever now occupies that stale coordinate. See is_stale().
"""
import threading
import time

_lock = threading.Lock()
_mapping: dict | None = None

# How long a screenshot's coordinate mapping stays trustworthy. Generous on
# purpose — this only guards against "the model is reasoning off a
# screenshot from a much earlier, unrelated turn," not normal think time
# between a screenshot and the click it informs.
STALE_AFTER_SECONDS = 120.0


def record(scale: float, origin_x: int, origin_y: int) -> None:
    """Called after every screenshot capture.

    scale: real screen pixels per image pixel (1.0 if the capture was not
    downscaled). origin_x/y: the capture's top-left corner in real screen
    coordinates (a monitor's origin, or an active window's, which need not be
    (0, 0) on a multi-monitor setup).
    """
    global _mapping
    with _lock:
        _mapping = {
            "scale": scale, "origin_x": origin_x, "origin_y": origin_y,
            "recorded_at": time.monotonic(),
        }


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


def is_stale() -> bool:
    """Whether the current mapping is old enough that the screen it was
    read from probably no longer matches. False when no screenshot has been
    recorded yet — that's the identity-mapping case above, not staleness."""
    with _lock:
        mapping = _mapping
    if mapping is None:
        return False
    return (time.monotonic() - mapping["recorded_at"]) > STALE_AFTER_SECONDS


def clear() -> None:
    """Reset hook, mainly for tests."""
    global _mapping
    with _lock:
        _mapping = None
