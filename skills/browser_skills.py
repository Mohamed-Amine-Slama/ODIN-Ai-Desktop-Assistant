"""Browser automation tools — driving a web page by element, not by pixel.

These are the fast path for anything happening inside a website. The vision
path (see_screen + click at coordinates) still exists and still works on the
same window, because the browser this drives is a real, visible one; reach for
it only when a page draws something the accessibility tree can't describe.

Risk tiers match skills/input_skills.py exactly, and for the same reason:
browser_click / browser_type / browser_scroll do to a web page precisely what
click / type_text / scroll do to the screen. You cannot un-send a message, so
none of them record an undo entry, and all three say so in their descriptions.

browser_close is never gated, whatever CONFIRM_DESTRUCTIVE says — same rule as
hand_control's stop. The one thing this feature must never do is refuse to
shut itself off.
"""
from core.browser import get_browser_controller
from core.risk import Risk

from .base_skill import BaseSkill
from .web_skills import OpenWebsiteSkill, _to_web_url


class BrowserNavigateSkill(BaseSkill):
    name = "browser_navigate"
    description = (
        "Open a page in the automation browser and list what's on it. Use this "
        "instead of open_website whenever you need to DO something on the site "
        "(search it, click through it, fill something in) rather than just show "
        "it to the user. Returns the page's interactive elements, each with a "
        "ref you pass to browser_click / browser_type / browser_scroll."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "URL or well-known site name, e.g. 'instagram' or 'https://news.ycombinator.com'.",
            }
        },
        "required": ["url"],
    }
    risk = Risk.SAFE

    def run(self, url: str) -> str:
        # Same name resolution as open_website, so "instagram" means the same
        # thing to both tools — and the same scheme check, which matters more
        # here: page.goto() would happily follow a 'file:///' a model made up.
        key = (url or "").strip().lower()
        raw = OpenWebsiteSkill.KNOWN.get(key, (url or "").strip())
        resolved = _to_web_url(raw)
        if resolved is None:
            return f"'{url}' isn't a web address I can open."
        return get_browser_controller().navigate(resolved)


class BrowserReadSkill(BaseSkill):
    name = "browser_read"
    description = (
        "List the interactive elements on the page the automation browser is "
        "showing, each with a ref. Call this after every click or typed entry "
        "to see what changed — refs from an older read stop working once the "
        "page moves on."
    )
    input_schema = {"type": "object", "properties": {}, "required": []}
    risk = Risk.SAFE

    def run(self) -> str:
        return get_browser_controller().read()


class BrowserClickSkill(BaseSkill):
    name = "browser_click"
    description = (
        "Click an element in the automation browser, by the ref browser_read "
        "gave you. Cannot be undone."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "ref": {
                "type": "string",
                "description": "Element ref from the most recent browser_read, e.g. '3:12'.",
            }
        },
        "required": ["ref"],
    }
    risk = Risk.MODERATE

    def consequence(self, ref: str = "", **_) -> str:
        return f"Click element {ref} in the browser?"

    def run(self, ref: str) -> str:
        return get_browser_controller().click(ref)


class BrowserTypeSkill(BaseSkill):
    name = "browser_type"
    description = (
        "Type into a text box in the automation browser, by the ref "
        "browser_read gave you. Set submit to press Enter afterwards — that "
        "sends a message or runs a search in one call instead of two. Cannot "
        "be undone."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "ref": {
                "type": "string",
                "description": "Element ref from the most recent browser_read, e.g. '3:12'.",
            },
            "text": {"type": "string", "description": "The text to type."},
            "submit": {
                "type": "boolean",
                "description": "Press Enter after typing. Defaults to false.",
            },
        },
        "required": ["ref", "text"],
    }
    risk = Risk.MODERATE

    def consequence(self, ref: str = "", text: str = "", submit: bool = False, **_) -> str:
        preview = text if len(text) <= 60 else text[:60] + "..."
        ending = " and press Enter" if submit else ""
        return f"Type this into browser element {ref}{ending}?\n    {preview}"

    def run(self, ref: str, text: str, submit: bool = False) -> str:
        return get_browser_controller().type_text(ref, text, submit=bool(submit))


class BrowserScrollSkill(BaseSkill):
    name = "browser_scroll"
    description = (
        "Scroll the automation browser. Pass a ref to scroll the panel that "
        "element sits in — chat and feed apps scroll an inner panel, not the "
        "page, so scrolling a conversation or contact list needs one. Cannot "
        "be undone."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "amount": {
                "type": "integer",
                "description": "Pixels to scroll. Positive scrolls down, negative up.",
            },
            "ref": {
                "type": "string",
                "description": (
                    "Optional element ref to scroll near, from the most recent "
                    "browser_read. Without it the page itself scrolls."
                ),
            },
        },
        "required": ["amount"],
    }
    risk = Risk.MODERATE

    def consequence(self, amount: int = 0, ref: str = "", **_) -> str:
        where = f" near element {ref}" if ref else ""
        return f"Scroll the browser {abs(int(amount))}px {'down' if amount >= 0 else 'up'}{where}?"

    def run(self, amount: int, ref: str = "") -> str:
        return get_browser_controller().scroll(int(amount), ref=ref or "")


class BrowserCloseSkill(BaseSkill):
    name = "browser_close"
    description = (
        "Close the automation browser. The user stays logged in — the profile "
        "is kept, so reopening doesn't ask them to sign in again."
    )
    input_schema = {"type": "object", "properties": {}, "required": []}
    risk = Risk.SAFE

    def run(self) -> str:
        return get_browser_controller().close()
