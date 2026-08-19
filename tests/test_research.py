"""Tests for core/research.py, the pipeline behind deep_learn. Web search and
the LLM completion helper are both mocked; no real network or provider calls,
and no dependency on which provider Jarvis itself is configured with.
"""
from types import SimpleNamespace

import pytest

import config
from core import research


# -- preflight ----------------------------------------------------------------

def test_preflight_needs_web_search(monkeypatch):
    monkeypatch.setattr("core.research.web_search_available", lambda: False)
    assert "ddgs" in research.preflight()


def test_preflight_needs_rag_deps(monkeypatch):
    monkeypatch.setattr("core.research.web_search_available", lambda: True)
    monkeypatch.setattr("core.research.knowledge.available", lambda: False)
    assert "requirements.txt" in research.preflight()


def test_preflight_passes_when_both_are_ready(monkeypatch):
    monkeypatch.setattr("core.research.web_search_available", lambda: True)
    monkeypatch.setattr("core.research.knowledge.available", lambda: True)
    assert research.preflight() is None


# -- _llm_complete provider branching ------------------------------------------

def test_llm_complete_uses_openai_compatible_path_by_default(monkeypatch):
    monkeypatch.setattr(config, "MODEL", "gemini-2.5-flash", raising=False)
    monkeypatch.setattr(config, "BASE_URL", "https://openrouter.ai/api/v1", raising=False)
    monkeypatch.setattr(config, "API_KEY", "key", raising=False)

    captured = {}

    class FakeCompletions:
        def create(self, **kwargs):
            captured.update(kwargs)
            message = SimpleNamespace(content="hello from openai")
            return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    class FakeOpenAI:
        def __init__(self, api_key, base_url):  # noqa: ARG002
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setattr("openai.OpenAI", FakeOpenAI)

    assert research._llm_complete("prompt") == "hello from openai"
    assert captured["model"] == "gemini-2.5-flash"


def test_llm_complete_uses_native_anthropic_path_for_claude(monkeypatch):
    """The anthropic package isn't installed in this test environment (same
    as the rest of the suite) — fake the module in sys.modules rather than
    patching an attribute on a module that doesn't exist to import."""
    import sys
    import types

    monkeypatch.setattr(config, "MODEL", "claude-opus-5", raising=False)
    monkeypatch.setattr(config, "BASE_URL", "", raising=False)
    monkeypatch.setattr(config, "API_KEY", "key", raising=False)

    captured = {}

    class FakeMessages:
        def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(content=[SimpleNamespace(type="text", text="hello from claude")])

    class FakeAnthropic:
        def __init__(self, api_key, base_url):  # noqa: ARG002
            self.messages = FakeMessages()

    fake_module = types.ModuleType("anthropic")
    fake_module.Anthropic = FakeAnthropic
    monkeypatch.setitem(sys.modules, "anthropic", fake_module)

    assert research._llm_complete("prompt") == "hello from claude"
    assert captured["model"] == "claude-opus-5"


def test_llm_complete_wraps_provider_errors(monkeypatch):
    monkeypatch.setattr(config, "MODEL", "gpt-4o-mini", raising=False)
    monkeypatch.setattr(config, "BASE_URL", "https://api.example.com", raising=False)

    class FakeCompletions:
        def create(self, **kwargs):  # noqa: ARG002
            raise RuntimeError("boom")

    class FakeOpenAI:
        def __init__(self, api_key, base_url):  # noqa: ARG002
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setattr("openai.OpenAI", FakeOpenAI)

    with pytest.raises(RuntimeError, match="couldn't reach the model"):
        research._llm_complete("prompt")


def test_llm_complete_treats_empty_choices_as_an_empty_answer(monkeypatch):
    """A gateway can legitimately return zero choices (content filtered, a
    malformed upstream response). Every caller of _llm_complete only catches
    RuntimeError to skip one step — an uncaught IndexError from blindly
    indexing choices[0] would abort the whole deep_learn run instead."""
    monkeypatch.setattr(config, "MODEL", "gpt-4o-mini", raising=False)
    monkeypatch.setattr(config, "BASE_URL", "https://api.example.com", raising=False)

    class FakeCompletions:
        def create(self, **kwargs):  # noqa: ARG002
            return SimpleNamespace(choices=[])

    class FakeOpenAI:
        def __init__(self, api_key, base_url):  # noqa: ARG002
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setattr("openai.OpenAI", FakeOpenAI)

    assert research._llm_complete("prompt") == ""


