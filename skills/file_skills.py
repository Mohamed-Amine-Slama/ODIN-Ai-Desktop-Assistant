"""Filesystem access.

Reading is SAFE — it changes nothing and Jarvis is far more useful when it can
look things up without asking. Everything that mutates lives in the second half
of this file and carries an undo entry.
"""
import fnmatch
import os
import shutil
from pathlib import Path

import config
from core.risk import Risk, is_sensitive_path
from core.security import guard
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


def _blank(path: str) -> bool:
    return not path or not path.strip()


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
        if _blank(path):
            return "I need a path to work with."
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
        return guard(text, source=f"read_file:{path}")


def _parse_pdf_pages(spec: str, total_pages: int) -> list[int]:
    """Parse a 1-indexed page spec like '1-5' or '1,3,5' into a sorted list of
    unique 0-indexed page numbers. Raises ValueError with a user-facing
    message on anything unparsable, rather than the raw exception a bad
    int(...) would produce."""
    result: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start_str, _, end_str = part.partition("-")
            try:
                start, end = int(start_str.strip()), int(end_str.strip())
            except ValueError:
                raise ValueError(f"'{part}' isn't a valid page range.")
            for n in range(max(1, start), min(total_pages, end) + 1):
                result.add(n - 1)
        else:
            try:
                n = int(part)
            except ValueError:
                raise ValueError(f"'{part}' isn't a valid page number.")
            if 1 <= n <= total_pages:
                result.add(n - 1)
    return sorted(result)


class ReadPdfSkill(BaseSkill):
    name = "read_pdf"
    description = (
        "Extract text from a PDF file on the user's PC. Use for 'what does "
        "this PDF say', 'summarise this report', 'read this document' — "
        "read_file only handles plain text and can't decode a PDF's binary "
        "format."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute path to the PDF file."},
            "pages": {
                "type": "string",
                "description": (
                    "Optional 1-indexed page range, e.g. '1-5' or '1,3,5'. "
                    "Omit to extract every page."
                ),
            },
        },
        "required": ["path"],
    }
    risk = Risk.SAFE

    def run(self, path: str, pages: str = "") -> str:
        if _blank(path):
            return "I need a path to work with."
        target = Path(path).expanduser()
        if not target.exists():
            return f"There is no file at {path}."
        if target.suffix.lower() != ".pdf":
            return f"{path} isn't a .pdf file — use read_file for plain text."

        try:
            import pdfplumber
        except ImportError:
            return "Reading PDFs needs the 'pdfplumber' package. Run: pip install pdfplumber"

        try:
            with pdfplumber.open(str(target)) as pdf:
                total = len(pdf.pages)
                try:
                    indices = _parse_pdf_pages(pages, total) if pages.strip() else range(total)
                except ValueError as e:
                    return str(e)
                parts = [pdf.pages[i].extract_text() or "" for i in indices if 0 <= i < total]
        except Exception as e:
            return f"I couldn't read {path}: {e}"

        text = "\n\n".join(p for p in parts if p.strip())
        if not text:
            return f"{path} has no extractable text ({total} page(s)) — it may be scanned images."
        if len(text) > READ_LIMIT:
            text = text[:READ_LIMIT] + f"\n\n[truncated — {total} page(s) total]"
        return guard(text, source=f"read_pdf:{path}")


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
        if _blank(path):
            return "I need a path to work with."
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
        if _blank(root):
            return "I need a directory to search under."
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
        if _blank(path):
            return "I need a path to work with."
        target = Path(path).expanduser()
        existed = target.exists()

        backup = None
        if existed:
            try:
                backup = move_to_trash(target)
            except OSError as e:
                return f"I couldn't back up {path} before writing, so I stopped: {e}"

            # Recorded now, before the write is even attempted: write_text()
            # opens in "w" mode, which truncates the file immediately, before
            # a single byte of new content lands. If the write then fails
            # partway (disk full, I/O error, AV lock), the backup taken above
            # is the only copy of the original left — recording its restore
            # only after a successful write would leave that backup orphaned
            # and undiscoverable, with the original already destroyed.
            def restore(dest=target, source=backup):
                shutil.copy2(source, dest)
                return f"Restored the previous {dest.name}."

            get_journal().record(f"Restore the previous {target.name}", restore)

        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        except PermissionError:
            if existed:
                return (
                    f"I don't have permission to write {path}. The previous "
                    "version is safely backed up and still recoverable."
                )
            return f"I don't have permission to write {path}."
        except OSError as e:
            if existed:
                return (
                    f"I couldn't write {path}: {e}. The previous version is "
                    "safely backed up and still recoverable."
                )
            return f"I couldn't write {path}: {e}"

        if existed:
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
        if _blank(path):
            return "I need a path to work with."
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
        if is_sensitive_path(destination):
            return Risk.DANGEROUS
        # An existing directory is the normal case — shutil.move nests the
        # file inside it, nothing is replaced. Only an existing *file* at the
        # destination is a genuine overwrite.
        if destination.exists() and not destination.is_dir():
            return Risk.DANGEROUS
        return Risk.MODERATE

    def consequence(self, src: str = "", dst: str = "", **_) -> str:
        destination = Path(dst).expanduser()
        if is_sensitive_path(destination):
            return f"{dst} is a protected system location. Move {src} there anyway?"
        if destination.exists() and not destination.is_dir():
            return f"Move {src} onto {dst}, replacing what's there?"
        return f"Move {src} to {dst}?"

    def run(self, src: str, dst: str) -> str:
        if _blank(src) or _blank(dst):
            return "I need both a source and a destination path."
        source = Path(src).expanduser()
        destination = Path(dst).expanduser()

        if not source.exists():
            return f"There is no file at {src}."

        # Where the item actually ends up: nested inside an existing directory,
        # or exactly at `destination` otherwise (rename, or overwrite).
        into_dir = destination.is_dir()
        final_path = destination / source.name if into_dir else destination

        replaced = None
        if not into_dir and destination.exists():
            try:
                replaced = move_to_trash(destination)
            except OSError as e:
                return f"I couldn't back up {dst} before replacing it, so I stopped: {e}"

        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(destination))
        except (OSError, shutil.Error) as e:
            return f"I couldn't move {src}: {e}"

        def undo(a=source, b=final_path, old=replaced):
            shutil.move(str(b), str(a))
            if old is not None:
                if old.is_dir():
                    shutil.copytree(old, b)
                else:
                    shutil.copy2(old, b)
            return f"Moved {a.name} back."

        get_journal().record(f"Move {final_path.name} back to {source}", undo)
        return f"Moved {src} to {dst}."


class DeleteFileSkill(BaseSkill):
    name = "delete_file"
    description = (
        f"Delete a file or folder on the user's PC. It is copied to "
        f"{config.ASSISTANT_NAME}'s trash first, so the deletion can be undone."
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
        if _blank(path):
            # Path("").expanduser() silently resolves to the process's own
            # working directory rather than erroring — without this check,
            # a blank path here would try to trash-then-delete Jarvis's own
            # cwd instead of failing loudly.
            return "I need a path to work with."
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
