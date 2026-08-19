"""core/knowledge.py's startup warm-up.

Importing chromadb costs ~2.6s, and it used to happen inside the first turn —
measured with cProfile, `_knowledge_context` -> `_get_collection` -> chromadb's
own import, all of it between the user asking and the request going out. The
app now pays that during startup, off the critical path.
"""
import core.knowledge as knowledge


def _reset():
    knowledge._client = None
    knowledge._collection = None


def test_warm_up_loads_the_collection_ahead_of_the_first_turn(monkeypatch):
    _reset()
    loaded = []
    monkeypatch.setattr(knowledge, "_get_collection", lambda: loaded.append(1) or "collection")

    assert knowledge.warm_up() is True
    assert loaded == [1]


def test_warm_up_is_silent_when_the_optional_deps_are_missing(monkeypatch):
    """RAG is optional. A machine without chromadb must still start, and must
    not surface an import error from a background thread."""
    _reset()

    def _raise():
        raise ImportError("no chromadb here")

    monkeypatch.setattr(knowledge, "_get_collection", _raise)

    assert knowledge.warm_up() is False


def test_warm_up_survives_a_broken_store(monkeypatch):
    """A corrupt or locked database must not take the launch down with it."""
    _reset()

    def _raise():
        raise RuntimeError("database is locked")

    monkeypatch.setattr(knowledge, "_get_collection", _raise)

    assert knowledge.warm_up() is False
