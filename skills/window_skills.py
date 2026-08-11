"""Window management via ctypes against user32.

ctypes rather than pywin32: this needs six calls, and pywin32 was removed from
requirements as unused. Every OS entry point is a module-level function so the
tests can patch it and run on Linux.
"""
import ctypes
import sys
from ctypes import wintypes

from core.risk import Risk
from core.undo import get_journal

from .base_skill import BaseSkill

IS_WINDOWS = sys.platform == "win32"

SW_MINIMIZE = 6
SW_MAXIMIZE = 3
SW_RESTORE = 9
WM_CLOSE = 0x0010

STATE_TO_FLAG = {"minimized": SW_MINIMIZE, "maximized": SW_MAXIMIZE, "normal": SW_RESTORE}


def enumerate_windows() -> list[tuple[int, str]]:
    """Return [(hwnd, title)] for visible, titled top-level windows."""
    if not IS_WINDOWS:
        return []

    user32 = ctypes.windll.user32
    found: list[tuple[int, str]] = []

    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def on_window(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length == 0:
            return True
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        title = buffer.value.strip()
        if title:
            found.append((int(hwnd), title))
        return True

    user32.EnumWindows(callback_type(on_window), 0)
    return found


def foreground_handle() -> int:
    return int(ctypes.windll.user32.GetForegroundWindow()) if IS_WINDOWS else 0


def focus_handle(hwnd: int) -> None:
    user32 = ctypes.windll.user32
    user32.ShowWindow(hwnd, SW_RESTORE)
    user32.SetForegroundWindow(hwnd)


def window_state(hwnd: int) -> str:
    user32 = ctypes.windll.user32
    if user32.IsIconic(hwnd):
        return "minimized"
    if user32.IsZoomed(hwnd):
        return "maximized"
    return "normal"


def set_state(hwnd: int, state: str) -> None:
    ctypes.windll.user32.ShowWindow(hwnd, STATE_TO_FLAG[state])


def close_handle(hwnd: int) -> None:
    ctypes.windll.user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)


def _match(title: str) -> tuple[list[tuple[int, str]], str | None]:
    """Return (matches, error_message)."""
    needle = (title or "").strip().lower()
    if not needle:
        return [], "I need a window title to match."

    matches = [(h, t) for h, t in enumerate_windows() if needle in t.lower()]
    if not matches:
        return [], f"I couldn't find a window matching '{title}'."
    if len(matches) > 1:
        listing = "\n".join(f"  {t}" for _, t in matches)
        return matches, (
            f"'{title}' matches more than one window — tell me which:\n{listing}"
        )
    return matches, None


class ListWindowsSkill(BaseSkill):
    name = "list_windows"
    description = "List the windows currently open on the user's PC."
    input_schema = {"type": "object", "properties": {}, "required": []}
    risk = Risk.SAFE

    def run(self) -> str:
        if not IS_WINDOWS:
            return "Window management is only available on Windows."
        windows = enumerate_windows()
        if not windows:
            return "I can't see any open windows."
        return f"{len(windows)} open window(s):\n" + "\n".join(f"  {t}" for _, t in windows)


class FocusWindowSkill(BaseSkill):
    name = "focus_window"
    description = "Bring a window to the front by (part of) its title."
    input_schema = {
        "type": "object",
        "properties": {"title": {"type": "string", "description": "Part of the window title."}},
        "required": ["title"],
    }
    risk = Risk.MODERATE

    def consequence(self, title: str = "", **_) -> str:
        return f"Switch to the '{title}' window?"

    def run(self, title: str) -> str:
        if not IS_WINDOWS:
            return "Window management is only available on Windows."
        matches, problem = _match(title)
        if problem:
            return problem

        hwnd, matched_title = matches[0]
        previous = foreground_handle()
        focus_handle(hwnd)

        if previous and previous != hwnd:
            def restore(target=previous):
                focus_handle(target)
                return "Switched back."

            get_journal().record("Switch back to the previous window", restore)
        return f"Switched to {matched_title}."


class SetWindowStateSkill(BaseSkill):
    name = "set_window_state"
    description = "Minimise, maximise, or restore a window by (part of) its title."
    input_schema = {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "Part of the window title."},
            "state": {"type": "string", "enum": ["minimized", "maximized", "normal"]},
        },
        "required": ["title", "state"],
    }
    risk = Risk.MODERATE

    def consequence(self, title: str = "", state: str = "", **_) -> str:
        return f"Set the '{title}' window to {state}?"

    def run(self, title: str, state: str) -> str:
        if not IS_WINDOWS:
            return "Window management is only available on Windows."
        if state not in STATE_TO_FLAG:
            return f"'{state}' isn't a window state I know."

        matches, problem = _match(title)
        if problem:
            return problem

        hwnd, matched_title = matches[0]
        previous = window_state(hwnd)
        set_state(hwnd, state)

        def restore(target=hwnd, old=previous):
            set_state(target, old)
            return f"Set it back to {old}."

        get_journal().record(f"Set {matched_title} back to {previous}", restore)
        return f"Set {matched_title} to {state}."


class CloseWindowSkill(BaseSkill):
    name = "close_window"
    description = (
        "Close a window by (part of) its title. This may discard unsaved work "
        "and cannot be undone."
    )
    input_schema = {
        "type": "object",
        "properties": {"title": {"type": "string", "description": "Part of the window title."}},
        "required": ["title"],
    }
    risk = Risk.DANGEROUS

    def consequence(self, title: str = "", **_) -> str:
        return f"Close the '{title}' window? Any unsaved work in it will be lost."

    def run(self, title: str) -> str:
        if not IS_WINDOWS:
            return "Window management is only available on Windows."
        matches, problem = _match(title)
        if problem:
            return problem

        hwnd, matched_title = matches[0]
        close_handle(hwnd)
        # Deliberately records no undo entry — a closed window cannot be reopened.
        return f"Closed {matched_title}."
