"""SQLite persistence: conversation, notes, reminders, and long-term memories.

Everything Jarvis should still know after a restart lives here. One connection
per Store, guarded by a lock, because the reminder scheduler runs on its own
thread.
"""
import hashlib
import json
import os
import sqlite3
import threading
import time

import config
from core import memory_index

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

CREATE TABLE IF NOT EXISTS knowledge_topics (
    topic       TEXT PRIMARY KEY,
    subtopics   TEXT NOT NULL,          -- JSON list of strings
    chunk_count INTEGER NOT NULL DEFAULT 0,
    updated_at  REAL NOT NULL
);

-- What deep_learn actually found, kept so it can be published elsewhere. The
-- vector store holds 180-word chunks and at most five joined URLs per chunk,
-- which is right for retrieval and lossy for anything wanting whole notes and
-- every source. published_at doubles as the publish work queue: NULL means
-- "not in a notebook yet", so a failed upload simply stays queued.
CREATE TABLE IF NOT EXISTS knowledge_sources (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    topic        TEXT NOT NULL,
    subtopic     TEXT NOT NULL,
    kind         TEXT NOT NULL,          -- 'note' | 'url'
    body         TEXT NOT NULL,          -- the note text, or the URL
    fingerprint  TEXT NOT NULL,          -- sha256(kind + body)
    ts           REAL NOT NULL,
    published_at REAL,
    UNIQUE (topic, fingerprint)
);
CREATE INDEX IF NOT EXISTS idx_knowledge_sources_pending
    ON knowledge_sources (topic, published_at);

