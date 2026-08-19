"""Publishing deep_learn's research into NotebookLM.

The local vector store (core/knowledge.py) stays the source of truth for what
ODIN knows; this is a second destination for the same material, so a topic ODIN
has researched is also something you can open, browse and share. One notebook
per topic, reused when the topic is learned again.

Same optional-dependency stance as the rest of the RAG stack: nothing is
imported at module scope, every entry point returns a plain-English reason
rather than raising, and skill_manager never registers the tools when the
package or the flag is missing.

Two layers, kept apart so the interesting logic is testable without the SDK or
a Google session anywhere nearby — the split core/browser.py makes between
RefTable and BrowserController:

- `dechunk` and `build_payload` are pure. They turn stored rows into the exact
  ordered list of sources to upload, and talk to nothing.
- `NotebookLMClient` is the impure shell: it owns the SDK client and is the
  only place in ODIN that knows what its methods are called.

Why the shell owns a thread: notebooklm-py is async (`async with
NotebookLMClient.from_storage() as c: await c.notebooks.create(...)`) and
everything calling into it here — a skill, a research run — is ordinary
synchronous code. Rather than pay a fresh sign-in per source by wrapping each
call in its own asyncio.run(), the shell runs one event loop on a background
thread and keeps a single authenticated session open across the whole publish.
The same reasoning core/browser.py gives for its Playwright thread, for a
different library's constraint.

Nothing here handles credentials. `notebooklm login` stores the session; ODIN
only points at it.
"""
import asyncio
import threading
from dataclasses import dataclass, field, replace

import config
from core import knowledge
from core.security import guard

NOTE_KIND = "note"
URL_KIND = "url"

NOTEBOOK_URL_TEMPLATE = "https://notebooklm.google.com/notebook/{}"

# Markers for the two failures worth reacting to differently, matched against
# both the exception's type name and its text. Text alone would miss a typed
# error with an empty message; type alone would miss the SDK wrapping one
# library's error in another's. Matching strings rather than importing the
# exception classes is what keeps this module testable with the package absent
# — the same trade core/browser.py makes with _BROWSER_GONE_MARKERS.
_AUTH_MARKERS = (
    "autherror",
    "loginrequired",
    "headlessreauth",
    "401",
    "403",
    "unauthorized",
    "forbidden",
    "not signed in",
    "sign in",
    "credential",
    "session expired",
)
_MISSING_MARKERS = (
    "notfound",
    "not found",
    "404",
    "no such notebook",
    "does not exist",
    "deleted",
)

# security.guard's block-mode refusal starts with this.
_WITHHELD_PREFIX = "[Withheld:"


@dataclass(frozen=True)
class Source:
    """One thing to upload.

    row_id is the knowledge_sources row this came from, or None for material
    reconstructed out of the vector store (topics learned before that table
    existed) — there is no row to mark published in that case.
    """

    kind: str
    title: str
    body: str
    row_id: "int | None" = None


def available() -> bool:
    """Whether notebooklm-py is installed, without constructing a client or
    making a network call to find out."""
    try:
        import notebooklm  # noqa: F401
    except ImportError:
        return False
    return True


def preflight() -> "str | None":
    """A user-facing reason publishing can't run, or None if it can.

    Deliberately cheap: skill_manager calls this at import time to decide
    whether to register the tools, so it must not touch the network. Whether
    the stored session is still valid is only discoverable by using it, and
    that failure is explained by _explain() at publish time instead.
    """
    if not getattr(config, "ENABLE_NOTEBOOKLM", False):
        return (
            "Publishing to NotebookLM is switched off. Set ENABLE_NOTEBOOKLM=1 "
            "in .env to turn it on."
        )
    if not available():
        return (
            "Publishing to NotebookLM needs the 'notebooklm-py' package. "
            "Run: pip install notebooklm-py"
        )
    return None


def _error_text(error: BaseException) -> str:
    return f"{type(error).__name__} {error}".lower()


def _explain(error: BaseException) -> str:
    """Turn an SDK failure into something worth saying out loud."""
    if any(m in _error_text(error) for m in _AUTH_MARKERS):
        return (
            "NotebookLM isn't signed in on this machine. Run `notebooklm login` "
            "once in a terminal, sign into Google, then ask me again."
        )
    return f"NotebookLM wouldn't take it: {error}"


def is_missing_notebook(error: BaseException) -> bool:
    """Whether this failure means the notebook is gone — deleted by hand in
    NotebookLM since ODIN last wrote to it."""
    return any(m in _error_text(error) for m in _MISSING_MARKERS)


def _field(row, name, default=None):
    """Read one column from a sqlite3.Row or a plain dict. sqlite3.Row raises
    IndexError for an unknown column where a dict raises KeyError."""
    try:
        value = row[name]
    except (KeyError, IndexError):
        return default
    return default if value is None else value


