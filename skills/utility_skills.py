"""General utility skills."""
import datetime
import threading
import ast
import operator
from .base_skill import BaseSkill
import config

try:
    from plyer import notification
except Exception:
    notification = None


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
        config.ensure_dirs()
        if action == "add":
            if not text.strip():
                return "There's nothing to save — what should the note say?"
            with open(config.NOTES_FILE, "a", encoding="utf-8") as f:
                stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
                f.write(f"[{stamp}] {text}\n")
            return "Note saved."
        if action == "read":
            try:
                with open(config.NOTES_FILE, "r", encoding="utf-8") as f:
                    content = f.read().strip()
                return content if content else "You have no saved notes."
            except FileNotFoundError:
                return "You have no saved notes."
        if action == "clear":
            open(config.NOTES_FILE, "w").close()
            return "All notes cleared."
        return "Unknown note action."


class ReminderSkill(BaseSkill):
    name = "set_reminder"
    description = "Set a one-off reminder that pops up a desktop notification after N minutes."
    input_schema = {
        "type": "object",
        "properties": {
            "message": {"type": "string", "description": "What to be reminded about."},
            "minutes": {"type": "number", "description": "Delay in minutes before reminding."},
        },
        "required": ["message", "minutes"],
    }

    def run(self, message: str, minutes: float) -> str:
        def fire():
            if notification:
                notification.notify(
                    title=f"{config.ASSISTANT_NAME} reminder", message=message, timeout=15
                )
            else:
                print(f"\n[REMINDER] {message}\n")

        timer = threading.Timer(minutes * 60, fire)
        timer.daemon = True
        timer.start()
        return f"Reminder set for {minutes} minute(s) from now."


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
        # 2 ** 999999999 will hang the assistant computing a number nobody
        # asked for, so cap the exponent rather than letting it run.
        if isinstance(node.op, ast.Pow) and abs(right) > _MAX_EXPONENT:
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
