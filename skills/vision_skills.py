"""Screen awareness — lets Jarvis actually look at what's on the display.

This is the one skill that returns image content blocks instead of a string.
`tool_result` content accepts the same block types as a user message, so the
screenshot goes straight back to the model as something it can see.
"""
import base64
import io
import sys

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
            }
        },
        "required": [],
    }

    def run(self, monitor: int = 0) -> SkillResult:
        try:
            png = _grab(monitor)
        except ImportError:
            return (
                "I can't see the screen — the 'mss' and 'Pillow' packages "
                "aren't installed. Run: pip install mss pillow"
            )
        except IndexError:
            return f"There's no monitor {monitor} attached."
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


def _grab(monitor: int) -> bytes:
    """Capture and downscale. Raises ImportError if deps are missing."""
    import mss
    from PIL import Image

    with mss.mss() as sct:
        # sct.monitors[0] is the union of all displays; 1..n are individual.
        if monitor < 0 or monitor >= len(sct.monitors):
            raise IndexError(monitor)
        shot = sct.grab(sct.monitors[monitor])

    image = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")

    longest = max(image.size)
    if longest > MAX_EDGE:
        scale = MAX_EDGE / longest
        new_size = (max(1, int(image.width * scale)), max(1, int(image.height * scale)))
        image = image.resize(new_size, Image.LANCZOS)

    buf = io.BytesIO()
    image.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
