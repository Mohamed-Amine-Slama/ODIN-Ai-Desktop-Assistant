"""Tests for window management and synthetic input.

Both are Windows-only, so the OS calls are patched and the suite still runs
on Linux/WSL — matching how system_skills is already tested.
"""
import sys
from types import SimpleNamespace

import pytest

from core.risk import Risk
from core.undo import UndoJournal, get_journal, set_journal
from skills.input_skills import ClickSkill, PressKeysSkill, TypeTextSkill
from skills.window_skills import (
    CloseWindowSkill, FocusWindowSkill, ListWindowsSkill, SetWindowStateSkill,
)


@pytest.fixture
def journal():
    j = UndoJournal(max_age_seconds=900)
    set_journal(j)
    yield j
    set_journal(None)


@pytest.fixture
def windows(monkeypatch):
    listing = [(101, "Chrome — Gmail"), (102, "Visual Studio Code"), (103, "Spotify")]
    monkeypatch.setattr("skills.window_skills.enumerate_windows", lambda: listing)
    monkeypatch.setattr("skills.window_skills.IS_WINDOWS", True)
    return listing


@pytest.fixture
def gui(monkeypatch):
    calls = []
    fake = SimpleNamespace(
        FAILSAFE=True,
        write=lambda text, interval=0: calls.append(("write", text)),
        hotkey=lambda *keys: calls.append(("hotkey", keys)),
        click=lambda x=None, y=None, button="left", clicks=1: calls.append(
            ("click", x, y, button, clicks)
        ),
        press=lambda key: calls.append(("press", key)),
    )
    monkeypatch.setitem(sys.modules, "pyautogui", fake)
    return calls


def test_list_windows_is_safe(windows):
    assert ListWindowsSkill().risk_for() == Risk.SAFE
    out = ListWindowsSkill().run()
    assert "Spotify" in out and "Visual Studio Code" in out


def test_focus_is_moderate_close_is_dangerous(windows):
    assert FocusWindowSkill().risk_for(title="spotify") == Risk.MODERATE
    assert CloseWindowSkill().risk_for(title="spotify") == Risk.DANGEROUS


def test_ambiguous_title_asks_rather_than_guessing(windows, monkeypatch, journal):
    """Two matches must not be resolved by picking the first one."""
    monkeypatch.setattr(
        "skills.window_skills.enumerate_windows",
        lambda: [(1, "Chrome — Gmail"), (2, "Chrome — Docs")],
    )
    out = FocusWindowSkill().run(title="chrome")
    assert "more than one" in out.lower()
    assert "Gmail" in out and "Docs" in out


def test_no_match_is_reported(windows):
    assert "couldn't find" in FocusWindowSkill().run(title="photoshop").lower()


def test_focus_records_an_undo_that_refocuses_the_previous_window(windows, monkeypatch, journal):
    focused = []
    monkeypatch.setattr("skills.window_skills.foreground_handle", lambda: 101)
    monkeypatch.setattr("skills.window_skills.focus_handle", lambda h: focused.append(h))

    FocusWindowSkill().run(title="spotify")
    assert focused == [103]

    get_journal().undo(get_journal().latest().token)
    assert focused == [103, 101], "undo should restore the previously focused window"


def test_close_window_records_no_undo(windows, monkeypatch, journal):
    """Closing may discard unsaved work and cannot be reversed."""
    monkeypatch.setattr("skills.window_skills.close_handle", lambda h: None)
    CloseWindowSkill().run(title="spotify")
    assert get_journal().latest() is None


def test_set_window_state_records_undo(windows, monkeypatch, journal):
    states = []
    monkeypatch.setattr("skills.window_skills.window_state", lambda h: "normal")
    monkeypatch.setattr("skills.window_skills.set_state", lambda h, s: states.append((h, s)))

    SetWindowStateSkill().run(title="spotify", state="minimized")
    assert states == [(103, "minimized")]

    get_journal().undo(get_journal().latest().token)
    assert states[-1] == (103, "normal")


def test_input_skills_are_moderate():
    assert TypeTextSkill().risk_for(text="hi") == Risk.MODERATE
    assert PressKeysSkill().risk_for(keys="ctrl+s") == Risk.MODERATE
    assert ClickSkill().risk_for(x=1, y=2) == Risk.MODERATE


def test_type_text(gui):
    out = TypeTextSkill().run(text="hello there")
    assert ("write", "hello there") in gui
    assert "11 characters" in out


def test_press_keys_splits_a_combo(gui):
    PressKeysSkill().run(keys="ctrl+shift+s")
    assert ("hotkey", ("ctrl", "shift", "s")) in gui


def test_click_passes_coordinates(gui):
    ClickSkill().run(x=100, y=250, button="right", clicks=2)
    assert ("click", 100, 250, "right", 2) in gui


def test_input_skills_never_record_undo(gui, journal):
    """Typing and clicking cannot be reversed, so no undo may be offered."""
    TypeTextSkill().run(text="x")
    PressKeysSkill().run(keys="enter")
    ClickSkill().run(x=1, y=1)
    assert get_journal().latest() is None


def test_failsafe_stays_enabled(gui):
    """Slamming the mouse into a screen corner aborts an automation. That is a
    real safety feature and must not be disabled."""
    TypeTextSkill().run(text="x")
    assert sys.modules["pyautogui"].FAILSAFE is True


def test_missing_dependency_is_a_message(monkeypatch):
    monkeypatch.setitem(sys.modules, "pyautogui", None)
    out = TypeTextSkill().run(text="x")
    assert "pyautogui" in out
