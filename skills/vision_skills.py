"""Screen awareness — lets Jarvis actually look at what's on the display.

This is the one skill that returns image content blocks instead of a string.
`tool_result` content accepts the same block types as a user message, so the
screenshot goes straight back to the model as something it can see.
"""
import base64
import io
import sys

from . import screen_state
from .base_skill import BaseSkill, SkillResult

IS_WINDOWS = sys.platform == "win32"

# Opus 5 accepts up to 2576px on the long edge, but a full-resolution grab of a
# 4K display costs ~4784 tokens. 1280px is plenty to read an error dialog or
# describe a layout, at roughly a third the price.
MAX_EDGE = 1280


class ScreenshotSkill(BaseSkill):
    name = "see_screen"
    description = (
        "Capture the user's screen and look at it. Use whenever the user refers "
        "to something visible on their PC — 'what's this error?', 'what am I "
        "looking at?', 'read this for me', 'is this code right?'. Returns an "
        "image you can see directly."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "monitor": {
                "type": "integer",
                "description": (
                    "Which monitor to capture. 0 (default) captures all monitors "
                    "combined; 1 is the primary display, 2 the second, etc."
                ),
            },
            "region": {
                "type": "string",
                "enum": ["full", "active_window"],
                "description": "Capture all displays or just the foreground Windows window.",
            },
        },
        "required": [],
    }

    def run(self, monitor: int = 0, region: str = "full") -> SkillResult:
        if region not in {"full", "active_window"}:
            return "region must be 'full' or 'active_window'."
        try:
            png = _grab_active_window() if region == "active_window" else _grab(monitor)
        except ImportError:
            return (
                "I can't see the screen — the 'mss' and 'Pillow' packages "
                "aren't installed. Run: pip install mss pillow"
            )
        except IndexError:
            return f"There's no monitor {monitor} attached."
        except NotImplementedError:
            return "Active-window capture is only available on Windows."
        except Exception as e:
            return f"I couldn't capture the screen: {e}"

        return [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": base64.standard_b64encode(png).decode("ascii"),
                },
            },
            {"type": "text", "text": "Screenshot of the user's current screen."},
        ]


def _ensure_dpi_aware() -> None:
    """Make this process report real (physical) pixel coordinates instead of
    coordinates virtualized for Windows display scaling.

    Without this, on any display running above 100% scaling, mss's
    physical-pixel capture disagrees with the (possibly virtualized)
    coordinates user32/pyautogui use for window rects and clicks, so a click
    at a coordinate read straight off a screenshot lands offset from what was
    actually shown. This is process-wide and idempotent, so calling it before
    every capture is enough to fix window_skills and input_skills too for the
    rest of the process's life.
    """
    if not IS_WINDOWS:
        return
    try:
        import ctypes
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def _grab(monitor: int) -> bytes:
    """Capture and downscale. Raises ImportError if deps are missing."""
    _ensure_dpi_aware()
    import mss
    with mss.mss() as sct:
        # sct.monitors[0] is the union of all displays; 1..n are individual.
        if monitor < 0 or monitor >= len(sct.monitors):
            raise IndexError(monitor)
        mon = sct.monitors[monitor]
        shot = sct.grab(mon)

    return _encode_shot(shot, mon["left"], mon["top"])


def _grab_active_window() -> bytes:
    """Capture the foreground window using Windows APIs and mss."""
    if not IS_WINDOWS:
        raise NotImplementedError

    _ensure_dpi_aware()
    import ctypes
    import mss

    class Rect(ctypes.Structure):
        _fields_ = [
            ("left", ctypes.c_long),
            ("top", ctypes.c_long),
            ("right", ctypes.c_long),
            ("bottom", ctypes.c_long),
        ]

    user32 = ctypes.windll.user32
    hwnd = user32.GetForegroundWindow()
    rect = Rect()
    if not hwnd or not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        raise RuntimeError("could not find the active window")

    width, height = rect.right - rect.left, rect.bottom - rect.top
    if width <= 0 or height <= 0:
        raise RuntimeError("the active window has no visible area")
    with mss.mss() as sct:
        shot = sct.grab({"left": rect.left, "top": rect.top, "width": width, "height": height})
    return _encode_shot(shot, rect.left, rect.top)


def _encode_shot(shot, origin_x: int, origin_y: int) -> bytes:
    """Downscale an mss screenshot, encode a compact PNG, and record how to
    map coordinates read off the resulting image back to real screen pixels
    (see screen_state) — this is what lets click/scroll take coordinates
    straight from what the model just saw, regardless of any downscaling."""
    from PIL import Image

    image = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
    real_width = image.width

    longest = max(image.size)
    if longest > MAX_EDGE:
        factor = MAX_EDGE / longest
        new_size = (max(1, int(image.width * factor)), max(1, int(image.height * factor)))
        image = image.resize(new_size, Image.LANCZOS)

    scale = real_width / image.width
    screen_state.record(scale, origin_x, origin_y)

    buf = io.BytesIO()
    image.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