-- Which notebook belongs to which topic, so re-learning a topic adds to the
-- notebook it already has instead of making a second one.
CREATE TABLE IF NOT EXISTS knowledge_notebooks (
    topic        TEXT PRIMARY KEY,
    notebook_id  TEXT NOT NULL,
    notebook_url TEXT NOT NULL,
    created_at   REAL NOT NULL,
    updated_at   REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS scheduled_tasks (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    created  REAL NOT NULL,
    prompt   TEXT NOT NULL,
    schedule TEXT NOT NULL,       -- e.g. "daily 08:00", "mon,wed,fri 18:30"
    last_run REAL,                -- NULL until it has fired at least once
    enabled  INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS security_events (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    ts       REAL NOT NULL,
    source   TEXT NOT NULL,   -- e.g. "read_file:C:\\notes.txt", "run_command"
    mode     TEXT NOT NULL,   -- warn | redact | block
    pattern  TEXT NOT NULL,   -- comma-joined names of the patterns that matched
    preview  TEXT NOT NULL    -- short REDACTED preview only, never the raw secret
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

        messages = []
        for r in reversed(rows):
            try:
                content = json.loads(r["content"])
            except (json.JSONDecodeError, TypeError):
                # A single corrupted row (partial write from a hard kill,
                # disk corruption, a hand-edited DB) must not take down the
                # whole app at startup — every write into this table already
                # treats persistence failures as "degrade, don't crash" (see
                # Brain._persist()); the read path deserves the same rather
                # than propagating out of load_history() unguarded.
                continue
            messages.append({"role": r["role"], "content": content})

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
                cur = self._conn.execute(
                    "INSERT INTO memories (ts, text) VALUES (?, ?)", (time.time(), text)
                )
                self._conn.commit()
                memory_id = cur.lastrowid
            except sqlite3.IntegrityError:
                return False
        # Outside the lock: embedding is comparatively slow, and best-effort
        # anyway — memory_index never raises, so a bad index doesn't undo the
        # commit that already succeeded.
        memory_index.index(memory_id, text)
        return True

    def recall(self, query: str = "", limit: int = 20) -> list[str]:
        """Semantic search first when a query is given, falling back to the
        LIKE search when the index is unavailable, empty, or finds nothing
        close enough — same "nothing indexed yet is normal" philosophy as
        core.knowledge. An empty query always lists the most recent facts;
        there is nothing to embed a similarity search against."""
        if query:
            semantic = memory_index.search(query, limit=limit)
            if semantic:
                return semantic

        with self._lock:
            if query:
                rows = self._conn.execute(
                    "SELECT text FROM memories WHERE text LIKE ? ESCAPE '\\' ORDER BY id DESC LIMIT ?",
                    (f"%{_escape_like(query)}%", limit),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT text FROM memories ORDER BY id DESC LIMIT ?", (limit,)
                ).fetchall()
        return [r["text"] for r in rows]

    def forget(self, query: str) -> int:
        with self._lock:
            doomed = self._conn.execute(
                "SELECT id FROM memories WHERE text LIKE ? ESCAPE '\\'",
                (f"%{_escape_like(query)}%",),
            ).fetchall()
            cur = self._conn.execute(
                "DELETE FROM memories WHERE text LIKE ? ESCAPE '\\'",
                (f"%{_escape_like(query)}%",),
            )
            self._conn.commit()
        for row in doomed:
            memory_index.remove(row["id"])
        return cur.rowcount

    # -- knowledge (deep_learn manifest) ------------------------------------

    def record_knowledge_topic(self, topic: str, subtopics: list[str], chunk_count: int) -> None:
        """Upsert a topic's manifest row. chunk_count is the running total
        stored in the vector DB, not just what this call added."""
        with self._lock:
            self._conn.execute(
                """INSERT INTO knowledge_topics (topic, subtopics, chunk_count, updated_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(topic) DO UPDATE SET
                       subtopics = excluded.subtopics,
                       chunk_count = excluded.chunk_count,
                       updated_at = excluded.updated_at""",
                (topic, json.dumps(subtopics), chunk_count, time.time()),
            )
            self._conn.commit()

    def list_knowledge_topics(self) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(
                "SELECT topic, subtopics, chunk_count, updated_at FROM knowledge_topics "
                "ORDER BY updated_at DESC"
            ).fetchall()

    def get_knowledge_topic(self, topic: str) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(
                "SELECT topic, subtopics, chunk_count, updated_at FROM knowledge_topics WHERE topic = ?",
                (topic,),
            ).fetchone()

    # -- knowledge sources (raw material for publishing) ---------------------

    def record_knowledge_sources(
        self, topic: str, subtopic: str, kind: str, bodies: list[str]
    ) -> int:
        """Store one research step's material, skipping anything already
        recorded for this topic. Returns how many new rows landed, so a
        re-learn can tell what was actually new."""
        now = time.time()
        rows = []
        for body in bodies:
            body = (body or "").strip()
            if not body:
                continue
            rows.append((topic, subtopic, kind, body, _fingerprint(kind, body), now))
        if not rows:
            return 0

        with self._lock:
            before = self._conn.total_changes
            self._conn.executemany(
                """INSERT OR IGNORE INTO knowledge_sources
                       (topic, subtopic, kind, body, fingerprint, ts)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                rows,
            )
            self._conn.commit()
            return self._conn.total_changes - before

    def pending_knowledge_sources(self, topic: str) -> list[sqlite3.Row]:
        """Everything for a topic that hasn't made it into a notebook yet,
        oldest first."""
        with self._lock:
            return self._conn.execute(
                "SELECT id, topic, subtopic, kind, body FROM knowledge_sources "
                "WHERE topic = ? AND published_at IS NULL ORDER BY id",
                (topic,),
            ).fetchall()

    def mark_knowledge_sources_published(self, row_ids) -> None:
        """Take rows out of the publish queue. Only the ones that actually
        uploaded are passed in, so a partial upload leaves the rest queued."""
        ids = [(time.time(), int(i)) for i in row_ids if i is not None]
        if not ids:
            return
        with self._lock:
            self._conn.executemany(
                "UPDATE knowledge_sources SET published_at = ? WHERE id = ?", ids
            )
            self._conn.commit()

    def get_knowledge_notebook(self, topic: str) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(
                "SELECT topic, notebook_id, notebook_url, created_at, updated_at "
                "FROM knowledge_notebooks WHERE topic = ?",
                (topic,),
            ).fetchone()

    def record_knowledge_notebook(self, topic: str, notebook_id: str, notebook_url: str) -> None:
        now = time.time()
        with self._lock:
            self._conn.execute(
                """INSERT INTO knowledge_notebooks
                       (topic, notebook_id, notebook_url, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(topic) DO UPDATE SET
                       notebook_id = excluded.notebook_id,
                       notebook_url = excluded.notebook_url,
                       updated_at = excluded.updated_at""",
                (topic, notebook_id, notebook_url, now, now),
            )
            self._conn.commit()

    def forget_knowledge_notebook(self, topic: str) -> None:
        """Drop the mapping — used when the notebook turns out to be gone,
        deleted by hand in NotebookLM since the last publish."""
        with self._lock:
            self._conn.execute("DELETE FROM knowledge_notebooks WHERE topic = ?", (topic,))
            self._conn.commit()

    # -- scheduled tasks -----------------------------------------------------

    def add_scheduled_task(self, prompt: str, schedule: str) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO scheduled_tasks (created, prompt, schedule) VALUES (?, ?, ?)",
                (time.time(), prompt, schedule),
            )
            self._conn.commit()
            return cur.lastrowid

    def list_scheduled_tasks(self, enabled_only: bool = False) -> list[sqlite3.Row]:
        query = "SELECT id, created, prompt, schedule, last_run, enabled FROM scheduled_tasks"
        if enabled_only:
            query += " WHERE enabled = 1"
        query += " ORDER BY id"
        with self._lock:
            return self._conn.execute(query).fetchall()

    def get_scheduled_task(self, task_id: int) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(
                "SELECT id, created, prompt, schedule, last_run, enabled FROM scheduled_tasks WHERE id = ?",
                (task_id,),
            ).fetchone()

    def delete_scheduled_task(self, task_id: int) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM scheduled_tasks WHERE id = ?", (task_id,))
            self._conn.commit()
            return cur.rowcount > 0

    def mark_task_run(self, task_id: int, ts: float) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE scheduled_tasks SET last_run = ? WHERE id = ?", (ts, task_id)
            )
            self._conn.commit()

    # -- security audit log -------------------------------------------------

    def log_security_event(self, source: str, mode: str, pattern: str, preview: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO security_events (ts, source, mode, pattern, preview) "
                "VALUES (?, ?, ?, ?, ?)",
                (time.time(), source, mode, pattern, preview),
            )
            self._conn.commit()

    def recent_security_events(self, limit: int = 50) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(
                "SELECT ts, source, mode, pattern, preview FROM security_events "
                "ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()

    # -- migration ---------------------------------------------------------

    def _migrate_notes_file(self) -> None:
        """One-time import of the old data/notes.txt into the notes table."""
        legacy = config.NOTES_FILE
        marker = legacy + ".migrated"
        # The marker's existence, not the rename's success, is what means
        # "already migrated" — trusting the rename let a FileExistsError (the
        # marker surviving a prior run for any reason) silently fall through
        # to except OSError, leaving notes.txt in place to be re-imported
        # (duplicated) on every subsequent startup.
        if os.path.exists(marker) or not os.path.exists(legacy):
            return
        try:
            with open(legacy, "r", encoding="utf-8") as f:
                lines = [ln.strip() for ln in f if ln.strip()]
            for line in lines:
                self.add_note(line)
            os.rename(legacy, marker)
        except OSError:
            pass  # not worth failing startup over


_STORE: Store | None = None
_STORE_LOCK = threading.Lock()


def get_store() -> Store:
    """Process-wide Store, created on first use.

    Skills reach for this rather than taking a constructor argument, so
    SkillManager stays a zero-argument registry. Tests override it with
    set_store(Store(":memory:")).
    """
    global _STORE
    with _STORE_LOCK:
        if _STORE is None:
            _STORE = Store()
        return _STORE


def set_store(store: Store | None) -> None:
    """Replace the process-wide Store (used by tests)."""
    global _STORE
    with _STORE_LOCK:
        _STORE = store


def _escape_like(text: str) -> str:
    """Escape SQL LIKE wildcards so a literal '%' or '_' in a memory query
    matches literally instead of acting as a wildcard. Paired with an
    ESCAPE '\\' clause at each call site."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _fingerprint(kind: str, body: str) -> str:
    """Stable identity for a stored source. Hashed rather than indexing the
    body itself: URLs are short but note bodies run to kilobytes, and the
    UNIQUE index only ever has to answer "have I stored this already?"."""
    return hashlib.sha256(f"{kind}\x00{body}".encode("utf-8")).hexdigest()


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
