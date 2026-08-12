"""Synthetic keyboard and mouse input.

None of these can be undone — you cannot un-type a keystroke — so none of them
record an undo entry.

pyautogui's corner failsafe is left enabled deliberately: slamming the mouse
into a screen corner aborts an in-flight automation, which is the only runtime
escape hatch once Jarvis is driving the machine.
"""
from core.risk import Risk

from . import screen_state
from .base_skill import BaseSkill


def _gui():
    """Import pyautogui lazily and keep the failsafe on.

    Returns (module, error_message). Importing at module scope would fail on a
    headless box and take the whole skill registry down with it.
    """
    try:
        import pyautogui
    except ImportError:
        return None, "Input control needs the 'pyautogui' package. Run: pip install pyautogui"
    except Exception as e:
        return None, f"Input control is unavailable: {e}"

    if pyautogui is None:
        return None, "Input control needs the 'pyautogui' package. Run: pip install pyautogui"

    pyautogui.FAILSAFE = True
    return pyautogui, None


class TypeTextSkill(BaseSkill):
    name = "type_text"
    description = (
        "Type text on the user's keyboard, into whatever window has focus. Use "
        "for filling in apps that have no other way in. Cannot be undone."
    )
    input_schema = {
        "type": "object",
        "properties": {"text": {"type": "string", "description": "The text to type."}},
        "required": ["text"],
    }
    risk = Risk.MODERATE

    def consequence(self, text: str = "", **_) -> str:
        preview = text if len(text) <= 60 else text[:60] + "..."
        return f"Type this into the focused window?\n    {preview}"

    def run(self, text: str) -> str:
        gui, problem = _gui()
        if problem:
            return problem
        if not text:
            return "There was nothing to type."
        gui.write(text, interval=0.01)
        return f"Typed {len(text)} characters."


class PressKeysSkill(BaseSkill):
    name = "press_keys"
    description = (
        "Press a key or key combination, e.g. 'enter', 'ctrl+s', 'alt+tab'. "
        "Cannot be undone."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "keys": {
                "type": "string",
                "description": "A key or combination joined by '+', e.g. 'ctrl+shift+s'.",
            }
        },
        "required": ["keys"],
    }
    risk = Risk.MODERATE

    def consequence(self, keys: str = "", **_) -> str:
        return f"Press {keys}?"

    def run(self, keys: str) -> str:
        gui, problem = _gui()
        if problem:
            return problem

        parts = [p.strip().lower() for p in (keys or "").split("+") if p.strip()]
        if not parts:
            return "There were no keys to press."

        gui.hotkey(*parts)
        return f"Pressed {'+'.join(parts)}."


class ClickSkill(BaseSkill):
    name = "click"
    description = (
        "Click the mouse at a screen position. Pair with see_screen to find "
        "what to click, and give x/y exactly as they appear in that "
        "screenshot — they are mapped onto the real screen automatically, "
        "even if the image you saw was scaled down. Cannot be undone."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "x": {"type": "integer", "description": "X coordinate, read off the last screenshot."},
            "y": {"type": "integer", "description": "Y coordinate, read off the last screenshot."},
            "button": {"type": "string", "enum": ["left", "right", "middle"]},
            "clicks": {"type": "integer", "description": "1 for a single click, 2 to double-click."},
        },
        "required": ["x", "y"],
    }
    risk = Risk.MODERATE

    def consequence(self, x: int = 0, y: int = 0, button: str = "left", **_) -> str:
        return f"{button.title()}-click at ({x}, {y})?"

    def run(self, x: int, y: int, button: str = "left", clicks: int = 1) -> str:
        gui, problem = _gui()
        if problem:
            return problem
        real_x, real_y = screen_state.to_real(x, y)
        gui.click(x=real_x, y=real_y, button=button, clicks=max(1, int(clicks)))
        return f"Clicked at ({x}, {y})."


class ScrollSkill(BaseSkill):
    name = "scroll"
    description = (
        "Scroll the mouse wheel up or down — through a feed, a DM/contact "
        "list, search results, or any page that doesn't fit on screen. Pair "
        "with see_screen before and after: scroll, then look again to check "
        "whether the target has actually come into view rather than assuming "
        "it has. x/y (optional) are read off the last screenshot the same way "
        "as click. Cannot be undone."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "amount": {
                "type": "integer",
                "description": "Notches to scroll. Positive scrolls up, negative scrolls down.",
            },
            "x": {"type": "integer", "description": "Optional X to scroll at, from the last screenshot."},
            "y": {"type": "integer", "description": "Optional Y to scroll at, from the last screenshot."},
        },
        "required": ["amount"],
    }
    risk = Risk.MODERATE

    def consequence(self, amount: int = 0, **_) -> str:
        direction = "up" if amount >= 0 else "down"
        return f"Scroll {direction} {abs(amount)} notch(es)?"

    def run(self, amount: int, x: int = None, y: int = None) -> str:
        gui, problem = _gui()
        if problem:
            return problem
        try:
            amount = int(amount)
        except (TypeError, ValueError):
            return "I need a number of notches to scroll."
        if amount == 0:
            return "There was nothing to scroll."

        if x is not None and y is not None:
            real_x, real_y = screen_state.to_real(x, y)
            gui.scroll(amount, x=real_x, y=real_y)
        else:
            gui.scroll(amount)

        direction = "up" if amount >= 0 else "down"
        return f"Scrolled {direction} {abs(amount)} notch(es)."
