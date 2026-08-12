"""Semantic search index for durable user memories (the `memory` skill).

core.store's `memories` table stays the system of record — this is a
best-effort mirror kept only for similarity search, the same relationship
core.knowledge has to core.research's fetched content. A failure here must
never break remember()/recall()/forget(), so every public function degrades
to a quiet no-op (index()/remove()) or an empty result (search()) rather than
raising, when chromadb/sentence-transformers aren't installed.

A separate Chroma collection from core.knowledge's "knowledge" collection —
different data, different lifecycle (a memory is removed by an explicit
`forget`; deep_learn notes never are) — but the two share one embedding model
via core.embeddings so neither doubles the ~80MB load.
"""
import os

import config
from core.embeddings import available as _embeddings_available
from core.embeddings import get_embedder

COLLECTION_NAME = "memories"
DB_DIR_NAME = "memory_db"

# Same relevance cutoff core.knowledge uses — Chroma's cosine distance, where
# 0 is identical and 2 is opposite.
RELEVANCE_DISTANCE_MAX = 0.85

_client = None
_collection = None


def available() -> bool:
    """Whether the optional RAG dependencies are installed, without loading
    the (slow, ~seconds-long-on-first-load) embedding model to find out."""
    try:
        import chromadb  # noqa: F401
    except ImportError:
        return False
    return _embeddings_available()


def _get_collection():
    global _client, _collection
    if _collection is None:
        import chromadb  # raises ImportError if missing; caller decides how to degrade

        config.ensure_dirs()
        db_path = os.path.join(config.DATA_DIR, DB_DIR_NAME)
        _client = chromadb.PersistentClient(path=db_path)
        _collection = _client.get_or_create_collection(COLLECTION_NAME)
    return _collection


def index(memory_id: int, text: str) -> None:
    """Embed and upsert one memory row. Silently does nothing if the
    optional deps aren't installed or anything else goes wrong — indexing
    must never be able to break remember()."""
    if not text.strip():
        return
    try:
        collection = _get_collection()
        embedding = get_embedder().encode([text]).tolist()
        collection.upsert(ids=[str(memory_id)], documents=[text], embeddings=embedding)
    except Exception:
        pass


def remove(memory_id: int) -> None:
    """Drop one memory from the index. Silently does nothing on any error —
    forget() must not fail just because the index couldn't keep up."""
    try:
        collection = _get_collection()
        collection.delete(ids=[str(memory_id)])
    except Exception:
        pass


def search(query_text: str, limit: int = 20) -> list[str]:
    """Semantically closest memories to query_text, most relevant first.

    Returns [] rather than raising when the index is empty, unavailable, or
    anything else goes wrong — the caller falls back to a plain LIKE search,
    so "nothing indexed yet" is a normal state, not an error.
    """
    if not query_text.strip():
        return []
    try:
        collection = _get_collection()
        if collection.count() == 0:
            return []
        embedding = get_embedder().encode([query_text]).tolist()
    except Exception:
        return []

    result = collection.query(
        query_embeddings=embedding,
        n_results=min(limit, max(1, collection.count())),
    )
    docs = (result.get("documents") or [[]])[0]
    dists = (result.get("distances") or [[]])[0]
    return [doc for doc, dist in zip(docs, dists) if dist <= RELEVANCE_DISTANCE_MAX]
