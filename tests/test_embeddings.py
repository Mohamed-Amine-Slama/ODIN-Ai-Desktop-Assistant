"""Tests for the shared sentence-embedding loader (core/embeddings.py).

device is intentionally left for sentence-transformers to auto-detect (it
already prefers a CUDA GPU over CPU when the installed torch build supports
one) — these cover that the resolved device gets reported, and that the
model is genuinely loaded once and cached, not the device-selection itself
(that's sentence-transformers' own, not ours to duplicate or fake).
"""
import sys
from types import SimpleNamespace

import pytest


@pytest.fixture(autouse=True)
def _reset_embedder():
    import core.embeddings as embeddings_module

    embeddings_module._embedder = None
    yield
    embeddings_module._embedder = None


def test_get_embedder_reports_the_resolved_device(monkeypatch, capsys):
    import core.embeddings as embeddings_module

    class FakeSentenceTransformer:
        def __init__(self, model_name):
            self.model_name = model_name
            self.device = "cuda:0"

    monkeypatch.setitem(
        sys.modules,
        "sentence_transformers",
        SimpleNamespace(SentenceTransformer=FakeSentenceTransformer),
    )

    embedder = embeddings_module.get_embedder()

    assert embedder.model_name == embeddings_module._MODEL_NAME
    assert "loaded on cuda:0" in capsys.readouterr().out


def test_get_embedder_reports_cpu_too(monkeypatch, capsys):
    """Not just a happy-path GPU message — CPU is reported the same way, so
    'why is this still slow' is answerable from the startup log alone."""
    import core.embeddings as embeddings_module

    class FakeSentenceTransformer:
        def __init__(self, model_name):  # noqa: ARG002
            self.device = "cpu"

    monkeypatch.setitem(
        sys.modules,
        "sentence_transformers",
        SimpleNamespace(SentenceTransformer=FakeSentenceTransformer),
    )

    embeddings_module.get_embedder()

    assert "loaded on cpu" in capsys.readouterr().out


def test_get_embedder_is_loaded_once_and_cached(monkeypatch):
    import core.embeddings as embeddings_module

    built = []

    class FakeSentenceTransformer:
        def __init__(self, model_name):  # noqa: ARG002
            built.append(1)
            self.device = "cpu"

    monkeypatch.setitem(
        sys.modules,
        "sentence_transformers",
        SimpleNamespace(SentenceTransformer=FakeSentenceTransformer),
    )

    first = embeddings_module.get_embedder()
    second = embeddings_module.get_embedder()

    assert first is second
    assert len(built) == 1


def test_get_embedder_reports_missing_package(monkeypatch):
    import core.embeddings as embeddings_module

    monkeypatch.setitem(sys.modules, "sentence_transformers", None)

    with pytest.raises(ImportError, match="sentence-transformers"):
        embeddings_module.get_embedder()


def test_available_does_not_load_the_model(monkeypatch):
    """available() is used just to decide whether to gate a skill's
    registration — it must not pay the slow model-load cost to answer that."""
    import core.embeddings as embeddings_module

    def boom(model_name):  # noqa: ARG001
        raise AssertionError("available() should not construct the model")

    monkeypatch.setitem(
        sys.modules, "sentence_transformers", SimpleNamespace(SentenceTransformer=boom)
    )

    assert embeddings_module.available() is True
