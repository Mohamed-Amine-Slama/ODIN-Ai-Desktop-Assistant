"""Tests for window management and synthetic input.

Both are Windows-only, so the OS calls are patched and the suite still runs
on Linux/WSL — matching how system_skills is already tested.
"""
import sys
from types import SimpleNamespace

import pytest

from core.risk import Risk
from core.undo import UndoJournal, get_journal, set_journal
from skills import screen_state
from skills.input_skills import ClickSkill, PressKeysSkill, ScrollSkill, TypeTextSkill
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


class _FailSafeException(Exception):
    pass


@pytest.fixture
def gui(monkeypatch):
    calls = []
    fake = SimpleNamespace(
        FAILSAFE=True,
        FailSafeException=_FailSafeException,
        size=lambda: (1920, 1080),
        write=lambda text, interval=0: calls.append(("write", text)),
        hotkey=lambda *keys: calls.append(("hotkey", keys)),
        click=lambda x=None, y=None, button="left", clicks=1: calls.append(
            ("click", x, y, button, clicks)
        ),
        press=lambda key: calls.append(("press", key)),
        scroll=lambda amount, x=None, y=None: calls.append(("scroll", amount, x, y)),
    )
    monkeypatch.setitem(sys.modules, "pyautogui", fake)
    # Click/scroll bounds-check against the real virtual-desktop size on
    # Windows (skills.input_skills._virtual_screen_bounds) — pinned here to
    # a generous fixed box so the coordinate math in these tests (which
    # deliberately includes an off-primary-monitor origin) isn't at the
    # mercy of whatever monitor layout the machine running the suite
    # actually has. Bounds-rejection itself gets its own tests below, with
    # this patched to a deliberately small box instead.
    monkeypatch.setattr(
        "skills.input_skills._virtual_screen_bounds",
        lambda gui: (-100_000, -100_000, 100_000, 100_000),
    )
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


def test_close_window_confirmation_shows_the_resolved_title(windows):
    """A short substring that happens to uniquely match must be confirmed
    against the real window it resolves to, not echoed back verbatim — the
    user has to know what they're actually agreeing to close."""
    out = CloseWindowSkill().consequence(title="spot")
    assert "Spotify" in out
    assert "'spot'" not in out


def test_close_window_confirmation_falls_back_to_the_raw_title_when_unresolved(windows):
    """No match / an ambiguous match can't be resolved yet at confirm time —
    must not raise, and should still show something sensible."""
    out = CloseWindowSkill().consequence(title="nonexistent app")
    assert "nonexistent app" in out


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


def test_press_keys_handles_a_trailing_literal_plus(gui):
    """'+' is both the delimiter and a valid key (e.g. ctrl++ to zoom in) —
    a plain split() silently drops the trailing literal +."""
    out = PressKeysSkill().run(keys="ctrl++")
    assert ("hotkey", ("ctrl", "+")) in gui
    assert "ctrl+++" not in out  # i.e. it wasn't just echoed back unparsed


def test_press_keys_handles_the_bare_plus_key(gui):
    PressKeysSkill().run(keys="+")
    assert ("hotkey", ("+",)) in gui


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


# -- coordinate mapping (see_screen -> click/scroll) ------------------------
#
# see_screen may downscale its capture (skills/vision_skills.MAX_EDGE); the
# model reads coordinates straight off that image with no idea it was scaled.
# screen_state.record/to_real is what maps those image-space coordinates back
# onto the real screen, and this is the fix for clicks silently landing on
# the wrong element while still reporting success.

def test_click_is_unscaled_with_no_screenshot_recorded(gui):
    """Identity mapping when see_screen was never called this session —
    matches the old, pre-mapping behaviour exactly."""
    ClickSkill().run(x=100, y=250, button="right", clicks=2)
    assert ("click", 100, 250, "right", 2) in gui


def test_click_maps_through_a_downscaled_screenshot(gui):
    """A screenshot taken at half real resolution: a click at image (100, 50)
    must land at real screen (200, 100), not at (100, 50)."""
    screen_state.record(scale=2.0, origin_x=0, origin_y=0)
    ClickSkill().run(x=100, y=50)
    assert ("click", 200, 100, "left", 1) in gui


