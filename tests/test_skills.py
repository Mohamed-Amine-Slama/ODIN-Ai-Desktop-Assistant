"""Tests for the skill registry, the confirmation gate, and the safe evaluator."""
import pytest

from conftest import response, text_block, tool_use_block
from core.risk import Risk
from skills.base_skill import SkillOutcome
from skills.skill_manager import SKILL_CLASSES, SkillManager
from skills.system_skills import CloseAppSkill, PowerControlSkill
from skills.utility_skills import CalculatorSkill


# -- registry --------------------------------------------------------------

def test_each_skill_instantiated_exactly_once():
    """The original built two instances per class and discarded one."""
    created = []

    class Counter:
        def __init_subclass__(cls, **kw):
            super().__init_subclass__(**kw)

    for cls in SKILL_CLASSES:
        original = cls.__init__

        def counting_init(self, *a, _cls=cls, _orig=original, **kw):
            created.append(_cls.__name__)
            _orig(self, *a, **kw)

        cls.__init__ = counting_init

    try:
        SkillManager()
    finally:
        for cls in SKILL_CLASSES:
            del cls.__init__

    assert len(created) == len(SKILL_CLASSES)
    assert len(set(created)) == len(created)


def test_tool_definitions_are_stable_across_calls():
    """Byte-identical tool blocks are what keep the prompt cache warm."""
    manager = SkillManager()
    assert manager.tool_definitions() == manager.tool_definitions()


def test_tool_names_are_unique():
    manager = SkillManager()
    names = [t["name"] for t in manager.tool_definitions()]
    assert len(names) == len(set(names))


def test_execute_reports_errors():
    manager = SkillManager()
    outcome = manager.execute("nope", {})
    assert outcome.is_error is True
    assert "Unknown skill" in outcome.content


def test_execute_reports_bad_arguments():
    manager = SkillManager()
    outcome = manager.execute("calculate", {"wrong_arg": 1})
    assert outcome.is_error is True
    assert "Invalid arguments" in outcome.content


# -- risk & confirmation gate -----------------------------------------------

def test_power_control_risk_by_action():
    skill = PowerControlSkill()
    assert skill.risk_for(action="shutdown") == Risk.DANGEROUS
    assert skill.risk_for(action="restart") == Risk.DANGEROUS
    # Reversible actions shouldn't nag.
    assert skill.risk_for(action="lock") == Risk.MODERATE
    assert skill.risk_for(action="sleep") == Risk.MODERATE


def test_close_app_is_dangerous():
    assert CloseAppSkill().risk_for(app_name="chrome") == Risk.DANGEROUS


def test_default_skill_risk_is_safe():
    assert CalculatorSkill().risk_for(expression="1+1") == Risk.SAFE


def test_consequence_is_human_readable():
    text = PowerControlSkill().consequence(action="shutdown")
    assert "shutdown" in text.lower()


def test_execute_returns_a_skill_outcome():
    outcome = SkillManager().execute("calculate", {"expression": "2+2"})
    assert isinstance(outcome, SkillOutcome)
    assert "4" in outcome.content
    assert outcome.is_error is False
    assert outcome.undo_token is None


def test_execute_reports_errors_on_the_outcome():
    outcome = SkillManager().execute("nope", {})
    assert outcome.is_error is True
    assert "Unknown skill" in outcome.content


def test_declining_shutdown_does_not_execute(make_brain, monkeypatch):
    """The whole point of the gate: a declined call must never reach the OS."""
    ran = []
    monkeypatch.setattr(
        "skills.system_skills.subprocess.run",
        lambda *a, **kw: ran.append(a),
    )

    brain = make_brain(
        [
            response(
                [tool_use_block("power_control", {"action": "shutdown"})],
                stop_reason="tool_use",
            ),
            response([text_block("Cancelled.")]),
        ],
        confirm=lambda skill, tool_input: False,
    )

    brain.ask("shut down the pc")

    assert ran == [], "shutdown ran despite being declined"
    result = brain.history[2]["content"][0]
    assert "declined" in result["content"]


