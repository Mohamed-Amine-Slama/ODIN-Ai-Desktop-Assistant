"""Minimal .env writer, used only by the settings panel.

Updates or appends KEY=value lines in place and leaves everything else in the
file — comments, ordering, unrelated keys (including API_KEY) — untouched.
Never reads a value back out loud or logs the file's contents; it only ever
writes the couple of keys a toggle just changed.
"""
import os
import tempfile

import config


def update_env(values: dict[str, str]) -> None:
    path = os.path.join(config.BASE_DIR, ".env")
    lines = []
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()

    remaining = dict(values)
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key = stripped.split("=", 1)[0].strip()
        if key in remaining:
            lines[i] = f"{key}={remaining.pop(key)}\n"

    for key, value in remaining.items():
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        lines.append(f"{key}={value}\n")

    # Written to a temp file in the same directory, then swapped into place
    # with os.replace() — a plain open(path, "w") truncates immediately, so
    # a crash between the truncate and the write completing would otherwise
    # leave .env empty or partially written, losing every setting (API_KEY
    # included), not just the couple of keys this call touched.
    directory = os.path.dirname(path) or "."
    fd, tmp_path = tempfile.mkstemp(prefix=".env.", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.writelines(lines)
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
