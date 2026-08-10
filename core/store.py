"""SQLite persistence: conversation, notes, reminders, and long-term memories.

Everything Jarvis should still know after a restart lives here. One connection
per Store, guarded by a lock, because the reminder scheduler runs on its own
thread.
"""
import json
import os
import sqlite3
import threading
import time

import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    ts       REAL    NOT NULL,
    role     TEXT    NOT NULL,
    content  TEXT    NOT NULL          -- JSON: str or list of content blocks
);

CREATE TABLE IF NOT EXISTS notes (
    id    INTEGER PRIMARY KEY AUTOINCREMENT,
    ts    REAL NOT NULL,
    text  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reminders (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    created  REAL NOT NULL,
    fire_at  REAL NOT NULL,
    message  TEXT NOT NULL,
    fired    INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_reminders_pending ON reminders (fired, fire_at);

CREATE TABLE IF NOT EXISTS memories (
    id    INTEGER PRIMARY KEY AUTOINCREMENT,
    ts    REAL NOT NULL,
    text  TEXT NOT NULL UNIQUE
);
"""


class Store:
    def __init__(self, path: str | None = None):
        config.ensure_dirs()
        self.path = path or os.path.join(config.DATA_DIR, "jarvis.db")
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()
        self._migrate_notes_file()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # -- conversation ------------------------------------------------------

    def append_message(self, role: str, content) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO messages (ts, role, content) VALUES (?, ?, ?)",
                (time.time(), role, json.dumps(content, default=_encode_block)),
            )
            self._conn.commit()

    def recent_messages(self, limit: int = 20) -> list[dict]:
        """Return the last `limit` messages, oldest first, trimmed to start on
        a user turn so the conversation is always well-formed."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT role, content FROM messages ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()

        messages = [
            {"role": r["role"], "content": json.loads(r["content"])} for r in reversed(rows)
        ]

        # A conversation may not start on an assistant turn, and must never
        # start on a tool_result whose tool_use we just trimmed away.
        while messages and not _is_plain_user_turn(messages[0]):
            messages.pop(0)
        return messages

    def clear_messages(self) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM messages")
            self._conn.commit()

    # -- notes -------------------------------------------------------------

    def add_note(self, text: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO notes (ts, text) VALUES (?, ?)", (time.time(), text)
            )
            self._conn.commit()

    def list_notes(self) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(
                "SELECT ts, text FROM notes ORDER BY id"
            ).fetchall()

    def clear_notes(self) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM notes")
            self._conn.commit()

    # -- reminders ---------------------------------------------------------

    def add_reminder(self, message: str, fire_at: float) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO reminders (created, fire_at, message) VALUES (?, ?, ?)",
                (time.time(), fire_at, message),
            )
            self._conn.commit()
            return cur.lastrowid

    def due_reminders(self, now: float | None = None) -> list[sqlite3.Row]:
        now = time.time() if now is None else now
        with self._lock:
            return self._conn.execute(
                "SELECT id, message, fire_at FROM reminders "
                "WHERE fired = 0 AND fire_at <= ? ORDER BY fire_at",
                (now,),
            ).fetchall()

    def mark_fired(self, reminder_id: int) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE reminders SET fired = 1 WHERE id = ?", (reminder_id,)
            )
            self._conn.commit()

    def pending_reminders(self) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(
                "SELECT id, message, fire_at FROM reminders WHERE fired = 0 ORDER BY fire_at"
            ).fetchall()

    # -- memories ----------------------------------------------------------

    def remember(self, text: str) -> bool:
        """Store a durable fact. Returns False if it was already known."""
        with self._lock:
            try:
                self._conn.execute(
                    "INSERT INTO memories (ts, text) VALUES (?, ?)", (time.time(), text)
                )
                self._conn.commit()
                return True
            except sqlite3.IntegrityError:
                return False

    def recall(self, query: str = "", limit: int = 20) -> list[str]:
        with self._lock:
            if query:
                rows = self._conn.execute(
                    "SELECT text FROM memories WHERE text LIKE ? ORDER BY id DESC LIMIT ?",
                    (f"%{query}%", limit),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT text FROM memories ORDER BY id DESC LIMIT ?", (limit,)
                ).fetchall()
        return [r["text"] for r in rows]

    def forget(self, query: str) -> int:
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM memories WHERE text LIKE ?", (f"%{query}%",)
            )
            self._conn.commit()
            return cur.rowcount

    # -- migration ---------------------------------------------------------

    def _migrate_notes_file(self) -> None:
        """One-time import of the old data/notes.txt into the notes table."""
        legacy = config.NOTES_FILE
        if not os.path.exists(legacy):
            return
        try:
            with open(legacy, "r", encoding="utf-8") as f:
                lines = [ln.strip() for ln in f if ln.strip()]
            for line in lines:
                self.add_note(line)
            os.rename(legacy, legacy + ".migrated")
        except OSError:
            pass  # not worth failing startup over


def _encode_block(obj):
    """JSON encoder for SDK content blocks (Pydantic models) stored verbatim."""
    for attr in ("model_dump", "dict"):
        fn = getattr(obj, attr, None)
        if callable(fn):
            return fn()
    if hasattr(obj, "__dict__"):
        return dict(obj.__dict__)
    return str(obj)


def _is_plain_user_turn(message: dict) -> bool:
    """True for a user message that is ordinary text — i.e. a safe place to
    start a conversation. A user message carrying tool_result blocks is NOT
    safe: its matching assistant tool_use may have been trimmed away."""
    if message.get("role") != "user":
        return False
    content = message.get("content")
    if isinstance(content, str):
        return True
    if isinstance(content, list):
        return not any(
            isinstance(b, dict) and b.get("type") == "tool_result" for b in content
        )
    return False