# -- run_deep_learn orchestration ----------------------------------------------

def test_run_deep_learn_needs_a_topic(monkeypatch):
    monkeypatch.setattr(research, "preflight", lambda: None)
    with pytest.raises(research.ResearchError, match="topic"):
        research.run_deep_learn("   ")


def test_run_deep_learn_reports_the_preflight_problem(monkeypatch):
    monkeypatch.setattr(research, "preflight", lambda: "needs a key")
    with pytest.raises(research.ResearchError, match="needs a key"):
        research.run_deep_learn("some topic")


def test_run_deep_learn_end_to_end(monkeypatch, tmp_path):
    """decompose -> research each subtopic (search + synthesize) -> gap-check
    -> record, with every external call (search, LLM, vector store) mocked."""
    monkeypatch.setattr(research, "preflight", lambda: None)
    monkeypatch.setattr(
        research,
        "web_search",
        lambda q, count=6: [  # noqa: ARG005
            {"title": "T", "url": "https://x.example", "snippet": "s"}
        ],
    )

    responses = iter(
        [
            '["Basics", "Advanced"]',  # decompose
            "Explanation of Basics (https://x.example)",  # research Basics, query 1
            "More on Basics (https://x.example)",  # research Basics, query 2
            "Explanation of Advanced (https://x.example)",  # research Advanced, query 1
            "More on Advanced (https://x.example)",  # research Advanced, query 2
            "[]",  # gap-find: nothing missing
        ]
    )
    monkeypatch.setattr(research, "_llm_complete", lambda prompt: next(responses))  # noqa: ARG005

    stored = []
    monkeypatch.setattr(
        "core.knowledge.store_notes",
        lambda topic, subtopic, notes, sources: stored.append((topic, subtopic)) or 2,  # noqa: ARG005
    )

    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path), raising=False)
    from core.store import Store, set_store

    store = Store(str(tmp_path / "test.db"))
    set_store(store)
    try:
        result = research.run_deep_learn("Python", depth="quick")
    finally:
        set_store(None)
        store.close()

    assert result["topic"] == "Python"
    assert result["subtopics"] == ["Basics", "Advanced"]
    assert result["gaps_filled"] == []
    assert result["chunks_added"] == 4
    assert stored == [("Python", "Basics"), ("Python", "Advanced")]


def test_run_deep_learn_falls_back_to_the_topic_when_decompose_fails_to_parse(monkeypatch, tmp_path):
    """A parsing hiccup on the subtopic list shouldn't fail the whole run —
    research the topic itself as a single pass instead."""
    monkeypatch.setattr(research, "preflight", lambda: None)
    monkeypatch.setattr(research, "web_search", lambda q, count=6: [])  # noqa: ARG005
    monkeypatch.setattr(research, "_llm_complete", lambda prompt: "not valid json")  # noqa: ARG005

    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path), raising=False)
    from core.store import Store, set_store

    store = Store(str(tmp_path / "test.db"))
    set_store(store)
    try:
        result = research.run_deep_learn("Rust ownership")
    finally:
        set_store(None)
        store.close()

    assert result["subtopics"] == []  # web_search returned [] -> nothing stored
    assert result["chunks_added"] == 0


# -- recording material for publishing ---------------------------------------

@pytest.fixture
def store(tmp_path, monkeypatch):
    """A real Store on a temp file, installed as the process-wide singleton."""
    from core.store import Store, set_store

    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path), raising=False)
    monkeypatch.setattr(config, "NOTES_FILE", str(tmp_path / "notes.txt"), raising=False)
    s = Store(str(tmp_path / "test.db"))
    set_store(s)
    yield s
    set_store(None)
    s.close()


def _fake_search(monkeypatch, url="https://a.example"):
    monkeypatch.setattr(
        "core.research.web_search",
        lambda q: [{"title": "T", "snippet": "S", "url": url}],
    )
    monkeypatch.setattr("core.research._llm_complete", lambda p: "Some notes.")


