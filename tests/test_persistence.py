"""Tests for the SQLite store, durable reminders, and memory skills."""
import time

import pytest

from conftest import response, text_block, tool_use_block
from core import memory_index
from core.scheduler import ReminderScheduler
from core.store import Store, set_store
from skills.utility_skills import (
    ListRemindersSkill,
    MemorySkill,
    NoteSkill,
    ReminderSkill,
)


@pytest.fixture
def store(tmp_path, monkeypatch):
    """A real Store on a temp file, installed as the process-wide singleton."""
    import config

    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path), raising=False)
    monkeypatch.setattr(config, "NOTES_FILE", str(tmp_path / "notes.txt"), raising=False)
    s = Store(str(tmp_path / "test.db"))
    set_store(s)
    yield s
    set_store(None)
    s.close()


# -- notes -----------------------------------------------------------------

def test_notes_roundtrip(store):
    skill = NoteSkill()
    assert "saved" in skill.run(action="add", text="buy milk")
    assert "buy milk" in skill.run(action="read")
    skill.run(action="clear")
    assert "no saved notes" in skill.run(action="read")


def test_notes_survive_a_new_store(store, tmp_path):
    NoteSkill().run(action="add", text="persisted note")
    reopened = Store(str(tmp_path / "test.db"))
    try:
        assert any("persisted note" in r["text"] for r in reopened.list_notes())
    finally:
        reopened.close()


def test_legacy_notes_file_is_migrated(tmp_path, monkeypatch):
    import config

    legacy = tmp_path / "notes.txt"
    legacy.write_text("[2026-01-01 10:00] an old note\n", encoding="utf-8")
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path), raising=False)
    monkeypatch.setattr(config, "NOTES_FILE", str(legacy), raising=False)

    s = Store(str(tmp_path / "m.db"))
    try:
        assert any("an old note" in r["text"] for r in s.list_notes())
        assert not legacy.exists(), "legacy file should be renamed after migration"
        assert (tmp_path / "notes.txt.migrated").exists()
    finally:
        s.close()


def test_migration_marker_prevents_reimport(tmp_path, monkeypatch):
    """A stale .migrated marker existing alongside notes.txt (e.g. a restored
    backup put the old file back) must stop re-migration on its own — the
    old code only skipped migration when os.rename succeeded, which silently
    re-imported (duplicated) every startup once that assumption broke."""
    import config

    legacy = tmp_path / "notes.txt"
    marker = tmp_path / "notes.txt.migrated"
    legacy.write_text("[2026-01-01 10:00] should not be reimported\n", encoding="utf-8")
    marker.write_text("", encoding="utf-8")
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path), raising=False)
    monkeypatch.setattr(config, "NOTES_FILE", str(legacy), raising=False)

    s = Store(str(tmp_path / "m.db"))
    try:
        assert s.list_notes() == []
        assert legacy.exists(), "left alone once already migrated, not re-consumed"
    finally:
        s.close()


# -- reminders -------------------------------------------------------------

def test_reminder_is_persisted_not_a_timer(store):
    """The old version used a daemon threading.Timer, which vanished on exit."""
    ReminderSkill().run(message="stretch", minutes=2)
    pending = store.pending_reminders()
    assert len(pending) == 1
    assert pending[0]["message"] == "stretch"


def test_reminder_survives_restart_and_fires_late(store, tmp_path):
    """The headline persistence test: set a reminder, 'restart', and it still
    fires because it came due while we were gone."""
    store.add_reminder("take a break", time.time() - 300)  # due 5 min ago

    reopened = Store(str(tmp_path / "test.db"))
    fired = []
    try:
        scheduler = ReminderScheduler(reopened, notify=fired.append)
        count = scheduler.fire_due()

        assert count == 1
        assert "take a break" in fired[0]
        assert "ago" in fired[0], "an overdue reminder should say how late it is"
        # And it must not fire a second time.
        assert scheduler.fire_due() == 0
    finally:
        reopened.close()


def test_future_reminders_do_not_fire_early(store):
    store.add_reminder("later", time.time() + 3600)
    fired = []
    assert ReminderScheduler(store, notify=fired.append).fire_due() == 0
    assert fired == []


def test_failed_notification_does_not_replay_forever(store):
    """A notification we can't deliver must still be marked fired, or it
    retries on every single poll."""
    store.add_reminder("boom", time.time() - 1)

    def broken(_):
        raise RuntimeError("no notification daemon")

    scheduler = ReminderScheduler(store, notify=broken)
    assert scheduler.fire_due() == 1
    assert scheduler.fire_due() == 0


def test_list_reminders(store):
    ReminderSkill().run(message="one", minutes=5)
    out = ListRemindersSkill().run()
    assert "one" in out


def test_reminder_rejects_bad_input(store):
    assert "number of minutes" in ReminderSkill().run(message="x", minutes="soon")
    assert "in the past" in ReminderSkill().run(message="x", minutes=-5)


# -- memory ----------------------------------------------------------------

def test_memory_remember_and_recall(store):
    skill = MemorySkill()
    assert "remember that" in skill.run(action="remember", text="monitor is a Dell U2720Q")
    assert "Dell U2720Q" in skill.run(action="recall", text="monitor")


