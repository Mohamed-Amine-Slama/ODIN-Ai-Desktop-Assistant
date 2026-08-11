"""Agentic research pipeline behind the deep_learn skill.

topic -> subtopics -> grounded web research per subtopic -> notes chunked and
embedded into the local vector store (core.knowledge) -> a self-check pass
that asks what a learner would need to answer to prove mastery, and researches
whatever the stored notes can't already answer.

This is the only place that runs multiple LLM calls outside of a normal brain
turn, so it talks to Gemini directly via skills.web_skills.gemini_generate
rather than going through core.brain.Brain — a research run has its own
control flow (loops, branches, no user waiting on each step) that doesn't fit
the tool-calling turn loop.

Requires Google Search grounding (skills.web_skills.google_search_key()) —
that's the only web-search capability this codebase implements locally.
"""
import json
import re

from core import knowledge
from core.store import get_store
from skills.web_skills import gemini_generate, google_search_key

DEPTH_SUBTOPICS = {"quick": 3, "standard": 5, "deep": 8}
SEARCHES_PER_SUBTOPIC = 2
MAX_GAP_FILLS = 3

_URL_RE = re.compile(r"\((https?://[^)\s]+)\)")


class ResearchError(Exception):
    """Raised for a condition that should be shown to the user verbatim."""


def preflight() -> str | None:
    """Return a user-facing reason deep_learn can't run, or None if it can."""
    if not google_search_key():
        return (
            "Deep learning needs Google Search grounding. Set GOOGLE_API_KEY "
            "in .env, or point BASE_URL at Gemini."
        )
    if not knowledge.available():
        return (
            "Deep learning needs the local knowledge packages. "
            "Run: pip install -r requirements-rag.txt"
        )
    return None


def run_deep_learn(topic: str, depth: str = "standard", progress=None) -> dict:
    """Research a topic and store what it finds. progress, if given, is called
    with a short status string before each research step."""
    topic = topic.strip()
    if not topic:
        raise ResearchError("I need a topic to research.")

    problem = preflight()
    if problem:
        raise ResearchError(problem)
    key = google_search_key()

    def note(msg: str) -> None:
        if progress is not None:
            try:
                progress(msg)
            except Exception:
                pass

    n = DEPTH_SUBTOPICS.get(depth, DEPTH_SUBTOPICS["standard"])
    note(f"Breaking '{topic}' down into subtopics...")
    subtopics = _decompose(topic, n, key)

    covered: list[str] = []
    added_chunks = 0
    for sub in subtopics:
        note(f"Researching: {sub}")
        chunks = _research_and_store(topic, sub, key)
        added_chunks += chunks
        if chunks:
            covered.append(sub)

    note("Checking for gaps...")
    gaps = _find_gaps(topic, covered, key)
    filled: list[str] = []
    for gap in gaps[:MAX_GAP_FILLS]:
        note(f"Filling a gap: {gap}")
        chunks = _research_and_store(topic, gap, key)
        added_chunks += chunks
        if chunks:
            filled.append(gap)

    store = get_store()
    previous = store.get_knowledge_topic(topic)
    prior_subtopics = json.loads(previous["subtopics"]) if previous else []
    prior_chunks = previous["chunk_count"] if previous else 0
    merged_subtopics = list(dict.fromkeys(prior_subtopics + covered + filled))
    store.record_knowledge_topic(topic, merged_subtopics, prior_chunks + added_chunks)

    return {
        "topic": topic,
        "subtopics": covered,
        "gaps_filled": filled,
        "chunks_added": added_chunks,
    }


def _decompose(topic: str, n: int, key: str) -> list[str]:
    prompt = (
        f'List exactly {n} distinct subtopics a learner needs to study to build '
        f'a solid working understanding of "{topic}". Order them from '
        "foundational to advanced. Reply with ONLY a JSON array of short "
        "subtopic strings, nothing else."
    )
    try:
        raw = gemini_generate(prompt, key, grounded=False)
    except RuntimeError:
        raw = ""
    subtopics = _parse_json_list(raw)
    # A parsing hiccup shouldn't fail the whole run — fall back to researching
    # the topic itself as a single pass.
    return subtopics[:n] if subtopics else [topic]


def _research_and_store(topic: str, subtopic: str, key: str) -> int:
    queries = [
        f"{subtopic}, in the context of {topic} — explain clearly with examples",
        f"{subtopic} ({topic}): common mistakes, best practices, and advanced tips",
    ][:SEARCHES_PER_SUBTOPIC]

    parts: list[str] = []
    sources: list[str] = []
    for q in queries:
        try:
            answer = gemini_generate(q, key, grounded=True)
        except RuntimeError:
            continue
        if answer:
            parts.append(answer)
            sources.extend(_URL_RE.findall(answer))

    if not parts:
        return 0
    notes = "\n\n".join(parts)
    return knowledge.store_notes(topic, subtopic, notes, _dedupe(sources))


def _find_gaps(topic: str, covered_subtopics: list[str], key: str) -> list[str]:
    """What a mastery quiz would ask, filtered down to what's NOT already well
    covered by what's stored (a poor vector-store match stands in for "the
    self-check answer wasn't confident")."""
    if not covered_subtopics:
        return []

    prompt = (
        f'A learner has just studied "{topic}", covering: '
        + ", ".join(covered_subtopics)
        + ". List up to 5 short quiz questions someone would need to answer to "
        "prove real mastery of this material — favor questions that probe "
        "edge cases, pitfalls, or connections between subtopics rather than "
        "restating definitions. Reply with ONLY a JSON array of question "
        "strings, nothing else."
    )
    try:
        raw = gemini_generate(prompt, key, grounded=False)
    except RuntimeError:
        return []

    gaps = []
    for question in _parse_json_list(raw):
        if knowledge.best_distance(question, topic=topic) > knowledge.RELEVANCE_DISTANCE_MAX:
            gaps.append(question.strip().rstrip("?")[:80])
    return gaps


def _parse_json_list(text: str) -> list[str]:
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if not match:
        return []
    try:
        data = json.loads(match.group(0))
    except (json.JSONDecodeError, ValueError, TypeError):
        return []
    if not isinstance(data, list):
        return []
    return [str(item).strip() for item in data if str(item).strip()]


def _dedupe(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))