def test_approving_shutdown_executes(make_brain, monkeypatch):
    ran = []
    monkeypatch.setattr("skills.system_skills.IS_WINDOWS", True)
    monkeypatch.setattr(
        "skills.system_skills.subprocess.run",
        lambda *a, **kw: ran.append(a[0]),
    )

    brain = make_brain(
        [
            response(
                [tool_use_block("power_control", {"action": "shutdown"})],
                stop_reason="tool_use",
            ),
            response([text_block("Shutting down.")]),
        ],
        confirm=lambda skill, tool_input: True,
    )

    brain.ask("shut down the pc")

    assert ran == [["shutdown", "/s", "/t", "5"]]


# -- close_app matching ----------------------------------------------------

def test_close_app_refuses_protected_processes():
    assert "won't terminate" in CloseAppSkill().run(app_name="lsass")
    assert "won't terminate" in CloseAppSkill().run(app_name="csrss.exe")


def test_close_app_uses_exact_match(monkeypatch):
    """close_app('s') used to terminate every process with an 's' in its name."""
    killed = []

    class FakeProc:
        def __init__(self, name):
            self.info = {"name": name}

        def terminate(self):
            killed.append(self.info["name"])

    monkeypatch.setattr(
        "skills.system_skills.psutil.process_iter",
        lambda attrs=None: [
            FakeProc("chrome.exe"),
            FakeProc("explorer.exe"),
            FakeProc("spotify.exe"),
        ],
    )

    out = CloseAppSkill().run(app_name="s")
    assert killed == []
    assert "couldn't find" in out

    CloseAppSkill().run(app_name="chrome")
    assert killed == ["chrome.exe"]


# -- calculator ------------------------------------------------------------

@pytest.mark.parametrize(
    "expression,expected",
    [("2 + 2", "4"), ("12 * (3 + 4) / 2", "42.0"), ("-5 + 3", "-2")],
)
def test_calculator_evaluates(expression, expected):
    assert expected in CalculatorSkill().run(expression=expression)


def test_calculator_rejects_huge_exponents():
    """2 ** 999999999 would hang the assistant computing an unused number."""
    out = CalculatorSkill().run(expression="2 ** 999999999")
    assert "Exponent too large" in out


def test_calculator_rejects_code_execution():
    for hostile in ["__import__('os').system('echo hi')", "open('/etc/passwd').read()", "[]"]:
        out = CalculatorSkill().run(expression=hostile)
        assert "couldn't evaluate" in out


def test_calculator_handles_divide_by_zero():
    assert "divides by zero" in CalculatorSkill().run(expression="1 / 0")


"""The undo-honesty check.

Every skill either can reverse itself and says so, or cannot and stays silent.
A skill that gains or loses undo support without its description being updated
is exactly the drift this test exists to catch.
"""
REVERSIBLE_SKILLS = {
    "write_file",
    "make_dir",
    "move_file",
    "delete_file",
    "focus_window",
    "set_window_state",
}

NEVER_REVERSIBLE = {
    "run_command",
    "type_text",
    "press_keys",
    "click",
    "scroll",
    "close_window",
    "power_control",
    "close_app",
}


def test_reversible_and_irreversible_sets_are_disjoint():
    assert REVERSIBLE_SKILLS.isdisjoint(NEVER_REVERSIBLE)


def test_every_declared_skill_is_registered():
    registered = set(SkillManager().skills)
    for name in REVERSIBLE_SKILLS | NEVER_REVERSIBLE:
        assert name in registered, f"{name} is not registered"


def test_irreversible_skills_describe_themselves_honestly():
    """If a skill cannot be undone, its description must say so — the model
    relays that to the user before they agree to it."""
    manager = SkillManager()
    for name in NEVER_REVERSIBLE:
        description = manager.get(name).description.lower()
        assert "undo" in description or "unsaved" in description, name
