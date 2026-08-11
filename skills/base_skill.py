"""Base class every skill must implement. Adding a new skill is just
subclassing this and registering it in skill_manager.py's SKILL_CLASSES list."""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Union

from core.risk import Risk

# A skill returns either a plain string, or a list of Anthropic content blocks
# (used by e.g. the screenshot skill to hand an image back to the model).
SkillResult = Union[str, list[dict]]


@dataclass
class SkillOutcome:
    """What running a skill produced.

    undo_token is set only when the action can genuinely be reversed — the
    caller uses its presence to decide whether to offer an undo affordance,
    so a skill that cannot undo must leave it None rather than lie.
    """

    content: SkillResult
    is_error: bool = False
    undo_token: str | None = None


class BaseSkill(ABC):
    # Unique tool name Claude will call (snake_case, no spaces)
    name: str = "base_skill"

    # Shown to Claude so it knows when to use this skill
    description: str = "Describe what this skill does and when to use it."

    # JSON schema for the parameters this skill accepts (Anthropic tool format)
    input_schema: dict = {"type": "object", "properties": {}, "required": []}

    # How much friction this skill's actions deserve. Nothing is ever blocked;
    # this only decides silent / undoable / ask-first.
    risk: Risk = Risk.SAFE

    @abstractmethod
    def run(self, **kwargs) -> SkillResult:
        """Execute the skill and return a short result to report back to the
        user (this gets read out loud / printed)."""
        raise NotImplementedError

    def risk_for(self, **kwargs) -> Risk:
        """Per-call risk. Lets a skill gate only its dangerous arguments —
        e.g. power_control asks before 'shutdown' but not before 'lock'."""
        return self.risk

    def consequence(self, **kwargs) -> str:
        """Plain-language description of what is about to happen, phrased as a
        yes/no question."""
        return f"Run {self.name} with {kwargs}?"

    def to_tool_definition(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }
