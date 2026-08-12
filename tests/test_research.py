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
    assert "requirements-rag.txt" in research.preflight()


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
