"""Undo journal and the trash that makes file deletion reversible.

In-memory only: undo does not survive a restart. The trash copy does, so a
crash loses the one-click reversal but never the data.
"""
import shutil
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import config


@dataclass
class UndoEntry:
    token: str
    description: str
    action: Callable[[], str]
    created: float = field(default_factory=time.time)


class UndoJournal:
    def __init__(self, max_age_seconds: float | None = None):
        self._entries: dict[str, UndoEntry] = {}
        self._order: list[str] = []
        self._lock = threading.Lock()
        self.max_age = (
            max_age_seconds if max_age_seconds is not None else config.UNDO_WINDOW_SECONDS
        )

    def record(self, description: str, action: Callable[[], str]) -> str:
        token = uuid.uuid4().hex[:12]
        with self._lock:
            self._entries[token] = UndoEntry(token, description, action)
            self._order.append(token)
        return token

    def latest(self) -> UndoEntry | None:
        self.expire()
        with self._lock:
            for token in reversed(self._order):
                entry = self._entries.get(token)
                if entry is not None:
                    return entry
        return None

    def undo(self, token: str) -> str:
        self.expire()
        with self._lock:
            entry = self._entries.get(token)
        if entry is None:
            return "That action is no longer undoable."

        # Run outside the lock, and only drop the entry on success — a failed
        # undo (destination now occupied) should stay retryable.
        result = entry.action()
        with self._lock:
            self._entries.pop(token, None)
            self._discard_order(token)
        return result

    def expire(self) -> int:
        cutoff = time.time() - self.max_age
        with self._lock:
            stale = [t for t, e in self._entries.items() if e.created < cutoff]
            for token in stale:
                del self._entries[token]
                self._discard_order(token)
        return len(stale)

    def _discard_order(self, token: str) -> None:
        """Remove token from _order if present. Caller holds self._lock."""
        try:
            self._order.remove(token)
        except ValueError:
            pass


_JOURNAL: UndoJournal | None = None
_JOURNAL_LOCK = threading.Lock()


def get_journal() -> UndoJournal:
    global _JOURNAL
    with _JOURNAL_LOCK:
        if _JOURNAL is None:
            _JOURNAL = UndoJournal()
        return _JOURNAL


def set_journal(journal: UndoJournal | None) -> None:
    """Replace the process-wide journal (used by tests)."""
    global _JOURNAL
    with _JOURNAL_LOCK:
        _JOURNAL = journal


def trash_dir() -> Path:
    path = Path(config.DATA_DIR) / "trash"
    path.mkdir(parents=True, exist_ok=True)
    return path


def move_to_trash(src: Path) -> Path:
    """Copy src into a fresh trash bucket and return the backup path.

    This copies rather than moves so the caller controls when the original
    disappears — an overwrite needs the backup taken while the original is
    still in place.
    """
    src = Path(src)
    bucket = trash_dir() / uuid.uuid4().hex[:12]
    bucket.mkdir(parents=True, exist_ok=True)
    # src.name is "" for a bare drive root ("C:/") or "." — bucket / "" is a
    # no-op path join, so dest would silently collapse onto bucket itself
    # (which was just created, and empty) instead of holding a copy.
    dest = bucket / (src.name or "item")
    if src.is_dir():
        shutil.copytree(src, dest)
    else:
        shutil.copy2(src, dest)
    return dest


def prune_trash(max_entries: int | None = None, max_age_days: float | None = None) -> int:
    """Drop old trash buckets. Returns how many were removed."""
    max_entries = config.TRASH_MAX_ENTRIES if max_entries is None else max_entries
    max_age_days = config.TRASH_MAX_AGE_DAYS if max_age_days is None else max_age_days

    root = trash_dir()
    buckets = sorted(
        (p for p in root.iterdir() if p.is_dir()),
        key=lambda p: p.stat().st_mtime,
    )

    cutoff = time.time() - max_age_days * 86400
    doomed = [b for b in buckets if b.stat().st_mtime < cutoff]
    keep = [b for b in buckets if b not in doomed]
    if len(keep) > max_entries:
        doomed.extend(keep[: len(keep) - max_entries])

    for bucket in doomed:
        shutil.rmtree(bucket, ignore_errors=True)
    return len(doomed)
