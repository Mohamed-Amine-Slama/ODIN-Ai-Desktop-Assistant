"""Registers all skills and exposes them as Claude tool definitions.

TO ADD A NEW SKILL:
1. Write a class in one of the skills/*.py files that subclasses BaseSkill.
2. Import it below and add it to SKILL_CLASSES.
That's it — the brain will automatically be able to call it.
"""
from .base_skill import BaseSkill, SkillResult
from .system_skills import (
    OpenAppSkill,
    CloseAppSkill,
    SystemInfoSkill,
    VolumeControlSkill,
    PowerControlSkill,
)
from .web_skills import OpenWebsiteSkill, SearchInBrowserSkill, WeatherSkill
from .vision_skills import ScreenshotSkill
from .utility_skills import (
    TimeDateSkill,
    NoteSkill,
    ReminderSkill,
    CalculatorSkill,
    ClipboardSkill,
)

SKILL_CLASSES = [
    OpenAppSkill,
    CloseAppSkill,
    SystemInfoSkill,
    VolumeControlSkill,
    PowerControlSkill,
    OpenWebsiteSkill,
    SearchInBrowserSkill,
    WeatherSkill,
    ScreenshotSkill,
    TimeDateSkill,
    NoteSkill,
    ReminderSkill,
    CalculatorSkill,
    ClipboardSkill,
]

# Anthropic-hosted tools. These execute on Anthropic's servers and their results
# arrive inline as content blocks — SkillManager never runs them and `execute()`
# is never called with these names.
#
# The _20260209 variants do dynamic filtering (they run code server-side to
# filter results before they reach the context window). Do NOT also declare a
# code_execution tool: a second execution environment confuses the model.
SERVER_TOOLS: list[dict] = [
    {"type": "web_search_20260209", "name": "web_search"},
    {"type": "web_fetch_20260209", "name": "web_fetch"},
]


class SkillManager:
    def __init__(self):
        self.skills: dict[str, BaseSkill] = {}
        for cls in SKILL_CLASSES:
            skill = cls()
            self.skills[skill.name] = skill

    def tool_definitions(self) -> list[dict]:
        """Local skills first, then server tools. Order is deterministic so the
        rendered tool block stays byte-identical across requests, which is what
        keeps the prompt cache warm."""
        return [s.to_tool_definition() for s in self.skills.values()] + list(SERVER_TOOLS)

    def is_local(self, tool_name: str) -> bool:
        return tool_name in self.skills

    def get(self, tool_name: str) -> BaseSkill | None:
        return self.skills.get(tool_name)

    def execute(self, tool_name: str, tool_input: dict) -> tuple[SkillResult, bool]:
        """Run a local skill. Returns (content, is_error).

        is_error tells the model the call failed so it can adapt, rather than
        reporting our error string back to the user as if it were an answer.
        """
        skill = self.skills.get(tool_name)
        if not skill:
            return f"Unknown skill: {tool_name}", True
        try:
            return skill.run(**tool_input), False
        except TypeError as e:
            # Almost always the model passing arguments the schema doesn't match.
            return f"Invalid arguments for {tool_name}: {e}", True
        except Exception as e:
            return f"Error running {tool_name}: {e}", True