def test_click_maps_through_a_non_primary_monitor_origin(gui):
    """A capture whose origin isn't (0, 0) — a second monitor, or an active
    window not anchored at the screen origin — must offset, not just scale."""
    screen_state.record(scale=1.0, origin_x=1920, origin_y=100)
    ClickSkill().run(x=10, y=20)
    assert ("click", 1930, 120, "left", 1) in gui


def test_scroll_passes_amount(gui):
    ScrollSkill().run(amount=-3)
    assert ("scroll", -3, None, None) in gui


def test_scroll_maps_position_through_the_last_screenshot(gui):
    screen_state.record(scale=2.0, origin_x=0, origin_y=0)
    ScrollSkill().run(amount=5, x=100, y=50)
    assert ("scroll", 5, 200, 100) in gui


def test_scroll_is_moderate_and_irreversible(gui, journal):
    assert ScrollSkill().risk_for(amount=1) == Risk.MODERATE
    ScrollSkill().run(amount=1)
    assert get_journal().latest() is None


def test_click_refuses_a_stale_screenshot_mapping(gui, monkeypatch):
    """A click read off a screenshot from long enough ago that the screen
    may no longer match it must be refused, not silently aimed at whatever
    now occupies that stale coordinate."""
    screen_state.record(scale=1.0, origin_x=0, origin_y=0)
    monkeypatch.setattr(screen_state, "is_stale", lambda: True)
    out = ClickSkill().run(x=100, y=50)
    assert "old" in out.lower()
    assert gui == []


def test_scroll_refuses_a_stale_screenshot_mapping(gui, monkeypatch):
    screen_state.record(scale=1.0, origin_x=0, origin_y=0)
    monkeypatch.setattr(screen_state, "is_stale", lambda: True)
    out = ScrollSkill().run(amount=1, x=100, y=50)
    assert "old" in out.lower()
    assert gui == []


def test_scroll_without_coordinates_ignores_staleness(gui, monkeypatch):
    """Staleness only matters for a *positioned* scroll — one with no x/y
    doesn't touch the mapping at all."""
    monkeypatch.setattr(screen_state, "is_stale", lambda: True)
    ScrollSkill().run(amount=1)
    assert ("scroll", 1, None, None) in gui


def test_click_rejects_coordinates_outside_the_virtual_screen(gui, monkeypatch):
    monkeypatch.setattr(
        "skills.input_skills._virtual_screen_bounds", lambda gui: (0, 0, 1920, 1080)
    )
    out = ClickSkill().run(x=5000, y=5000)
    assert "outside the current screen" in out
    assert gui == []


def test_scroll_rejects_coordinates_outside_the_virtual_screen(gui, monkeypatch):
    monkeypatch.setattr(
        "skills.input_skills._virtual_screen_bounds", lambda gui: (0, 0, 1920, 1080)
    )
    out = ScrollSkill().run(amount=1, x=5000, y=5000)
    assert "outside the current screen" in out
    assert gui == []


def test_click_reports_a_clean_message_when_the_failsafe_fires(gui, monkeypatch):
    def _raise(*a, **k):
        raise sys.modules["pyautogui"].FailSafeException("boom")

    monkeypatch.setattr(sys.modules["pyautogui"], "click", _raise)
    out = ClickSkill().run(x=1, y=1)
    assert "failsafe" in out.lower()


def test_scroll_reports_a_clean_message_when_the_failsafe_fires(gui, monkeypatch):
    def _raise(*a, **k):
        raise sys.modules["pyautogui"].FailSafeException("boom")

    monkeypatch.setattr(sys.modules["pyautogui"], "scroll", _raise)
    out = ScrollSkill().run(amount=1)
    assert "failsafe" in out.lower()


def test_virtual_screen_bounds_covers_a_monitor_left_of_the_primary(monkeypatch):
    """pyautogui.size() alone only ever reports the primary monitor — a
    real second monitor placed to the left of it sits at negative
    coordinates, which a naive (0, 0, width, height) box would wrongly
    reject. On non-Windows this falls back to gui.size() at origin (0, 0)."""
    from skills.input_skills import _virtual_screen_bounds

    monkeypatch.setattr("skills.input_skills.IS_WINDOWS", False)
    fake_gui = SimpleNamespace(size=lambda: (1920, 1080))
    assert _virtual_screen_bounds(fake_gui) == (0, 0, 1920, 1080)
