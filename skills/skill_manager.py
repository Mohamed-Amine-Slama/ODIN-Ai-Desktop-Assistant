"""Registers all skills and exposes them as tool definitions in both the
Anthropic and the OpenAI function-calling shapes.

TO ADD A NEW SKILL:
1. Write a class in one of the skills/*.py files that subclasses BaseSkill.
2. Import it below and add it to SKILL_CLASSES.
That's it — the brain will automatically be able to call it.
"""
import config
from .base_skill import BaseSkill, SkillOutcome, SkillResult
from .system_skills import (
    OpenAppSkill,
    CloseAppSkill,
    SystemInfoSkill,
    VolumeControlSkill,
    PowerControlSkill,
)
from .web_skills import (
    OpenWebsiteSkill,
    SearchInBrowserSkill,
    WeatherSkill,
    WebFetchSkill,
    WebSearchSkill,
    google_search_key,
)
from .vision_skills import ScreenshotSkill
from .file_skills import (
    DeleteFileSkill, ListDirSkill, MakeDirSkill, MoveFileSkill,
    ReadFileSkill, SearchFilesSkill, WriteFileSkill,
)
from .window_skills import (
    CloseWindowSkill, FocusWindowSkill, ListWindowsSkill, SetWindowStateSkill,
)
from .utility_skills import (
    TimeDateSkill,
    NoteSkill,
    ReminderSkill,
    ListRemindersSkill,
    MemorySkill,
    CalculatorSkill,
    ClipboardSkill,
    WaitSkill,
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
    ReadFileSkill,
    ListDirSkill,
    SearchFilesSkill,
    WriteFileSkill,
    MakeDirSkill,
    MoveFileSkill,
    DeleteFileSkill,
    ListWindowsSkill,
    FocusWindowSkill,
    SetWindowStateSkill,
    CloseWindowSkill,
    TimeDateSkill,
    NoteSkill,
    ReminderSkill,
    ListRemindersSkill,
    MemorySkill,
    CalculatorSkill,
    ClipboardSkill,
    WaitSkill,
]

# Reading a page needs no credentials, so it is always available. Search does,
# and registering it without one would put a tool in the prompt that can only
# ever answer "I can't" — build_system_prompt keys off the registered set.
SKILL_CLASSES.append(WebFetchSkill)
if google_search_key():
    SKILL_CLASSES.append(WebSearchSkill)

    # deep_learn hard-requires the same grounded search WebSearchSkill uses,
    # so it is gated identically. list_learned_topics only reads what's
    # already stored locally and stays available either way.
    from .knowledge_skills import DeepLearnSkill

    SKILL_CLASSES.append(DeepLearnSkill)

from .knowledge_skills import ListLearnedTopicsSkill

SKILL_CLASSES.append(ListLearnedTopicsSkill)

if getattr(config, "ENABLE_SHELL", True):
    from .shell_skills import RunCommandSkill

    SKILL_CLASSES.append(RunCommandSkill)

if getattr(config, "ENABLE_INPUT_CONTROL", True):
    from .input_skills import ClickSkill, PressKeysSkill, TypeTextSkill

    SKILL_CLASSES.extend([TypeTextSkill, PressKeysSkill, ClickSkill])

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
        """Anthropic shape: local skills first, then server tools. Order is
        deterministic so the rendered tool block stays byte-identical across
        requests, which is what keeps the prompt cache warm.

        A local skill sharing a name with a server tool is dropped here — the
        server-side one is better on this provider, and declaring the same tool
        name twice is a hard 400.
        """
        reserved = {t["name"] for t in SERVER_TOOLS}
        local = [s.to_tool_definition() for s in self.skills.values() if s.name not in reserved]
        return local + list(SERVER_TOOLS)

    def openai_tool_definitions(self) -> list[dict]:
        """Expose local skills formatted as OpenAI tools."""
        tools = []
        for s in self.skills.values():
            tools.append({
                "type": "function",
                "function": {
                    "name": s.name,
                    "description": s.description,
                    "parameters": s.input_schema,
                },
            })
        return tools

    def is_local(self, tool_name: str) -> bool:
        return tool_name in self.skills

    def get(self, tool_name: str) -> BaseSkill | None:
        return self.skills.get(tool_name)

    def execute(self, tool_name: str, tool_input: dict) -> SkillOutcome:
        """Run a local skill.

        is_error tells the model the call failed so it can adapt, rather than
        reporting our error string back to the user as if it were an answer.
        """
        skill = self.skills.get(tool_name)
        if not skill:
            return SkillOutcome(f"Unknown skill: {tool_name}", is_error=True)

        from core.undo import get_journal

        journal = get_journal()
        before = journal.latest()

        try:
            content = skill.run(**tool_input)
        except TypeError as e:
            # Almost always the model passing arguments the schema doesn't match.
            return SkillOutcome(f"Invalid arguments for {tool_name}: {e}", is_error=True)
        except Exception as e:
            return SkillOutcome(f"Error running {tool_name}: {e}", is_error=True)

        # A skill that recorded an undo entry during run() is reversible.
        after = journal.latest()
        token = after.token if after is not None and after is not before else None
        return SkillOutcome(content, is_error=False, undo_token=token)
