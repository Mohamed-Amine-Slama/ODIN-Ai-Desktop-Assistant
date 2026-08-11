"""Tests for the undo journal and the trash used by destructive file ops."""
import time
from pathlib import Path

import pytest

from core.undo import UndoJournal, move_to_trash, prune_trash


@pytest.fixture
def journal():
    return UndoJournal(max_age_seconds=900)


def test_record_returns_a_token_and_undo_runs_the_action(journal):
    calls = []
    token = journal.record("Restore notes.txt", lambda: calls.append("done") or "Restored.")

    assert isinstance(token, str) and token
    assert journal.undo(token) == "Restored."
    assert calls == ["done"]


def test_undo_is_single_use(journal):
    token = journal.record("x", lambda: "ok")
    journal.undo(token)
    assert "no longer undoable" in journal.undo(token)


def test_unknown_token_is_a_message_not_an_exception(journal):
    assert "no longer undoable" in journal.undo("nope")


def test_latest_returns_the_most_recent_entry(journal):
    journal.record("first", lambda: "a")
    journal.record("second", lambda: "b")
    assert journal.latest().description == "second"


def test_latest_is_none_when_empty(journal):
    assert journal.latest() is None


def test_entries_expire(journal):
    token = journal.record("old", lambda: "a")
    journal._entries[token].created = time.time() - 1000
    assert journal.expire() == 1
    assert "no longer undoable" in journal.undo(token)


def test_a_failing_undo_keeps_the_entry_for_retry(journal):
    """If the target path is now occupied, the user should be able to fix it
    and try again rather than losing the ability to reverse."""
    attempts = []

    def flaky():
        attempts.append(1)
        if len(attempts) == 1:
            raise OSError("destination exists")
        return "Restored on retry."

    token = journal.record("restore", flaky)
    with pytest.raises(OSError):
        journal.undo(token)
    assert journal.undo(token) == "Restored on retry."


def test_move_to_trash_copies_the_file(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))

    original = tmp_path / "report.docx"
    original.write_text("important", encoding="utf-8")

    backup = move_to_trash(original)

    assert backup.exists()
    assert backup.read_text(encoding="utf-8") == "important"
    assert backup.name == "report.docx"
    assert original.exists(), "move_to_trash copies; the caller deletes"


def test_move_to_trash_handles_directories(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))

    folder = tmp_path / "project"
    folder.mkdir()
    (folder / "a.txt").write_text("a", encoding="utf-8")

    backup = move_to_trash(folder)
    assert (backup / "a.txt").read_text(encoding="utf-8") == "a"


def test_prune_trash_by_count(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))

    for i in range(5):
        f = tmp_path / f"f{i}.txt"
        f.write_text(str(i), encoding="utf-8")
        move_to_trash(f)
        time.sleep(0.01)

    removed = prune_trash(max_entries=2, max_age_days=365)
    assert removed == 3
    assert len(list((Path(tmp_path) / "trash").iterdir())) == 2
