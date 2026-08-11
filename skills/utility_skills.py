"""General utility skills."""
import ast
import datetime
import operator
import time

from core.store import get_store

from .base_skill import BaseSkill


class TimeDateSkill(BaseSkill):
    name = "get_time_date"
    description = "Get the current time and/or date."
    input_schema = {"type": "object", "properties": {}, "required": []}

    def run(self) -> str:
        now = datetime.datetime.now()
        return now.strftime("It's %I:%M %p on %A, %B %d, %Y.")


class NoteSkill(BaseSkill):
    name = "manage_notes"
    description = "Save a note for the user, or read back all saved notes."
    input_schema = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["add", "read", "clear"]},
            "text": {"type": "string", "description": "Note content, required when action is 'add'."},
        },
        "required": ["action"],
    }

    def run(self, action: str, text: str = "") -> str:
        store = get_store()
        if action == "add":
            if not text.strip():
                return "There's nothing to save — what should the note say?"
            store.add_note(text.strip())
            return "Note saved."
        if action == "read":
            rows = store.list_notes()
            if not rows:
                return "You have no saved notes."
            lines = [
                f"[{datetime.datetime.fromtimestamp(r['ts']):%Y-%m-%d %H:%M}] {r['text']}"
                for r in rows
            ]
            return "\n".join(lines)
        if action == "clear":
            store.clear_notes()
            return "All notes cleared."
        return "Unknown note action."


class ReminderSkill(BaseSkill):
    name = "set_reminder"
    description = (
        "Set a one-off reminder that pops up a desktop notification after N "
        "minutes. Reminders are saved to disk, so they survive a restart and "
        "still fire if Jarvis was closed when they came due."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "message": {"type": "string", "description": "What to be reminded about."},
            "minutes": {"type": "number", "description": "Delay in minutes before reminding."},
        },
        "required": ["message", "minutes"],
    }

    def run(self, message: str, minutes: float) -> str:
        if not message.strip():
            return "What should I remind you about?"
        try:
            minutes = float(minutes)
        except (TypeError, ValueError):
            return "I need a number of minutes for the reminder."
        if minutes < 0:
            return "I can't set a reminder in the past."

        # Persisted, not a daemon threading.Timer — the old version lost every
        # pending reminder the moment the process exited, without saying so.
        get_store().add_reminder(message.strip(), time.time() + minutes * 60)
        when = "now" if minutes < 1 else f"in {minutes:g} minute(s)"
        return f"Reminder set for {when}."


class ListRemindersSkill(BaseSkill):
    name = "list_reminders"
    description = "List the user's reminders that haven't fired yet."
    input_schema = {"type": "object", "properties": {}, "required": []}

    def run(self) -> str:
        rows = get_store().pending_reminders()
        if not rows:
            return "You have no pending reminders."
        lines = [
            f"{datetime.datetime.fromtimestamp(r['fire_at']):%a %H:%M} — {r['message']}"
            for r in rows
        ]
        return "Pending reminders:\n" + "\n".join(lines)


class MemorySkill(BaseSkill):
    name = "memory"
    description = (
        "Store or look up durable facts about the user that should persist "
        "across sessions — preferences, hardware, names, recurring context. "
        "Use 'remember' when the user tells you something worth keeping "
        "('my monitor is a Dell U2720Q', 'I prefer metric'). Use 'recall' when "
        "answering would benefit from something you were told previously."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["remember", "recall", "forget"]},
            "text": {
                "type": "string",
                "description": (
                    "The fact to remember, or a search term for recall/forget. "
                    "Omit on recall to list everything."
                ),
            },
        },
        "required": ["action"],
    }

    def run(self, action: str, text: str = "") -> str:
        store = get_store()

        if action == "remember":
            if not text.strip():
                return "What should I remember?"
            if store.remember(text.strip()):
                return "Noted — I'll remember that."
            return "I already knew that."

        if action == "recall":
            facts = store.recall(text.strip())
            if not facts:
                return (
                    f"I don't have anything stored about '{text}'."
                    if text.strip()
                    else "I haven't been told anything to remember yet."
                )
            return "Here's what I remember:\n" + "\n".join(f"- {f}" for f in facts)

        if action == "forget":
            if not text.strip():
                return "What should I forget? I won't clear everything without a specific request."
            removed = store.forget(text.strip())
            return f"Forgot {removed} item(s)." if removed else "I had nothing matching that."

        return "Unknown memory action."


