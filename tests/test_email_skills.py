"""Tests for skills/email_skills.py.

No real OAuth or network: google_ready/microsoft_ready/default_account/
get_provider are all mocked at the skills.email_skills module level, so these
run without the email packages installed or any account connected.
"""
import pytest

from core.risk import Risk
from core.undo import UndoJournal, get_journal, set_journal
from skills.email_skills import (
    CreateEventSkill,
    DeleteEventSkill,
    ListEventsSkill,
    ReadEmailSkill,
    SendEmailSkill,
)


class _FakeProvider:
    def __init__(self):
        self.sent = []
        self.created = []
        self.deleted = []

    def list_recent_mail(self, n):
        return [{"from": "a@x.com", "subject": "Hi", "date": "today", "snippet": "hello"}][:n]

    def send_mail(self, to, subject, body):
        self.sent.append((to, subject, body))

    def list_events(self, days_ahead):  # noqa: ARG002
        return [
            {
                "id": "e1",
                "title": "Standup",
                "start": "2026-08-13T09:00:00",
                "end": "2026-08-13T09:15:00",
            }
        ]

    def create_event(self, title, start, end, description=""):  # noqa: ARG002
        self.created.append((title, start, end))
        return "new-id"

    def delete_event(self, event_id):
        self.deleted.append(event_id)


@pytest.fixture
def journal():
    j = UndoJournal(max_age_seconds=900)
    set_journal(j)
    yield j
    set_journal(None)


@pytest.fixture
def fake_provider(monkeypatch):
    provider = _FakeProvider()
    monkeypatch.setattr("skills.email_skills.get_provider", lambda account: provider)  # noqa: ARG005
    monkeypatch.setattr("skills.email_skills.google_ready", lambda: True)
    monkeypatch.setattr("skills.email_skills.microsoft_ready", lambda: False)
    monkeypatch.setattr("skills.email_skills.default_account", lambda: "google")
    return provider


# -- registration gating (default: nothing configured in this test env) -----

def test_email_skills_not_registered_without_any_account_configured():
    from skills.skill_manager import SkillManager

    names = set(SkillManager().skills)
    email_skills = {"read_email", "send_email", "list_events", "create_event", "delete_event"}
    assert not (email_skills & names)


# -- account resolution -------------------------------------------------------

def test_no_account_connected_is_a_message_not_a_crash(monkeypatch):
    monkeypatch.setattr("skills.email_skills.google_ready", lambda: False)
    monkeypatch.setattr("skills.email_skills.microsoft_ready", lambda: False)
    monkeypatch.setattr("skills.email_skills.default_account", lambda: None)

    assert "/connect" in ReadEmailSkill().run()


def test_ambiguous_accounts_asks_rather_than_guessing(monkeypatch):
    monkeypatch.setattr("skills.email_skills.google_ready", lambda: True)
    monkeypatch.setattr("skills.email_skills.microsoft_ready", lambda: True)
    monkeypatch.setattr("skills.email_skills.default_account", lambda: None)

    assert "which account" in ReadEmailSkill().run().lower()


def test_unknown_account_name_is_rejected(fake_provider):  # noqa: ARG001
    assert "yahoo" in ReadEmailSkill().run(account="yahoo")


def test_specifying_an_unconnected_account_is_a_message(monkeypatch):
    monkeypatch.setattr("skills.email_skills.google_ready", lambda: False)
    monkeypatch.setattr("skills.email_skills.microsoft_ready", lambda: True)
    monkeypatch.setattr("skills.email_skills.default_account", lambda: "microsoft")

    assert "/connect google" in ReadEmailSkill().run(account="google")


# -- read_email ---------------------------------------------------------------

def test_read_email_lists_recent_messages(fake_provider):  # noqa: ARG001
    out = ReadEmailSkill().run(count=3)
    assert "Hi" in out
    assert "a@x.com" in out


def test_read_email_is_safe_tier():
    assert ReadEmailSkill().risk_for() == Risk.SAFE


# -- send_email ----------------------------------------------------------------

def test_send_email_is_dangerous_tier():
    assert SendEmailSkill().risk_for(to="x@y.com", subject="s", body="b") == Risk.DANGEROUS


def test_send_email_delegates_to_the_provider(fake_provider):
    out = SendEmailSkill().run(to="friend@x.com", subject="hi", body="hello there")
    assert "Sent to friend@x.com" in out
    assert fake_provider.sent == [("friend@x.com", "hi", "hello there")]


def test_send_email_consequence_names_the_recipient_and_subject():
    out = SendEmailSkill().consequence(to="friend@x.com", subject="Lunch?")
    assert "friend@x.com" in out
    assert "Lunch?" in out


# -- list_events / create_event / delete_event --------------------------------

def test_list_events_reports_upcoming(fake_provider):  # noqa: ARG001
    assert "Standup" in ListEventsSkill().run()


def test_list_events_empty_is_a_plain_message(fake_provider):
    fake_provider.list_events = lambda days_ahead: []  # noqa: ARG005
    out = ListEventsSkill().run(days_ahead=3)
    assert "nothing" in out.lower()


def test_create_event_is_moderate_tier():
    assert CreateEventSkill().risk_for(title="t", start="s", end="e") == Risk.MODERATE


def test_create_event_records_an_undo_that_deletes_it(journal, fake_provider):  # noqa: ARG001
    out = CreateEventSkill().run(
        title="Dentist", start="2026-08-13T10:00:00", end="2026-08-13T10:30:00"
    )
    assert "Dentist" in out
    assert fake_provider.created == [("Dentist", "2026-08-13T10:00:00", "2026-08-13T10:30:00")]

    get_journal().undo(get_journal().latest().token)
    assert fake_provider.deleted == ["new-id"]


def test_delete_event_is_dangerous_tier():
    assert DeleteEventSkill().risk_for(event_id="e1") == Risk.DANGEROUS


def test_delete_event_delegates_to_the_provider(fake_provider):
    out = DeleteEventSkill().run(event_id="e1")
    assert "deleted" in out.lower()
    assert fake_provider.deleted == ["e1"]
