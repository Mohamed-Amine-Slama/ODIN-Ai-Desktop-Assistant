"""Arbitrary shell execution.

This is the one skill in the project that intentionally uses shell=True: its
whole purpose is to accept a shell command string, so an argv list does not
apply. core.risk.classify_command is the mitigation, and it is pattern-based
and therefore incomplete — see the spec's "Risks accepted" section.

A shell command can never be undone, so this skill never records an undo entry.
"""
import subprocess

from core.risk import Risk, classify_command
from core.security import guard

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
        output = guard(output, source="run_command")

        if completed.returncode != 0:
            return f"Command finished with exit code {completed.returncode}.\n{output}"
        return output
