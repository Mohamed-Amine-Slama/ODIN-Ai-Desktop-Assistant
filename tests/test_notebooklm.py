"""Tests for core/notebooklm.py — payload assembly, the reconstruction path
for topics learned before knowledge_sources existed, and the upload loop.

Nothing here imports notebooklm-py or touches a network: the SDK lives behind
NotebookLMClient, and every test that publishes hands in a fake with the same
two methods.
"""
import pytest

import config
from core import knowledge, notebooklm
from core.notebooklm import PublishResult, Source


def _row(kind, body, subtopic="basics", row_id=1):
    return {"id": row_id, "kind": kind, "body": body, "subtopic": subtopic}


# -- preflight ----------------------------------------------------------------

def test_preflight_reports_the_flag_being_off(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_NOTEBOOKLM", False, raising=False)
    assert "ENABLE_NOTEBOOKLM" in notebooklm.preflight()


def test_preflight_reports_a_missing_package(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_NOTEBOOKLM", True, raising=False)
    monkeypatch.setattr(notebooklm, "available", lambda: False)
    assert "notebooklm-py" in notebooklm.preflight()


def test_preflight_passes_when_flag_and_package_are_both_ready(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_NOTEBOOKLM", True, raising=False)
    monkeypatch.setattr(notebooklm, "available", lambda: True)
    assert notebooklm.preflight() is None


# -- dechunk ------------------------------------------------------------------

def test_dechunk_restores_the_text_chunk_text_split():
    original = " ".join(f"word{i}" for i in range(500))
    chunks = knowledge.chunk_text(original)
    assert len(chunks) > 1, "the fixture must actually span several chunks"
    assert notebooklm.dechunk(chunks) == original


def test_dechunk_leaves_a_single_chunk_alone():
    assert notebooklm.dechunk(["just one short chunk"]) == "just one short chunk"


def test_dechunk_of_nothing_is_empty():
    assert notebooklm.dechunk([]) == ""


# -- build_payload ------------------------------------------------------------

def test_notes_come_before_urls():
    rows = [
        _row("url", "https://a.example", row_id=1),
        _row("note", "Notes about basics.", row_id=2),
    ]
    kinds = [s.kind for s in notebooklm.build_payload("Rust", rows)]
    assert kinds == ["note", "url"]


def test_a_note_is_titled_topic_then_subtopic():
    rows = [_row("note", "Body.", subtopic="ownership")]
    assert notebooklm.build_payload("Rust", rows)[0].title == "Rust — ownership"


def test_a_note_carries_its_own_subtopics_urls_as_a_reference_list():
    rows = [
        _row("note", "Body.", subtopic="ownership", row_id=1),
        _row("url", "https://a.example", subtopic="ownership", row_id=2),
        _row("url", "https://b.example", subtopic="traits", row_id=3),
    ]
    note = notebooklm.build_payload("Rust", rows)[0]
    assert "Sources:" in note.body
    assert "https://a.example" in note.body
    assert "https://b.example" not in note.body


def test_the_same_url_found_twice_becomes_one_source():
    rows = [
        _row("url", "https://a.example", subtopic="ownership", row_id=1),
        _row("url", "https://a.example", subtopic="traits", row_id=2),
    ]
    urls = [s for s in notebooklm.build_payload("Rust", rows) if s.kind == "url"]
    assert len(urls) == 1


def test_urls_are_capped():
    rows = [_row("url", f"https://e{i}.example", row_id=i) for i in range(30)]
    urls = [s for s in notebooklm.build_payload("Rust", rows, max_urls=5) if s.kind == "url"]
    assert len(urls) == 5


def test_empty_bodies_are_dropped():
    rows = [_row("note", "   ", row_id=1), _row("url", "", row_id=2)]
    assert notebooklm.build_payload("Rust", rows) == []


def test_each_source_remembers_the_row_it_came_from():
    rows = [_row("note", "Body.", row_id=7)]
    assert notebooklm.build_payload("Rust", rows)[0].row_id == 7


def test_a_row_without_a_subtopic_falls_back_to_the_topic():
    rows = [{"id": 1, "kind": "note", "body": "Body.", "subtopic": ""}]
    assert notebooklm.build_payload("Rust", rows)[0].title == "Rust — Rust"


# -- the upload loop ----------------------------------------------------------

class FakeClient:
    """Stands in for NotebookLMClient. `fail_on` is a set of source titles
    whose upload should raise, which is how partial failure is simulated."""

    def __init__(
        self,
        notebook=("nb-1", "https://notebooklm.google.com/notebook/nb-1"),
        ensure_error=None,
        fail_on=(),
    ):
        self.notebook = notebook
        self.ensure_error = ensure_error
        self.fail_on = set(fail_on)
        self.added = []
        self.ensure_calls = []
        self.closed = False

    def ensure_notebook(self, topic, notebook_id=None):
        self.ensure_calls.append((topic, notebook_id))
        if self.ensure_error is not None:
            raise self.ensure_error
        return self.notebook

    def add_source(self, notebook_id, source):
        if source.title in self.fail_on:
            raise RuntimeError("upload rejected")
        self.added.append(source)

    def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def _scan_off(monkeypatch):
    """core.security.guard logs audit rows through the process-wide store;
    these tests are about publishing, not scanning."""
    monkeypatch.setattr(config, "SECURITY_SCAN_MODE", "off", raising=False)


def test_publishing_uploads_every_source_and_reports_the_notebook():
    client = FakeClient()
    sources = [
        Source("note", "Rust — ownership", "Body.", 1),
        Source("url", "https://a.example", "https://a.example", 2),
    ]

    result = notebooklm.publish_sources("Rust", sources, client=client)

    assert result.error is None
    assert result.added == 2
    assert result.published_row_ids == [1, 2]
    assert result.notebook_url.endswith("nb-1")
    assert len(client.added) == 2


def test_publishing_closes_the_session_it_opened():
    client = FakeClient()
    notebooklm.publish_sources("Rust", [Source("note", "t", "b", 1)], client=client)
    assert client.closed is True


def test_a_failed_upload_leaves_its_row_queued():
    client = FakeClient(fail_on=["Rust — ownership"])
    sources = [
        Source("note", "Rust — ownership", "Body.", 1),
        Source("url", "https://a.example", "https://a.example", 2),
    ]

    result = notebooklm.publish_sources("Rust", sources, client=client)

    assert result.added == 1
    assert result.attempted == 2
    assert result.published_row_ids == [2]
    assert result.error is None


def test_a_notebook_that_cannot_be_created_is_reported_not_raised():
    client = FakeClient(ensure_error=RuntimeError("could not sign in"))
    result = notebooklm.publish_sources("Rust", [Source("note", "t", "b", 1)], client=client)
    assert result.added == 0
    assert result.error


def test_an_auth_failure_tells_the_user_to_log_in():
    assert "notebooklm login" in notebooklm._explain(RuntimeError("401 Unauthorized"))


def test_an_auth_failure_is_recognised_by_exception_type_too():
    class AuthError(Exception):
        pass

    assert "notebooklm login" in notebooklm._explain(AuthError("rpc failed"))


def test_an_ordinary_failure_is_passed_through():
    message = notebooklm._explain(RuntimeError("rate limited"))
    assert "rate limited" in message
    assert "notebooklm login" not in message


def test_a_deleted_notebook_is_recognised():
    assert notebooklm.is_missing_notebook(RuntimeError("notebook not found"))
    assert not notebooklm.is_missing_notebook(RuntimeError("rate limited"))


def test_a_deleted_notebook_is_recognised_by_exception_type_too():
    class NotebookNotFoundError(Exception):
        pass

    assert notebooklm.is_missing_notebook(NotebookNotFoundError("gone"))


def test_a_missing_notebook_is_flagged_on_the_result():
    client = FakeClient(ensure_error=RuntimeError("notebook not found"))
    result = notebooklm.publish_sources(
        "Rust", [Source("note", "t", "b", 1)], notebook_id="nb-old", client=client
    )
    assert result.missing_notebook is True


def test_publishing_nothing_says_so():
    result = notebooklm.publish_sources("Rust", [], client=FakeClient())
    assert result.added == 0
    assert "nothing new" in result.error.lower()


def test_a_source_holding_a_secret_is_skipped_rather_than_uploaded(monkeypatch):
    monkeypatch.setattr(
        "core.notebooklm.guard",
        lambda text, source: "[Withheld: this result looks like it contains a secret (aws).]",
    )
    client = FakeClient()

    result = notebooklm.publish_sources("Rust", [Source("note", "t", "b", 1)], client=client)

    assert client.added == []
    assert result.skipped_secrets == 1
    assert result.published_row_ids == []


def test_redacted_text_is_what_gets_uploaded(monkeypatch):
    monkeypatch.setattr("core.notebooklm.guard", lambda text, source: "REDACTED body")
    client = FakeClient()

    notebooklm.publish_sources("Rust", [Source("note", "t", "secret body", 1)], client=client)

    assert client.added[0].body == "REDACTED body"


def test_the_client_singleton_can_be_replaced():
    fake = FakeClient()
    notebooklm.set_notebooklm_client(fake)
    try:
        assert notebooklm.get_notebooklm_client() is fake
    finally:
        notebooklm.set_notebooklm_client(None)


def test_publish_result_summarises_a_full_upload():
    result = PublishResult(notebook_url="https://x/1", added=3, attempted=3)
    message = result.message("Rust")
    assert "all 3" in message
    assert "https://x/1" in message


def test_publish_result_summarises_a_partial_upload():
    result = PublishResult(notebook_url="https://x/1", added=22, attempted=25)
    assert "22 of 25" in result.message("Rust")


def test_publish_result_mentions_what_it_held_back():
    result = PublishResult(notebook_url="https://x/1", added=1, attempted=1, skipped_secrets=2)
    assert "2" in result.message("Rust")
    assert "secret" in result.message("Rust")


def test_publish_result_reports_an_error_verbatim():
    result = PublishResult(error="Run `notebooklm login`.")
    assert result.message("Rust") == "Run `notebooklm login`."
