"""Base class every skill must implement. Adding a new skill is just
subclassing this and registering it in skill_manager.py's SKILL_CLASSES list."""
from abc import ABC, abstractmethod
from typing import Union

# A skill returns either a plain string, or a list of Anthropic content blocks
# (used by e.g. the screenshot skill to hand an image back to the model).
SkillResult = Union[str, list[dict]]


class BaseSkill(ABC):
    # Unique tool name Claude will call (snake_case, no spaces)
    name: str = "base_skill"

    # Shown to Claude so it knows when to use this skill
    description: str = "Describe what this skill does and when to use it."

    # JSON schema for the parameters this skill accepts (Anthropic tool format)
    input_schema: dict = {"type": "object", "properties": {}, "required": []}

    # When True, the brain asks the user before running this skill. Use for
    # anything hard to undo — shutting down, killing processes, deleting files.
    requires_confirmation: bool = False

    @abstractmethod
    def run(self, **kwargs) -> SkillResult:
        """Execute the skill and return a short result to report back to the
        user (this gets read out loud / printed)."""
        raise NotImplementedError

    def needs_confirmation(self, **kwargs) -> bool:
        """Per-call override. Lets a skill gate only its dangerous arguments —
        e.g. power_control confirms 'shutdown' but not 'lock'."""
        return self.requires_confirmation

    def confirmation_prompt(self, **kwargs) -> str:
        """Human-readable description of what is about to happen, phrased as a
        yes/no question for the user."""
        return f"Run {self.name} with {kwargs}?"

    def to_tool_definition(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }
