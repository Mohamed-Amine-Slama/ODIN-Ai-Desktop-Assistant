"""Minimal .env writer, used only by the settings panel.

Updates or appends KEY=value lines in place and leaves everything else in the
file — comments, ordering, unrelated keys (including API_KEY) — untouched.
Never reads a value back out loud or logs the file's contents; it only ever
writes the couple of keys a toggle just changed.
"""
import os

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

    with open(path, "w", encoding="utf-8") as f:
        f.writelines(lines)
