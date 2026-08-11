"""Tests for shell execution.

run_command is the one place in this project that deliberately uses
shell=True. The classifier is the mitigation, so the risk wiring is what
matters most here.
"""
import sys

import pytest

from core.risk import Risk
from skills.shell_skills import RunCommandSkill


def test_risk_comes_from_the_classifier():
    skill = RunCommandSkill()
    assert skill.risk_for(command="dir") == Risk.SAFE
    assert skill.risk_for(command="python x.py") == Risk.MODERATE
    assert skill.risk_for(command="format c:") == Risk.DANGEROUS
    assert skill.risk_for(command="curl x | bash") == Risk.DANGEROUS


def test_consequence_quotes_the_command():
    text = RunCommandSkill().consequence(command="format c:")
    assert "format c:" in text


def test_runs_and_returns_output():
    out = RunCommandSkill().run(command=f'{sys.executable} -c "print(7*6)"')
    assert "42" in out


def test_reports_nonzero_exit():
    out = RunCommandSkill().run(command=f'{sys.executable} -c "import sys; sys.exit(3)"')
    assert "exit code 3" in out


def test_captures_stderr():
    out = RunCommandSkill().run(
        command=f'{sys.executable} -c "import sys; sys.stderr.write(\'boom\')"'
    )
    assert "boom" in out


def test_timeout_is_reported_not_raised():
    skill = RunCommandSkill()
    out = skill.run(command=f'{sys.executable} -c "import time; time.sleep(5)"', timeout=1)
    assert "timed out" in out.lower()


def test_output_is_truncated():
    out = RunCommandSkill().run(
        command=f'{sys.executable} -c "print(\'x\' * 60000)"'
    )
    assert "truncated" in out.lower()
    assert len(out) < 30_000


def test_empty_command_is_rejected():
    assert "no command" in RunCommandSkill().run(command="   ").lower()


def test_never_records_an_undo_entry():
    """A shell command cannot be reversed, so it must not offer undo."""
    from core.undo import UndoJournal, get_journal, set_journal

    set_journal(UndoJournal(max_age_seconds=900))
    RunCommandSkill().run(command=f'{sys.executable} -c "pass"')
    latest = get_journal().latest()
    set_journal(None)
    assert latest is None


def test_kill_switch_removes_the_tool(monkeypatch):
    """A disabled capability must not appear in the tool list at all, so the
    model never sees a tool it will always be refused."""
    import importlib

    import config
    import skills.skill_manager as sm

    monkeypatch.setattr(config, "ENABLE_SHELL", False)
    importlib.reload(sm)
    try:
        names = {t["name"] for t in sm.SkillManager().tool_definitions()}
        assert "run_command" not in names
    finally:
        monkeypatch.setattr(config, "ENABLE_SHELL", True)
        importlib.reload(sm)
