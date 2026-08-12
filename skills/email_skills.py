"""Email and calendar skills, backed by core.email_providers.

Each account needs one interactive /connect (main.py) before these can do
anything — OAuth needs a real browser and a human present, which a
model-driven tool call can't provide, so connecting is deliberately not a
skill itself.
"""
from core.email_providers import (
    ProviderError,
    default_account,
    get_provider,
    google_ready,
    microsoft_ready,
)
from core.risk import Risk
from core.undo import get_journal

from .base_skill import BaseSkill

_ACCOUNT_SCHEMA = {
    "type": "string",
    "enum": ["google", "microsoft"],
    "description": "Which connected account to use. Omit if only one is connected.",
}


def _resolve_account(account: str = "") -> tuple[str | None, str | None]:
    """Return (account_name, error_message) — exactly one is set. Never
    guesses when more than one account is connected; asks instead."""
    if account:
        account = account.strip().lower()
        if account not in ("google", "microsoft"):
            return None, f"'{account}' isn't an account I know — use 'google' or 'microsoft'."
        ready = google_ready() if account == "google" else microsoft_ready()
        if not ready:
            return None, f"The {account} account isn't connected yet. Run /connect {account} first."
        return account, None

    resolved = default_account()
    if resolved is not None:
        return resolved, None
    if not google_ready() and not microsoft_ready():
        return None, "No email account is connected yet. Run /connect google or /connect microsoft first."
    return None, "Both Google and Microsoft are connected — say which account to use."


class ReadEmailSkill(BaseSkill):
    name = "read_email"
    description = (
        "Read the user's most recent emails: subject, sender, date, and a "
        "short preview. Use 'account' to pick google or microsoft if both "
        "are connected."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "count": {"type": "integer", "description": "How many recent emails. Default 5."},
            "account": _ACCOUNT_SCHEMA,
        },
        "required": [],
    }
    risk = Risk.SAFE

    def run(self, count: int = 5, account: str = "") -> str:
        resolved, error = _resolve_account(account)
        if error:
            return error
        try:
            messages = get_provider(resolved).list_recent_mail(max(1, min(int(count), 25)))
        except ProviderError as e:
            return str(e)
        if not messages:
            return "No recent emails."
        lines = [
            f"- From {m['from']} — \"{m['subject']}\" ({m['date']}): {m['snippet']}"
            for m in messages
        ]
        return f"{len(messages)} recent email(s):\n" + "\n".join(lines)


class SendEmailSkill(BaseSkill):
    name = "send_email"
    description = (
        "Send an email from the user's connected account. Externally visible "
        "and cannot be undone, so it always asks for confirmation first."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "to": {"type": "string", "description": "Recipient email address."},
            "subject": {"type": "string"},
            "body": {"type": "string"},
            "account": _ACCOUNT_SCHEMA,
        },
        "required": ["to", "subject", "body"],
    }
    risk = Risk.DANGEROUS

    def consequence(self, to: str = "", subject: str = "", **_) -> str:
        return f"Send an email to {to} with subject '{subject}'?"

    def run(self, to: str, subject: str, body: str, account: str = "") -> str:
        resolved, error = _resolve_account(account)
        if error:
            return error
        try:
            get_provider(resolved).send_mail(to, subject, body)
        except ProviderError as e:
            return str(e)
        return f"Sent to {to}."


class ListEventsSkill(BaseSkill):
    name = "list_events"
    description = "List the user's upcoming calendar events."
    input_schema = {
        "type": "object",
        "properties": {
            "days_ahead": {
                "type": "integer",
                "description": "How many days ahead to look. Default 7.",
            },
            "account": _ACCOUNT_SCHEMA,
        },
        "required": [],
    }
    risk = Risk.SAFE

    def run(self, days_ahead: int = 7, account: str = "") -> str:
        resolved, error = _resolve_account(account)
        if error:
            return error
        try:
            events = get_provider(resolved).list_events(max(1, int(days_ahead)))
        except ProviderError as e:
            return str(e)
        if not events:
            return f"Nothing on the calendar in the next {days_ahead} day(s)."
        lines = [f"- {e['title']}: {e['start']} to {e['end']}" for e in events]
        return f"{len(events)} upcoming event(s):\n" + "\n".join(lines)


class CreateEventSkill(BaseSkill):
    name = "create_event"
    description = (
        "Create a calendar event. start/end are ISO 8601 datetimes, e.g. "
        "'2026-08-13T14:00:00'. Reversible — can be undone right after "
        "creating it."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "start": {"type": "string", "description": "ISO 8601 datetime, e.g. 2026-08-13T14:00:00."},
            "end": {"type": "string", "description": "ISO 8601 datetime, e.g. 2026-08-13T15:00:00."},
            "description": {"type": "string"},
            "account": _ACCOUNT_SCHEMA,
        },
        "required": ["title", "start", "end"],
    }
    risk = Risk.MODERATE

    def consequence(self, title: str = "", start: str = "", **_) -> str:
        return f"Create the event '{title}' at {start}?"

    def run(self, title: str, start: str, end: str, description: str = "", account: str = "") -> str:
        resolved, error = _resolve_account(account)
        if error:
            return error
        try:
            event_id = get_provider(resolved).create_event(title, start, end, description)
        except ProviderError as e:
            return str(e)

        def undo(acct=resolved, eid=event_id, t=title):
            get_provider(acct).delete_event(eid)
            return f"Removed '{t}' from the calendar."

        get_journal().record(f"Delete the event '{title}'", undo)
        return f"Created '{title}' from {start} to {end}."


class DeleteEventSkill(BaseSkill):
    name = "delete_event"
    description = "Delete a calendar event by its ID (from list_events). Cannot be undone."
    input_schema = {
        "type": "object",
        "properties": {
            "event_id": {"type": "string"},
            "account": _ACCOUNT_SCHEMA,
        },
        "required": ["event_id"],
    }
    risk = Risk.DANGEROUS

    def consequence(self, event_id: str = "", **_) -> str:  # noqa: ARG002
        return "Delete that calendar event? This can't be undone."

    def run(self, event_id: str, account: str = "") -> str:
        resolved, error = _resolve_account(account)
        if error:
            return error
        try:
            get_provider(resolved).delete_event(event_id)
        except ProviderError as e:
            return str(e)
        return "Event deleted."
