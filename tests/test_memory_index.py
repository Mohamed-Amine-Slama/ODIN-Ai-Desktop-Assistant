"""Tests for core/memory_index.py's graceful degradation when chromadb /
sentence-transformers aren't installed — forced via sys.modules (same
technique test_capabilities.py uses for ddgs) so this holds regardless of
whether the dev/CI machine actually has the optional RAG stack installed,
and so it never does a real chromadb write as an accidental side effect."""
import sys

import pytest

from core import memory_index


@pytest.fixture(autouse=True)
def _no_rag_deps(monkeypatch):
    monkeypatch.setitem(sys.modules, "chromadb", None)
    monkeypatch.setitem(sys.modules, "sentence_transformers", None)


def test_available_is_false_without_the_optional_deps():
    assert memory_index.available() is False


def test_index_is_a_silent_noop_without_deps():
    memory_index.index(1, "some fact")  # must not raise


def test_remove_is_a_silent_noop_without_deps():
    memory_index.remove(1)  # must not raise


def test_search_returns_empty_list_without_deps():
    assert memory_index.search("anything") == []


def test_search_with_blank_query_returns_empty_list():
    assert memory_index.search("   ") == []


def test_search_degrades_to_empty_list_when_query_itself_raises(monkeypatch):
    """The module's own docstring promises search() 'returns [] rather than
    raising ... when ... anything else goes wrong' — that has to cover a
    real failure from collection.query() itself (corrupted collection, a
    chromadb version mismatch, disk I/O), not just chromadb/
    sentence-transformers being absent."""

    class _BoomCollection:
        def count(self):
            return 1

        def query(self, **kwargs):  # noqa: ARG002
            raise RuntimeError("boom")

    class _FakeEmbedder:
        def encode(self, texts):  # noqa: ARG002
            class _Vec:
                def tolist(self):
                    return [[0.0]]

            return _Vec()

    monkeypatch.setattr(memory_index, "_get_collection", lambda: _BoomCollection())
    monkeypatch.setattr(memory_index, "get_embedder", lambda: _FakeEmbedder())

    assert memory_index.search("anything") == []