def test_research_records_notes_and_urls_for_publishing(store, monkeypatch):
    _fake_search(monkeypatch)
    monkeypatch.setattr("core.research.knowledge.store_notes", lambda *a, **k: 3)

    assert research._research_and_store("Rust", "ownership") == 3

    rows = store.pending_knowledge_sources("Rust")
    assert sorted(r["kind"] for r in rows) == ["note", "url"]
    assert any(r["body"] == "https://a.example" for r in rows)


def test_nothing_is_recorded_when_the_vector_store_rejects_the_notes(store, monkeypatch):
    _fake_search(monkeypatch)
    monkeypatch.setattr("core.research.knowledge.store_notes", lambda *a, **k: 0)

    assert research._research_and_store("Rust", "ownership") == 0
    assert store.pending_knowledge_sources("Rust") == []


def test_a_storage_failure_does_not_fail_the_research_step(store, monkeypatch):
    _fake_search(monkeypatch)
    monkeypatch.setattr("core.research.knowledge.store_notes", lambda *a, **k: 3)

    def _boom(*a, **k):
        raise RuntimeError("disk full")

    monkeypatch.setattr(store, "record_knowledge_sources", _boom)

    assert research._research_and_store("Rust", "ownership") == 3


# -- publishing ---------------------------------------------------------------

class _StubPublisher:
    """Captures what research hands to notebooklm.publish_sources."""

    def __init__(self, result):
        self.result = result
        self.calls = []

    def __call__(self, topic, sources, notebook_id=None, client=None):
        self.calls.append((topic, list(sources), notebook_id))
        return self.result


def _ready_to_publish(monkeypatch):
    monkeypatch.setattr("core.research.notebooklm.preflight", lambda: None)


def test_publishing_marks_only_the_rows_that_uploaded(store, monkeypatch):
    from core.notebooklm import PublishResult

    _ready_to_publish(monkeypatch)
    store.record_knowledge_sources("Rust", "ownership", "note", ["A note."])
    store.record_knowledge_sources("Rust", "ownership", "url", ["https://a.example"])
    queued = store.pending_knowledge_sources("Rust")

    result = PublishResult(
        notebook_id="nb-1",
        notebook_url="https://x/1",
        added=1,
        attempted=2,
        published_row_ids=[queued[0]["id"]],
    )
    monkeypatch.setattr("core.research.notebooklm.publish_sources", _StubPublisher(result))

    out = research.publish_topic("Rust")

    assert out["ok"] is True
    assert [r["id"] for r in store.pending_knowledge_sources("Rust")] == [queued[1]["id"]]
    assert store.get_knowledge_notebook("Rust")["notebook_id"] == "nb-1"


def test_publishing_reuses_the_topics_existing_notebook(store, monkeypatch):
    from core.notebooklm import PublishResult

    _ready_to_publish(monkeypatch)
    store.record_knowledge_notebook("Rust", "nb-existing", "https://x/existing")
    store.record_knowledge_sources("Rust", "ownership", "note", ["A note."])
    publisher = _StubPublisher(
        PublishResult(notebook_id="nb-existing", notebook_url="https://x/existing",
                      added=1, attempted=1)
    )
    monkeypatch.setattr("core.research.notebooklm.publish_sources", publisher)

    research.publish_topic("Rust")

    assert publisher.calls[0][2] == "nb-existing"


def test_a_notebook_deleted_by_hand_is_recreated_once(store, monkeypatch):
    from core.notebooklm import PublishResult

    _ready_to_publish(monkeypatch)
    store.record_knowledge_notebook("Rust", "nb-gone", "https://x/gone")
    store.record_knowledge_sources("Rust", "ownership", "note", ["A note."])

    gone = PublishResult(attempted=1, error="not found", missing_notebook=True)
    fresh = PublishResult(notebook_id="nb-new", notebook_url="https://x/new",
                          added=1, attempted=1)
    results = [gone, fresh]
    calls = []

    def _publish(topic, sources, notebook_id=None, client=None):
        calls.append(notebook_id)
        return results.pop(0)

    monkeypatch.setattr("core.research.notebooklm.publish_sources", _publish)

    out = research.publish_topic("Rust")

    assert calls == ["nb-gone", None]
    assert out["ok"] is True
    assert store.get_knowledge_notebook("Rust")["notebook_id"] == "nb-new"


