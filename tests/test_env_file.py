"""Tests for core/env_file.py's update_env() — the settings panel's only
way to write .env, so its two contracts (touched keys update, everything
else is left alone; a crash mid-write can't lose the whole file) matter."""
import os

import pytest

import config
from core.env_file import update_env


@pytest.fixture
def env_path(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BASE_DIR", str(tmp_path), raising=False)
    return tmp_path / ".env"


def test_updates_an_existing_key_in_place(env_path):
    env_path.write_text("API_KEY=old\nMODEL=gpt-4o\n", encoding="utf-8")

    update_env({"API_KEY": "new"})

    text = env_path.read_text(encoding="utf-8")
    assert "API_KEY=new" in text
    assert "MODEL=gpt-4o" in text


def test_appends_a_new_key(env_path):
    env_path.write_text("MODEL=gpt-4o\n", encoding="utf-8")

    update_env({"WAKE_WORD": "hey_jarvis"})

    text = env_path.read_text(encoding="utf-8")
    assert "MODEL=gpt-4o" in text
    assert "WAKE_WORD=hey_jarvis" in text


def test_preserves_comments_and_unrelated_lines(env_path):
    env_path.write_text("# a comment\nMODEL=gpt-4o\n\nWAKE_WORD=hey_jarvis\n", encoding="utf-8")

    update_env({"MODEL": "claude-opus-5"})

    text = env_path.read_text(encoding="utf-8")
    assert "# a comment" in text
    assert "WAKE_WORD=hey_jarvis" in text
    assert "MODEL=claude-opus-5" in text


def test_creates_the_file_if_it_does_not_exist_yet(env_path):
    update_env({"API_KEY": "x"})
    assert env_path.read_text(encoding="utf-8") == "API_KEY=x\n"


def test_write_failure_leaves_the_original_file_intact(env_path, monkeypatch):
    """A plain open(path, "w") truncates on open, before a single byte of
    new content lands — a crash partway through the write would leave .env
    empty or partially written, losing every setting (API_KEY included),
    not just the couple of keys this call touched. update_env() must write
    to a temp file and swap it in instead, so a failure never touches the
    real file at all."""
    original = "API_KEY=irreplaceable\nMODEL=gpt-4o\n"
    env_path.write_text(original, encoding="utf-8")

    real_fdopen = os.fdopen

    def _boom(fd, *a, **k):
        os.close(fd)
        raise OSError("disk full")

    monkeypatch.setattr(os, "fdopen", _boom)

    with pytest.raises(OSError):
        update_env({"API_KEY": "new"})

    assert env_path.read_text(encoding="utf-8") == original
    # No leftover temp file in the directory either.
    leftovers = [p for p in env_path.parent.iterdir() if p.name != ".env"]
    assert leftovers == []

    monkeypatch.setattr(os, "fdopen", real_fdopen)