def test_memory_survives_restart(store, tmp_path):
    MemorySkill().run(action="remember", text="prefers metric units")
    reopened = Store(str(tmp_path / "test.db"))
    try:
        assert "prefers metric units" in reopened.recall("metric")
    finally:
        reopened.close()


def test_memory_deduplicates(store):
    skill = MemorySkill()
    skill.run(action="remember", text="likes tea")
    assert "already knew" in skill.run(action="remember", text="likes tea")


def test_memory_forget_requires_a_target(store):
    """Guard against wiping everything on a vague 'forget it'."""
    MemorySkill().run(action="remember", text="something")
    assert "won't clear everything" in MemorySkill().run(action="forget", text="")
    assert store.recall() == ["something"]


def test_memory_recall_when_empty(store):
    assert "haven't been told anything" in MemorySkill().run(action="recall")


def test_memory_recall_escapes_like_wildcards(store):
    """A literal '%' or '_' in a query must not act as a SQL LIKE wildcard —
    otherwise 'recall 50%' would also match completely unrelated memories."""
    skill = MemorySkill()
    skill.run(action="remember", text="uses 50% battery saver mode")
    skill.run(action="remember", text="uses xyz battery saver mode")

    out = skill.run(action="recall", text="50%")
    assert "50% battery" in out
    assert "xyz battery" not in out


def test_memory_forget_escapes_like_wildcards(store):
    skill = MemorySkill()
    skill.run(action="remember", text="50% off coupon code")
    skill.run(action="remember", text="xyz off coupon code")

    skill.run(action="forget", text="50%")
    assert store.recall() == ["xyz off coupon code"]


# -- semantic memory index ---------------------------------------------------
# core.memory_index degrades to no-ops when chromadb/sentence-transformers
# aren't installed (not required for these tests), so its calls are mocked
# here to test the store's wiring to it in isolation.

def test_remember_indexes_the_new_row(store, monkeypatch):
    calls = []
    monkeypatch.setattr(memory_index, "index", lambda mid, text: calls.append((mid, text)))

    MemorySkill().run(action="remember", text="likes dark roast coffee")

    assert len(calls) == 1
    memory_id, text = calls[0]
    assert isinstance(memory_id, int)
    assert text == "likes dark roast coffee"


def test_recall_prefers_semantic_results_when_available(store, monkeypatch):
    store.remember("has a Dell U2720Q monitor")
    monkeypatch.setattr(memory_index, "search", lambda query, limit=20: ["semantic hit"])

    assert store.recall("what display am I using") == ["semantic hit"]


def test_recall_falls_back_to_like_when_semantic_search_finds_nothing(store, monkeypatch):
    store.remember("has a Dell U2720Q monitor")
    monkeypatch.setattr(memory_index, "search", lambda query, limit=20: [])

    assert store.recall("Dell") == ["has a Dell U2720Q monitor"]


def test_recall_with_no_query_never_calls_semantic_search(store, monkeypatch):
    """Nothing to embed a similarity search against — listing everything must
    stay a plain LIKE-free scan, not silently return zero results."""
    store.remember("fact one")

    def boom(*a, **k):
        raise AssertionError("search() should not be called for an empty query")

    monkeypatch.setattr(memory_index, "search", boom)
    assert store.recall() == ["fact one"]


def test_forget_removes_matching_rows_from_the_index(store, monkeypatch):
    removed = []
    monkeypatch.setattr(memory_index, "remove", lambda mid: removed.append(mid))
    store.remember("temporary fact")

    store.forget("temporary")

    assert len(removed) == 1


# -- conversation persistence ---------------------------------------------

def test_conversation_is_saved_and_restored(store, make_brain):
    brain = make_brain([response([text_block("Hi there.")])], store=store)
    brain.ask("hello")

    fresh = make_brain([], store=store)
    assert fresh.load_history() == 2
    assert fresh.history[0]["content"] == "hello"


def test_only_committed_turns_are_persisted(store, make_brain):
    """A turn that blew up mid-flight must not leave a broken conversation on
    disk any more than it does in memory."""
    import openai

    brain = make_brain(
        [
            response([tool_use_block("get_time_date", {})], stop_reason="tool_use"),
            openai.APIConnectionError(request=None),
        ],
        store=store,
    )

    with pytest.raises(openai.APIConnectionError):
        brain.ask("what time is it")

    assert store.recent_messages() == []


def test_restore_never_starts_on_a_tool_result(store, make_brain):
    """Trimming to the last N messages could otherwise orphan a tool_result
    whose matching tool_use was cut, which the API rejects."""
    store.append_message("assistant", [{"type": "tool_use", "id": "t1", "name": "x", "input": {}}])
    store.append_message("user", [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}])
    store.append_message("assistant", [{"type": "text", "text": "done"}])

    restored = store.recent_messages(limit=2)

    assert restored == [] or restored[0]["role"] == "user"
    for msg in restored:
        if msg["role"] == "user" and isinstance(msg["content"], list):
            assert not any(b.get("type") == "tool_result" for b in msg["content"])


def test_reset_clears_saved_history(store, make_brain):
    brain = make_brain([response([text_block("ok.")])], store=store)
    brain.ask("hello")
    brain.reset()

    assert store.recent_messages() == []
    assert brain.history == []
