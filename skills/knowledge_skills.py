"""Skills for the local long-term knowledge base (RAG): deep_learn researches
and permanently remembers a topic; list_learned_topics reports what's already
known. Retrieval itself isn't a skill — core.brain injects relevant stored
notes into context automatically, the same way it does with core.store
memories, so there's nothing for the model to call to use them."""
import datetime
import json

from core import research
from core.store import get_store

from .base_skill import BaseSkill


class DeepLearnSkill(BaseSkill):
    name = "deep_learn"
    description = (
        "Research a topic in depth and permanently remember it, so future "
        "questions about it can be answered from what was learned instead of "
        "a fresh guess. Use when the user asks you to learn, study, master, "
        "or 'get up to speed on' a subject — not for an ordinary question, "
        "which web_search already answers. Takes roughly a minute or two: "
        "several searches, note-taking, and a self-check pass that fills "
        "gaps it finds. Tell the user you're starting before calling this."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "topic": {
                "type": "string",
                "description": "The subject to research, e.g. 'React hooks'.",
            },
            "depth": {
                "type": "string",
                "enum": ["quick", "standard", "deep"],
                "description": (
                    "How thorough to be. 'standard' (default) covers about 5 "
                    "subtopics; 'quick' covers 3; 'deep' covers 8."
                ),
            },
        },
        "required": ["topic"],
    }

    def run(self, topic: str, depth: str = "standard") -> str:
        try:
            result = research.run_deep_learn(topic, depth=depth)
        except research.ResearchError as e:
            return str(e)

        subtopics = result["subtopics"]
        lines = [
            f"Learned about \"{result['topic']}\": "
            + (", ".join(subtopics) if subtopics else "nothing usable was found")
            + "."
        ]
        if result["gaps_filled"]:
            lines.append(f"Also filled gaps in: {', '.join(result['gaps_filled'])}.")
        lines.append(f"Stored {result['chunks_added']} notes chunks — ask me anything about it.")
        return " ".join(lines)


class ListLearnedTopicsSkill(BaseSkill):
    name = "list_learned_topics"
    description = "List the topics deep_learn has already researched and remembered."
    input_schema = {"type": "object", "properties": {}, "required": []}

    def run(self) -> str:
        rows = get_store().list_knowledge_topics()
        if not rows:
            return "I haven't deep-learned anything yet."

        lines = []
        for row in rows:
            subtopics = json.loads(row["subtopics"])
            when = datetime.datetime.fromtimestamp(row["updated_at"]).strftime("%Y-%m-%d")
            lines.append(
                f"- {row['topic']} ({row['chunk_count']} notes, updated {when}): "
                + ", ".join(subtopics)
            )
        return "Topics I've deep-learned:\n" + "\n".join(lines)