# Safe arithmetic evaluator (no eval()) for the calculator skill
_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.Mod: operator.mod,
}


_MAX_EXPONENT = 1000
_MAX_POW_BASE = 1_000_000


def _safe_eval(node):
    if isinstance(node, ast.Constant):
        if not isinstance(node.value, (int, float)):
            raise ValueError("Only numbers are allowed")
        return node.value
    if isinstance(node, ast.BinOp):
        op = _OPS.get(type(node.op))
        if op is None:
            raise ValueError("Unsupported operator")
        left, right = _safe_eval(node.left), _safe_eval(node.right)
        # Keep both operands bounded. Huge bases can create massive responses
        # even with a small exponent, while huge exponents can hang the process.
        if isinstance(node.op, ast.Pow):
            if abs(left) > _MAX_POW_BASE:
                raise ValueError("Base too large")
            if abs(right) > _MAX_EXPONENT:
                raise ValueError("Exponent too large")
        return op(left, right)
    if isinstance(node, ast.UnaryOp):
        op = _OPS.get(type(node.op))
        if op is None:
            raise ValueError("Unsupported operator")
        return op(_safe_eval(node.operand))
    raise ValueError("Unsupported expression")


class CalculatorSkill(BaseSkill):
    name = "calculate"
    description = "Evaluate a math expression, e.g. '12 * (3 + 4) / 2'."
    input_schema = {
        "type": "object",
        "properties": {"expression": {"type": "string"}},
        "required": ["expression"],
    }

    def run(self, expression: str) -> str:
        try:
            result = _safe_eval(ast.parse(expression, mode="eval").body)
        except ZeroDivisionError:
            return "That divides by zero."
        except (ValueError, SyntaxError, KeyError) as e:
            return f"I couldn't evaluate '{expression}': {e}"
        return f"{expression} = {result}"


# Longer clipboard contents get truncated rather than dumped into context.
_CLIPBOARD_LIMIT = 20000


class ClipboardSkill(BaseSkill):
    name = "clipboard"
    description = (
        "Read what's currently on the user's clipboard, or copy text onto it. "
        "Pairs well with see_screen — the user can copy an error message and "
        "ask about it, or ask you to put a result on the clipboard to paste."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["read", "write"]},
            "text": {
                "type": "string",
                "description": "Text to copy. Required when action is 'write'.",
            },
        },
        "required": ["action"],
    }

    def run(self, action: str, text: str = "") -> str:
        try:
            import pyperclip
        except ImportError:
            return "Clipboard access needs the 'pyperclip' package. Run: pip install pyperclip"

        if action == "read":
            try:
                content = pyperclip.paste()
            except Exception as e:
                return f"I couldn't read the clipboard: {e}"
            if not content:
                return "The clipboard is empty."
            if len(content) > _CLIPBOARD_LIMIT:
                return (
                    f"Clipboard contents (truncated to {_CLIPBOARD_LIMIT} of "
                    f"{len(content)} characters):\n{content[:_CLIPBOARD_LIMIT]}"
                )
            return f"Clipboard contents:\n{content}"

        if action == "write":
            if not text:
                return "There's nothing to copy — what should I put on the clipboard?"
            try:
                pyperclip.copy(text)
            except Exception as e:
                return f"I couldn't write to the clipboard: {e}"
            return "Copied to the clipboard."

        return "Unknown clipboard action."
