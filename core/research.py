"""Agentic research pipeline behind the deep_learn skill.

topic -> subtopics -> web research per subtopic (DuckDuckGo search +
synthesis) -> notes chunked and embedded into the local vector store
(core.knowledge) -> a
self-check pass that asks what a learner would need to answer to prove
mastery, and researches whatever the stored notes can't already answer.

This is the only place that runs multiple LLM calls outside of a normal brain
turn, so it talks to a model directly via _llm_complete rather than going
through core.brain.Brain — a research run has its own control flow (loops,
branches, no user waiting on each step) that doesn't fit the tool-calling
turn loop.

Two ingredients, from two different places: DuckDuckGo search
(skills.web_skills, via the `ddgs` package — no key needed) for raw results,
and whatever LLM Jarvis itself is configured with (config.API_KEY/BASE_URL/
MODEL) to decompose topics, synthesize notes from those raw results, and
self-check for gaps. Search and reasoning are deliberately decoupled — unlike
the old Gemini-grounding approach, this works with any provider Jarvis is
already configured to use, and needs no separate API key at all for search.
"""
import json
import re

import config
from core import knowledge, learning_status, notebooklm
from core.store import get_store
from skills.web_skills import web_search, web_search_available

DEPTH_SUBTOPICS = {"quick": 3, "standard": 5, "deep": 8}
SEARCHES_PER_SUBTOPIC = 2
MAX_GAP_FILLS = 3

_URL_RE = re.compile(r"\((https?://[^)\s]+)\)")


class ResearchError(Exception):
    """Raised for a condition that should be shown to the user verbatim."""


