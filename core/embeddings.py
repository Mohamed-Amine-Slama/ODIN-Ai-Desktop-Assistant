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
    """The shared SentenceTransformer instance, loaded on first use.

    device is left unset so sentence-transformers auto-detects it — it
    already prefers a CUDA GPU over CPU when the installed torch build
    supports one. That auto-detection is opaque from the outside, though, so
    this reports what it actually picked, the same way core.speech_input
    does for the STT model — GPU use should be something you can verify.
    """
    global _embedder
    if _embedder is None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as e:
            raise ImportError(
                "This needs the 'sentence-transformers' package. "
                "Run: pip install -r requirements.txt"
            ) from e
        _embedder = SentenceTransformer(_MODEL_NAME)
        print(f"[embeddings] '{_MODEL_NAME}' loaded on {_embedder.device}")
    return _embedder


def available() -> bool:
    """Whether sentence-transformers is installed, without loading the
    (slow, ~seconds-long-on-first-load) model to find out."""
    try:
        import sentence_transformers  # noqa: F401
    except ImportError:
        return False
    return True
