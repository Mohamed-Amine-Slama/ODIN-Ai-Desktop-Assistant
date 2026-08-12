"""Shared sentence-embedding model.

Used by core.knowledge (deep_learn's RAG store) and core.memory_index
(semantic recall for the memory skill) — factored out here so both share one
lazily-loaded ~80MB model instance instead of each loading their own copy.

Optional dependency, same as the rest of the RAG stack: if
sentence-transformers isn't installed, get_embedder() raises ImportError with
an actionable message, and callers turn that into a plain-English reply
instead of a crash.
"""

_embedder = None
_MODEL_NAME = "all-MiniLM-L6-v2"


def get_embedder():
    """The shared SentenceTransformer instance, loaded on first use."""
    global _embedder
    if _embedder is None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as e:
            raise ImportError(
                "This needs the 'sentence-transformers' package. "
                "Run: pip install -r requirements-rag.txt"
            ) from e
        _embedder = SentenceTransformer(_MODEL_NAME)
    return _embedder


def available() -> bool:
    """Whether sentence-transformers is installed, without loading the
    (slow, ~seconds-long-on-first-load) model to find out."""
    try:
        import sentence_transformers  # noqa: F401
    except ImportError:
        return False
    return True