def test_a_missing_notebook_is_not_retried_when_there_was_none(store, monkeypatch):
    from core.notebooklm import PublishResult

    _ready_to_publish(monkeypatch)
    store.record_knowledge_sources("Rust", "ownership", "note", ["A note."])
    publisher = _StubPublisher(
        PublishResult(attempted=1, error="not found", missing_notebook=True)
    )
    monkeypatch.setattr("core.research.notebooklm.publish_sources", publisher)

    out = research.publish_topic("Rust")

    assert len(publisher.calls) == 1
    assert out["ok"] is False


def test_publishing_an_unlearned_topic_says_so(store, monkeypatch):
    _ready_to_publish(monkeypatch)
    monkeypatch.setattr("core.research.knowledge.all_chunks", lambda topic: [])
    out = research.publish_topic("Nothing")
    assert out["ok"] is False
    assert "haven't" in out["message"].lower()


def test_publishing_reports_preflight_problems(store, monkeypatch):
    monkeypatch.setattr("core.research.notebooklm.preflight", lambda: "switched off")
    assert research.publish_topic("Rust") == {"ok": False, "message": "switched off", "url": ""}


def test_publishing_needs_a_topic(store, monkeypatch):
    _ready_to_publish(monkeypatch)
    assert research.publish_topic("   ")["ok"] is False


def test_a_topic_learned_before_the_sources_table_is_reconstructed(store, monkeypatch):
    from core.notebooklm import PublishResult

    _ready_to_publish(monkeypatch)
    chunks = [
        {
            "id": "rust::ownership::1000::abcd::0",
            "text": "First half of the notes.",
            "subtopic": "ownership",
            "sources": "https://a.example | https://b.example",
            "ts": 1000.0,
        }
    ]
    monkeypatch.setattr("core.research.knowledge.all_chunks", lambda topic: chunks)
    publisher = _StubPublisher(
        PublishResult(notebook_id="nb-1", notebook_url="https://x/1", added=3, attempted=3)
    )
    monkeypatch.setattr("core.research.notebooklm.publish_sources", publisher)

    out = research.publish_topic("Rust")

    sent = publisher.calls[0][1]
    assert [s.kind for s in sent] == ["note", "url", "url"]
    assert sent[0].row_id is None
    assert out["ok"] is True


def test_deep_learn_does_not_publish_unless_asked(store, monkeypatch):
    monkeypatch.setattr("core.research.preflight", lambda: None)
    monkeypatch.setattr("core.research._decompose", lambda t, n: ["ownership"])
    monkeypatch.setattr("core.research._research_and_store", lambda t, s: 2)
    monkeypatch.setattr("core.research._find_gaps", lambda t, c: [])
    called = []
    monkeypatch.setattr("core.research.publish_topic", lambda *a, **k: called.append(1))

    result = research.run_deep_learn("Rust")

    assert called == []
    assert "published" not in result


def test_deep_learn_publishes_when_asked(store, monkeypatch):
    monkeypatch.setattr("core.research.preflight", lambda: None)
    monkeypatch.setattr("core.research._decompose", lambda t, n: ["ownership"])
    monkeypatch.setattr("core.research._research_and_store", lambda t, s: 2)
    monkeypatch.setattr("core.research._find_gaps", lambda t, c: [])
    monkeypatch.setattr(
        "core.research.publish_topic",
        lambda topic, progress=None: {"ok": True, "message": "Published it.", "url": "https://x/1"},
    )

    result = research.run_deep_learn("Rust", publish=True)

    assert result["published"] == "Published it."
    assert result["notebook_url"] == "https://x/1"


def test_a_publish_failure_still_returns_the_research(store, monkeypatch):
    monkeypatch.setattr("core.research.preflight", lambda: None)
    monkeypatch.setattr("core.research._decompose", lambda t, n: ["ownership"])
    monkeypatch.setattr("core.research._research_and_store", lambda t, s: 2)
    monkeypatch.setattr("core.research._find_gaps", lambda t, c: [])

    def _boom(*a, **k):
        raise RuntimeError("google is down")

    monkeypatch.setattr("core.research.publish_topic", _boom)

    result = research.run_deep_learn("Rust", publish=True)

    assert result["chunks_added"] == 2
    assert "google is down" in result["published"]