def dechunk(chunks, overlap: int = knowledge.CHUNK_OVERLAP_WORDS) -> str:
    """Rejoin chunk_text() output back into the notes it was made from.

    chunk_text() steps by (size - overlap) words, so every chunk after the
    first repeats the last `overlap` words of the one before it. Dropping
    those restores the original text. Only used for topics learned before
    core.store's knowledge_sources table existed.
    """
    parts = []
    for i, chunk in enumerate(chunks):
        words = str(chunk).split()
        if i:
            words = words[overlap:]
        if words:
            parts.append(" ".join(words))
    return " ".join(parts)


def build_payload(topic: str, rows, max_urls: "int | None" = None) -> "list[Source]":
    """Turn stored knowledge_sources rows into the ordered list to upload.

    One text source per subtopic, each ending in the primary sources that
    subtopic was written from, followed by the topic's URLs as their own
    sources. Notes go first because they are the distilled material and
    NotebookLM lists sources in the order they were added.
    """
    if max_urls is None:
        max_urls = getattr(config, "NOTEBOOKLM_MAX_URL_SOURCES", 20)

    notes = []            # (subtopic, body, row_id) in first-seen order
    ordered_urls = []     # (url, row_id), deduped, in first-seen order
    urls_by_subtopic = {}
    seen_urls = set()

    for row in rows:
        kind = _field(row, "kind", "")
        body = str(_field(row, "body", "")).strip()
        if not body:
            continue
        subtopic = str(_field(row, "subtopic", "")).strip() or topic
        row_id = _field(row, "id")

        if kind == NOTE_KIND:
            notes.append((subtopic, body, row_id))
        elif kind == URL_KIND:
            if body in seen_urls:
                continue
            seen_urls.add(body)
            ordered_urls.append((body, row_id))
            urls_by_subtopic.setdefault(subtopic, []).append(body)

    sources = []
    for subtopic, body, row_id in notes:
        refs = urls_by_subtopic.get(subtopic, [])
        text = body if not refs else body + "\n\nSources:\n" + "\n".join(refs)
        sources.append(Source(NOTE_KIND, f"{topic} — {subtopic}", text, row_id))

    for url, row_id in ordered_urls[:max_urls]:
        sources.append(Source(URL_KIND, url, url, row_id))

    return sources


class NotebookLMClient:
    """The impure half: owns the notebooklm-py session and talks to Google.

    Every SDK call ODIN makes goes through the three public methods below, so
    when this unofficial API shifts, this class is the whole blast radius.

    The session is opened on first use and kept open until close(), so one
    publish of twenty-five sources authenticates once rather than once per
    source.
    """

    def __init__(self, sdk=None):
        # sdk is injectable so a caller can hand in something that isn't the
        # real library — used by anything that wants the threading and the
        # error mapping without the network.
        self._sdk = sdk
        self._ctx = None
        self._loop = None
        self._thread = None
        self._lock = threading.RLock()

    # -- the async bridge ---------------------------------------------------

    def _timeout(self) -> float:
        return float(getattr(config, "NOTEBOOKLM_TIMEOUT_SECONDS", 120))

    def _ensure_loop(self):
        """Start the background event loop, once. Daemon so a wedged upload
        can never keep ODIN from exiting."""
        if self._loop is None:
            self._loop = asyncio.new_event_loop()
            self._thread = threading.Thread(
                target=self._loop.run_forever, name="notebooklm", daemon=True
            )
            self._thread.start()
        return self._loop

    def _await(self, coro):
        """Run one coroutine on the background loop and block for its result."""
        future = asyncio.run_coroutine_threadsafe(coro, self._ensure_loop())
        return future.result(timeout=self._timeout())

    async def _open(self):
        import notebooklm

        path = getattr(config, "NOTEBOOKLM_AUTH_PATH", "") or None
        profile = getattr(config, "NOTEBOOKLM_PROFILE", "") or None
        # from_storage() returns a context manager rather than a client, and
        # entering it is what loads the stored session and opens the HTTP
        # session. Held open here and released in close().
        ctx = notebooklm.NotebookLMClient.from_storage(
            path=path, profile=profile, timeout=self._timeout()
        )
        return ctx, await ctx.__aenter__()

    def _connect(self):
        """The SDK client, built on first use. The import stays in here so
        that merely importing this module never requires the package."""
        with self._lock:
            if self._sdk is None:
                self._ctx, self._sdk = self._await(self._open())
            return self._sdk

    # -- what ODIN actually asks of NotebookLM ------------------------------

    def ensure_notebook(self, topic: str, notebook_id: "str | None" = None):
        """Return (notebook_id, notebook_url) for this topic, creating the
        notebook when there isn't one yet.

        Raises on failure — publish_sources is what turns that into something
        a person can read.
        """
        sdk = self._connect()
        if notebook_id:
            notebook = self._await(sdk.notebooks.get_or_none(notebook_id))
            if notebook is None:
                # Deleted in NotebookLM since ODIN last wrote to it. Say so in
                # the language is_missing_notebook() reads, and let the caller
                # decide whether to make a fresh one.
                raise LookupError(f"notebook {notebook_id} not found")
        else:
            notebook = self._await(sdk.notebooks.create(topic))
        return notebook.id, NOTEBOOK_URL_TEMPLATE.format(notebook.id)

    def add_source(self, notebook_id: str, source: Source) -> None:
        """Upload one source. Raises on failure; the caller decides whether one
        bad source is worth abandoning the rest (it isn't)."""
        sdk = self._connect()
        if source.kind == URL_KIND:
            self._await(sdk.sources.add_url(notebook_id, source.body))
        else:
            self._await(sdk.sources.add_text(notebook_id, source.title, source.body))

    def close(self) -> None:
        """Release the session and stop the loop. Safe to call when nothing was
        ever opened, and never raises — this runs in the finally of a publish
        that has already reported its outcome."""
        with self._lock:
            ctx, loop = self._ctx, self._loop
            self._ctx = self._sdk = None
            self._loop = self._thread = None

        if ctx is not None and loop is not None:
            try:
                asyncio.run_coroutine_threadsafe(
                    ctx.__aexit__(None, None, None), loop
                ).result(timeout=self._timeout())
            except Exception as e:  # noqa: BLE001 - closing must not raise
                print(f"[notebooklm] couldn't close the session cleanly: {e}")
        if loop is not None:
            loop.call_soon_threadsafe(loop.stop)