def preflight() -> str | None:
    """Return a user-facing reason deep_learn can't run, or None if it can."""
    if not web_search_available():
        return "Deep learning needs the 'ddgs' package. Run: pip install ddgs"
    if not knowledge.available():
        return (
            "Deep learning needs the local knowledge packages. "
            "Run: pip install -r requirements.txt"
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

    def note(msg: str) -> None:
        if progress is not None:
            try:
                progress(msg)
            except Exception:
                pass

    def report(subtopic: str, done: int, total: int) -> None:
        """Structured progress for anything watching (the HUD's voice orb) —
        independent of `progress` above, so it fires for every deep_learn
        run regardless of who's calling it, not just callers that pass a
        `progress` callback."""
        if total > 0:
            learning_status.report(topic, subtopic, min(1.0, done / total))

    n = DEPTH_SUBTOPICS.get(depth, DEPTH_SUBTOPICS["standard"])
    note(f"Breaking '{topic}' down into subtopics...")
    subtopics = _decompose(topic, n)
    total_steps = len(subtopics) + MAX_GAP_FILLS

    covered: list[str] = []
    added_chunks = 0
    for i, sub in enumerate(subtopics):
        note(f"Researching: {sub}")
        report(sub, i, total_steps)
        chunks = _research_and_store(topic, sub)
        added_chunks += chunks
        if chunks:
            covered.append(sub)

    note("Checking for gaps...")
    gaps = _find_gaps(topic, covered)
    filled: list[str] = []
    for i, gap in enumerate(gaps[:MAX_GAP_FILLS]):
        note(f"Filling a gap: {gap}")
        report(gap, len(subtopics) + i, total_steps)
        chunks = _research_and_store(topic, gap)
        added_chunks += chunks
        if chunks:
            filled.append(gap)

    report(topic, total_steps, total_steps)

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


def _decompose(topic: str, n: int) -> list[str]:
    prompt = (
        f'List exactly {n} distinct subtopics a learner needs to study to build '
        f'a solid working understanding of "{topic}". Order them from '
        "foundational to advanced. Reply with ONLY a JSON array of short "
        "subtopic strings, nothing else."
    )
    try:
        raw = _llm_complete(prompt)
    except RuntimeError:
        raw = ""
    subtopics = _parse_json_list(raw)
    # A parsing hiccup shouldn't fail the whole run — fall back to researching
    # the topic itself as a single pass.
    return subtopics[:n] if subtopics else [topic]


def _research_and_store(topic: str, subtopic: str) -> int:
    queries = [
        f"{subtopic} {topic} explained examples",
        f"{subtopic} {topic} common mistakes best practices advanced tips",
    ][:SEARCHES_PER_SUBTOPIC]

    parts: list[str] = []
    sources: list[str] = []
    for q in queries:
        try:
            results = web_search(q)
        except RuntimeError:
            continue
        if not results:
            continue

        digest = "\n".join(f"- {r['title']}: {r['snippet']} ({r['url']})" for r in results)
        prompt = (
            f'Using ONLY the search results below, write a clear, accurate '
            f'explanation of "{subtopic}" in the context of "{topic}", with '
            f"examples where useful. Cite sources inline as (url).\n\n"
            f"Search results:\n{digest}"
        )
        try:
            answer = _llm_complete(prompt)
        except RuntimeError:
            continue
        if answer:
            parts.append(answer)
            sources.extend(r["url"] for r in results if r.get("url"))

    if not parts:
        return 0

    notes = "\n\n".join(parts)
    urls = _dedupe(sources)
    stored = knowledge.store_notes(topic, subtopic, notes, urls)
    if stored:
        # Chroma keeps 180-word chunks and at most five joined URLs per chunk —
        # right for retrieval, lossy for anything that wants the notes whole.
        # Keep the raw material too, but only after store_notes succeeded:
        # SQLite must never claim material the vector store rejected.
        _record_sources(topic, subtopic, notes, urls)
    return stored


def _record_sources(topic: str, subtopic: str, notes: str, urls: list[str]) -> None:
    """Persist one research step's material so it can be published later.

    Never raises: the notes are already safely in the vector store by the time
    this runs, and a research run must not fail over bookkeeping for a feature
    the user may not even have switched on.
    """
    try:
        store = get_store()
        store.record_knowledge_sources(topic, subtopic, notebooklm.NOTE_KIND, [notes])
        store.record_knowledge_sources(topic, subtopic, notebooklm.URL_KIND, urls)
    except Exception as e:  # noqa: BLE001 - bookkeeping must not break research
        print(f"[research] couldn't record sources for publishing: {e}")


def _find_gaps(topic: str, covered_subtopics: list[str]) -> list[str]:
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
        raw = _llm_complete(prompt)
    except RuntimeError:
        return []

    gaps = []
    for question in _parse_json_list(raw):
        if knowledge.best_distance(question, topic=topic) > knowledge.RELEVANCE_DISTANCE_MAX:
            gaps.append(question.strip().rstrip("?")[:80])
    return gaps


def _llm_complete(prompt: str) -> str:
    """One-off, non-streaming completion using whatever provider Jarvis
    itself is configured with. Decomposing a topic, synthesizing notes from
    search results, and self-checking for gaps are all plain reasoning over
    material already in hand — they don't need Brain's tool loop, streaming,
    or a specific provider's search grounding, just a text-in/text-out call.

    Mirrors core.brain.Brain's own provider selection (native Anthropic when
    MODEL names a Claude or BASE_URL is unset, OpenAI-compatible otherwise)
    in miniature, rather than depending on Brain itself.
    """
    if config.MODEL.startswith("claude") or not config.BASE_URL:
        import anthropic

        client = anthropic.Anthropic(api_key=config.API_KEY, base_url=config.BASE_URL or None)
        try:
            response = client.messages.create(
                model=config.MODEL,
                max_tokens=1024,
                messages=[{"role": "user", "content": prompt}],
            )
        except Exception as e:
            raise RuntimeError(f"couldn't reach the model: {e}") from e
        return "".join(b.text for b in response.content if b.type == "text").strip()

    import openai

    client = openai.OpenAI(api_key=config.API_KEY, base_url=config.BASE_URL or None)
    try:
        response = client.chat.completions.create(
            model=config.MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1024,
        )
        # A gateway can legitimately return zero choices (content filtered,
        # malformed upstream response) — treated as "nothing useful came
        # back" like any other empty completion, not a failure to reach the
        # model. Reading choices[0] unconditionally would raise IndexError
        # outside this try, which every caller here only catches as
        # RuntimeError — an uncaught IndexError would abort the whole
        # deep_learn run instead of just skipping this one step.
        if not response.choices:
            return ""
        return (response.choices[0].message.content or "").strip()
    except Exception as e:
        raise RuntimeError(f"couldn't reach the model: {e}") from e


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
