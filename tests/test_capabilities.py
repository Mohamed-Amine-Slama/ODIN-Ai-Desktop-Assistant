"""Tests for server-side web tools, screen vision, and clipboard."""
import base64
from types import SimpleNamespace

from conftest import Block, response, text_block, tool_use_block
from skills.skill_manager import SERVER_TOOLS, SkillManager
from skills.utility_skills import ClipboardSkill
from skills.vision_skills import ScreenshotSkill
from skills.web_skills import OpenWebsiteSkill


# -- server tools ----------------------------------------------------------

def test_server_tools_are_advertised():
    names = {t["name"] for t in SkillManager().tool_definitions()}
    assert {"web_search", "web_fetch"} <= names


def test_server_tools_are_not_local_skills():
    """SkillManager must never try to execute a server-side tool."""
    manager = SkillManager()
    for tool in SERVER_TOOLS:
        assert manager.is_local(tool["name"]) is False
        assert manager.get(tool["name"]) is None


def test_no_code_execution_tool_declared():
    """The _20260209 web tools run code execution internally. Declaring a
    second execution environment alongside them confuses the model."""
    types = {t.get("type") for t in SkillManager().tool_definitions()}
    assert not any(t and t.startswith("code_execution") for t in types)


def test_server_tool_results_are_not_executed(make_brain):
    """A server_tool_use block must pass through untouched — only client-side
    tool_use blocks get executed and answered with a tool_result."""
    brain = make_brain(
        [
            response(
                [
                    Block(type="server_tool_use", name="web_search", input={"query": "x"}, id="s1"),
                    Block(type="web_search_tool_result", tool_use_id="s1", content=[]),
                    text_block("Here's what I found."),
                ]
            )
        ]
    )

    reply = brain.ask("what's the news")

    assert reply == "Here's what I found."
    # No tool_result user message should have been synthesised.
    assert [m["role"] for m in brain.history] == ["user", "assistant"]


# -- vision ----------------------------------------------------------------

def test_screenshot_returns_image_blocks(monkeypatch):
    monkeypatch.setattr("skills.vision_skills._grab", lambda monitor: b"\x89PNG-fake")

    result = ScreenshotSkill().run()

    assert isinstance(result, list)
    image = result[0]
    assert image["type"] == "image"
    assert image["source"]["media_type"] == "image/png"
    assert base64.standard_b64decode(image["source"]["data"]) == b"\x89PNG-fake"


def test_screenshot_missing_deps_is_a_message_not_a_crash(monkeypatch):
    def boom(monitor):
        raise ImportError("no mss")

    monkeypatch.setattr("skills.vision_skills._grab", boom)
    out = ScreenshotSkill().run()

    assert isinstance(out, str)
    assert "pip install mss pillow" in out


def test_screenshot_bad_monitor(monkeypatch):
    def boom(monitor):
        raise IndexError(monitor)

    monkeypatch.setattr("skills.vision_skills._grab", boom)
    assert "no monitor 9" in ScreenshotSkill().run(monitor=9)


def test_image_result_flows_back_as_tool_result(make_brain, monkeypatch):
    """The end-to-end path: skill returns image blocks, brain puts them in a
    tool_result the model can actually see."""
    monkeypatch.setattr("skills.vision_skills._grab", lambda monitor: b"PNGDATA")

    brain = make_brain(
        [
            response([tool_use_block("see_screen", {})], stop_reason="tool_use"),
            response([text_block("You have a terminal open.")]),
        ]
    )

    brain.ask("what's on my screen")

    result = brain.history[2]["content"][0]
    assert result["type"] == "tool_result"
    assert result["content"][0]["type"] == "image"
    assert "is_error" not in result


# -- clipboard -------------------------------------------------------------

def _fake_pyperclip(monkeypatch, value="", fail=False):
    store = {"value": value}
    module = SimpleNamespace(
        paste=lambda: (_ for _ in ()).throw(RuntimeError("x")) if fail else store["value"],
        copy=lambda t: store.__setitem__("value", t),
    )
    monkeypatch.setitem(__import__("sys").modules, "pyperclip", module)
    return store


def test_clipboard_read(monkeypatch):
    _fake_pyperclip(monkeypatch, "hello world")
    assert "hello world" in ClipboardSkill().run(action="read")


def test_clipboard_read_empty(monkeypatch):
    _fake_pyperclip(monkeypatch, "")
    assert "empty" in ClipboardSkill().run(action="read")


def test_clipboard_write(monkeypatch):
    store = _fake_pyperclip(monkeypatch)
    assert "Copied" in ClipboardSkill().run(action="write", text="abc")
    assert store["value"] == "abc"


def test_clipboard_truncates_huge_content(monkeypatch):
    _fake_pyperclip(monkeypatch, "x" * 50000)
    out = ClipboardSkill().run(action="read")
    assert "truncated" in out
    assert len(out) < 25000


# -- browser safety --------------------------------------------------------

def test_open_website_rejects_dangerous_schemes(monkeypatch):
    opened = []
    monkeypatch.setattr("skills.web_skills.webbrowser.open", opened.append)

    for bad in [
        "file:///etc/passwd",
        "javascript:alert(1)",
        "data:text/html,<script>alert(1)</script>",
        "vbscript:msgbox(1)",
        "  file:///C:/Windows/System32/config/SAM  ",
    ]:
        assert "isn't a web address" in OpenWebsiteSkill().run(site=bad), bad
    assert opened == []


def test_open_website_allows_host_with_port(monkeypatch):
    """'localhost:8080' looks like a scheme to urlparse but isn't one."""
    opened = []
    monkeypatch.setattr("skills.web_skills.webbrowser.open", opened.append)

    OpenWebsiteSkill().run(site="localhost:8080")
    assert opened == ["https://localhost:8080"]


def test_open_website_known_alias(monkeypatch):
    opened = []
    monkeypatch.setattr("skills.web_skills.webbrowser.open", opened.append)

    OpenWebsiteSkill().run(site="youtube")
    assert opened == ["https://youtube.com"]


def test_open_website_adds_scheme(monkeypatch):
    opened = []
    monkeypatch.setattr("skills.web_skills.webbrowser.open", opened.append)

    OpenWebsiteSkill().run(site="news.ycombinator.com")
    assert opened == ["https://news.ycombinator.com"]
