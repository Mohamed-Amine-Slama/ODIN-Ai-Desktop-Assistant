"""Tests for core/memory_index.py's graceful degradation when chromadb /
sentence-transformers aren't installed — the normal state in this test
environment, and the contract every caller (core.store) depends on."""
from core import memory_index


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
