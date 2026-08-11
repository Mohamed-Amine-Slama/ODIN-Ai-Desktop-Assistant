"""Local long-term knowledge store for the deep_learn skill (RAG).

This is deliberately separate from core.store: that's small, structured rows
(messages, notes, reminders); this is unstructured text at a scale where a
vector index earns its keep. Everything here is optional infrastructure — if
chromadb/sentence-transformers aren't installed, every function raises
ImportError with an actionable message, and callers (the skill) turn that into
a plain-English reply instead of a crash.

Nothing here talks to a network. Fetching content is core.research's job;
this module only chunks, embeds, and retrieves what it's handed.
"""
import os
import re
import time

import config

COLLECTION_NAME = "knowledge"
DB_DIR_NAME = "knowledge_db"

# Chunk by words, not characters, so a chunk is always a coherent stretch of
# text for the embedding model rather than a mid-word cut.
CHUNK_WORDS = 180
CHUNK_OVERLAP_WORDS = 30

# Chroma reports cosine distance (0 = identical, 2 = opposite). Above this, a
# retrieved chunk is treated as noise rather than a real match — used both to
# filter what's shown to the model and to decide a self-check question is a
# genuine gap rather than something already covered.
RELEVANCE_DISTANCE_MAX = 0.85

_embedder = None
_client = None
_collection = None


def _get_embedder():
    global _embedder
    if _embedder is None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as e:
            raise ImportError(
                "Local knowledge needs the 'sentence-transformers' package. "
                "Run: pip install -r requirements-rag.txt"
            ) from e
        _embedder = SentenceTransformer("all-MiniLM-L6-v2")
    return _embedder


def _get_collection():
    global _client, _collection
    if _collection is None:
        try:
            import chromadb
        except ImportError as e:
            raise ImportError(
                "Local knowledge needs the 'chromadb' package. "
                "Run: pip install -r requirements-rag.txt"
            ) from e
        config.ensure_dirs()
        db_path = os.path.join(config.DATA_DIR, DB_DIR_NAME)
        _client = chromadb.PersistentClient(path=db_path)
        _collection = _client.get_or_create_collection(COLLECTION_NAME)
    return _collection


def available() -> bool:
    """Whether the optional RAG dependencies are installed, without importing
    the (slow, ~seconds-long-on-first-load) embedding model to find out."""
    try:
        import chromadb  # noqa: F401
        import sentence_transformers  # noqa: F401
    except ImportError:
        return False
    return True


def chunk_text(text: str, size: int = CHUNK_WORDS, overlap: int = CHUNK_OVERLAP_WORDS) -> list[str]:
    words = text.split()
    if not words:
        return []
    if len(words) <= size:
        return [" ".join(words)]

    step = max(1, size - overlap)
    chunks = []
    for start in range(0, len(words), step):
        piece = words[start : start + size]
        if not piece:
            break
        chunks.append(" ".join(piece))
        if start + size >= len(words):
            break
    return chunks


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.strip().lower()).strip("-") or "x"


def store_notes(topic: str, subtopic: str, notes: str, sources: list[str]) -> int:
    """Chunk, embed, and persist researched notes for one subtopic.

    Returns the number of chunks stored (0 if there was nothing usable).
    """
    chunks = chunk_text(notes)
    if not chunks:
        return 0

    collection = _get_collection()
    embeddings = _get_embedder().encode(chunks).tolist()
    source_str = " | ".join(sources[:5])
    ts = time.time()
    base_id = f"{_slug(topic)}::{_slug(subtopic)}::{int(ts * 1000)}"
    ids = [f"{base_id}::{i}" for i in range(len(chunks))]
    metadatas = [
        {"topic": topic, "subtopic": subtopic, "sources": source_str, "ts": ts}
        for _ in chunks
    ]

    collection.upsert(ids=ids, documents=chunks, embeddings=embeddings, metadatas=metadatas)
    return len(chunks)


def query(query_text: str, topic: str | None = None, n_results: int = 5) -> list[dict]:
    """Retrieve the most relevant stored chunks for a query.

    Returns [] rather than raising when the store is empty or unavailable —
    "nothing learned yet" is a normal state, not an error.
    """
    if not query_text.strip():
        return []
    try:
        collection = _get_collection()
        if collection.count() == 0:
            return []
        embedding = _get_embedder().encode([query_text]).tolist()
    except ImportError:
        return []

    where = {"topic": topic} if topic else None
    result = collection.query(
        query_embeddings=embedding,
        n_results=min(n_results, max(1, collection.count())),
        where=where,
    )

    hits = []
    docs = (result.get("documents") or [[]])[0]
    metas = (result.get("metadatas") or [[]])[0]
    dists = (result.get("distances") or [[]])[0]
    for doc, meta, dist in zip(docs, metas, dists):
        if dist > RELEVANCE_DISTANCE_MAX:
            continue
        hits.append(
            {
                "text": doc,
                "topic": meta.get("topic", ""),
                "subtopic": meta.get("subtopic", ""),
                "sources": meta.get("sources", ""),
                "distance": dist,
            }
        )
    return hits


def best_distance(query_text: str, topic: str | None = None) -> float:
    """Distance of the single closest stored chunk, or infinity if none."""
    hits = query(query_text, topic=topic, n_results=1)
    return hits[0]["distance"] if hits else float("inf")
