"""Filesystem access.

Reading is SAFE — it changes nothing and Jarvis is far more useful when it can
look things up without asking. Everything that mutates lives in the second half
of this file and carries an undo entry.
"""
import fnmatch
import os
import shutil
from pathlib import Path

from core.risk import Risk, is_sensitive_path
from core.undo import get_journal, move_to_trash

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