_CLIENT: "NotebookLMClient | None" = None
_CLIENT_LOCK = threading.Lock()


def get_notebooklm_client() -> NotebookLMClient:
    """Process-wide client, mirroring core.browser.get_browser_controller()."""
    global _CLIENT
    with _CLIENT_LOCK:
        if _CLIENT is None:
            _CLIENT = NotebookLMClient()
        return _CLIENT


def set_notebooklm_client(client) -> None:
    """Replace the process-wide client. Used by tests."""
    global _CLIENT
    with _CLIENT_LOCK:
        _CLIENT = client


@dataclass
class PublishResult:
    """What one publish attempt did.

    Partial success is the normal case, not an edge case: a five-subtopic topic
    uploads twenty-five-odd sources and any one of them can fail, so this counts
    rather than raising.
    """

    notebook_id: str = ""
    notebook_url: str = ""
    added: int = 0
    attempted: int = 0
    skipped_secrets: int = 0
    missing_notebook: bool = False
    published_row_ids: list = field(default_factory=list)
    error: "str | None" = None

    def message(self, topic: str) -> str:
        if self.error:
            return self.error

        parts = [f'Published "{topic}" to NotebookLM']
        if self.added == self.attempted:
            parts.append(f"— all {self.added} sources.")
        else:
            parts.append(
                f"— {self.added} of {self.attempted} sources; "
                "I'll retry the rest next time."
            )
        if self.skipped_secrets:
            parts.append(
                f"Held back {self.skipped_secrets} that looked like they "
                "contained a secret."
            )
        if self.notebook_url:
            parts.append(self.notebook_url)
        return " ".join(parts)


def publish_sources(topic, sources, notebook_id=None, client=None) -> PublishResult:
    """Upload an already-built payload into this topic's notebook.

    Never raises. A failed source is skipped and left out of published_row_ids,
    which is what keeps it queued for the next attempt.
    """
    client = client or get_notebooklm_client()
    result = PublishResult(attempted=len(sources))

    if not sources:
        result.error = f'Nothing new to publish for "{topic}".'
        return result

    try:
        try:
            result.notebook_id, result.notebook_url = client.ensure_notebook(
                topic, notebook_id
            )
        except Exception as e:  # noqa: BLE001 - reported, never raised
            result.error = _explain(e)
            result.missing_notebook = is_missing_notebook(e)
            return result

        for source in sources:
            # The one egress check in ODIN. Research notes are synthesized from
            # web results, but anything that ever reached them is about to leave
            # this machine, so a credential gets held back rather than uploaded.
            body = guard(source.body, source=f"notebooklm:{topic}")
            if isinstance(body, str) and body.startswith(_WITHHELD_PREFIX):
                result.skipped_secrets += 1
                continue
            try:
                client.add_source(result.notebook_id, replace(source, body=body))
            except Exception as e:  # noqa: BLE001 - one bad source, not the run
                print(f"[notebooklm] couldn't add {source.title!r}: {e}")
                continue
            result.added += 1
            if source.row_id is not None:
                result.published_row_ids.append(source.row_id)

        return result
    finally:
        # One publish, one session. Holding it open between turns would leave a
        # logged-in HTTP session and a live event loop sitting idle for hours.
        try:
            client.close()
        except Exception as e:  # noqa: BLE001
            print(f"[notebooklm] couldn't close the session: {e}")
