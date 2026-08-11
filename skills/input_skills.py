"""Synthetic keyboard and mouse input.

None of these can be undone — you cannot un-type a keystroke — so none of them
record an undo entry.

pyautogui's corner failsafe is left enabled deliberately: slamming the mouse
into a screen corner aborts an in-flight automation, which is the only runtime
escape hatch once Jarvis is driving the machine.
"""
from core.risk import Risk

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
        "what to click. Cannot be undone."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "x": {"type": "integer", "description": "Screen X coordinate."},
            "y": {"type": "integer", "description": "Screen Y coordinate."},
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
        gui.click(x=int(x), y=int(y), button=button, clicks=max(1, int(clicks)))
        return f"Clicked at ({x}, {y})."
