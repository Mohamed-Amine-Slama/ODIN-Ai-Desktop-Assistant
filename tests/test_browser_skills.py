"""Tests for the DOM-driven browser automation path (core/browser.py +
skills/browser_skills.py).

Nothing here launches a browser or imports Playwright. The two halves are
tested where they're worth testing: RefTable against plain fake data, and
BrowserController's threading/health/timeout behaviour against fake jobs that
raise on demand. The parts that genuinely need a real page — whether a wheel
event lands on Instagram's DM panel, whether a login survives a restart — are
in the README's manual smoke test, because a fake cannot answer them.
"""
import importlib
import threading
import time

import pytest

import config
import skills.skill_manager as sm
from core.browser import (
    BrowserController,
    RefTable,
    _accessible_name,
    _is_browser_gone,
    browser_automation_available,
    get_browser_controller,
    set_browser_controller,
)
from core.risk import Risk
from skills.browser_skills import (
    BrowserClickSkill,
    BrowserCloseSkill,
    BrowserNavigateSkill,
    BrowserReadSkill,
    BrowserScrollSkill,
    BrowserTypeSkill,
)


@pytest.fixture(autouse=True)
def _clean_controller():
    yield
    set_browser_controller(None)


class _FakeController:
    """Records what the skills asked for, exactly as tests/test_gesture_skills.py
    does for the camera."""

    def __init__(self):
        self.calls = []

    def navigate(self, url):
        self.calls.append(("navigate", url))
        return "opened"

    def read(self):
        self.calls.append(("read",))
        return "listing"

    def click(self, ref):
        self.calls.append(("click", ref))
        return "clicked"

    def type_text(self, ref, text, submit=False):
        self.calls.append(("type", ref, text, submit))
        return "typed"

    def scroll(self, amount, ref=""):
        self.calls.append(("scroll", amount, ref))
        return "scrolled"

    def close(self):
        self.calls.append(("close",))
        return "closed"


# -- risk tiers -------------------------------------------------------------

def test_reading_the_page_is_safe():
    """Navigating and reading change nothing, matching open_website/see_screen."""
    assert BrowserNavigateSkill().risk_for() == Risk.SAFE
    assert BrowserReadSkill().risk_for() == Risk.SAFE


def test_acting_on_the_page_is_moderate():
    """Same tier as click/type_text/scroll in skills/input_skills.py — these do
    the same thing to a web page that those do to the screen."""
    assert BrowserClickSkill().risk_for(ref="1:0") == Risk.MODERATE
    assert BrowserTypeSkill().risk_for(ref="1:0", text="hi") == Risk.MODERATE
    assert BrowserScrollSkill().risk_for(amount=400) == Risk.MODERATE


def test_closing_the_browser_is_never_gated():
    """Whatever CONFIRM_DESTRUCTIVE says, shutting this off must be immediate —
    the same rule hand_control's stop follows."""
    assert BrowserCloseSkill().risk_for() == Risk.SAFE


def test_irreversible_browser_skills_say_so():
    """Checked here rather than in test_skills.py's NEVER_REVERSIBLE set: that
    set is asserted against a default (flag-off) SkillManager, where these
    skills are deliberately absent."""
    for skill in (BrowserClickSkill(), BrowserTypeSkill(), BrowserScrollSkill()):
        assert "cannot be undone" in skill.description.lower(), skill.name


def test_acting_on_the_page_records_no_undo_token():
    """You cannot un-send a message. The skills must not write an undo entry,
    or the UI would offer an undo that does nothing."""
    from core.undo import get_journal

    fake = _FakeController()
    set_browser_controller(fake)
    journal = get_journal()
    before = journal.latest()

    BrowserClickSkill().run(ref="1:0")
    BrowserTypeSkill().run(ref="1:1", text="hello")
    BrowserScrollSkill().run(amount=300)

    assert journal.latest() is before


def test_consequences_name_what_is_about_to_happen():
    assert "1:4" in BrowserClickSkill().consequence(ref="1:4")
    typed = BrowserTypeSkill().consequence(ref="1:4", text="hello there", submit=True)
    assert "hello there" in typed and "Enter" in typed


# -- skills delegate to the singleton ---------------------------------------

def test_navigate_resolves_a_site_name_like_open_website_does():
    fake = _FakeController()
    set_browser_controller(fake)
    BrowserNavigateSkill().run(url="instagram")
    assert fake.calls == [("navigate", "https://instagram.com")]


def test_navigate_refuses_a_non_web_scheme():
    """page.goto() would happily follow a file:// URL and read the disk, so the
    same check open_website applies has to apply here."""
    fake = _FakeController()
    set_browser_controller(fake)
    out = BrowserNavigateSkill().run(url="file:///etc/passwd")
    assert "isn't a web address" in out
    assert fake.calls == []


def test_every_skill_drives_the_one_singleton():
    fake = _FakeController()
    set_browser_controller(fake)

    BrowserNavigateSkill().run(url="https://example.com")
    BrowserReadSkill().run()
    BrowserClickSkill().run(ref="1:2")
    BrowserTypeSkill().run(ref="1:3", text="hi", submit=True)
    BrowserScrollSkill().run(amount=-200, ref="1:4")
    BrowserCloseSkill().run()

    assert fake.calls == [
        ("navigate", "https://example.com"),
        ("read",),
        ("click", "1:2"),
        ("type", "1:3", "hi", True),
        ("scroll", -200, "1:4"),
        ("close",),
    ]


def test_submit_defaults_to_false():
    """A model that omits submit must not accidentally send a half-typed
    message."""
    fake = _FakeController()
    set_browser_controller(fake)
    BrowserTypeSkill().run(ref="1:0", text="draft")
    assert fake.calls == [("type", "1:0", "draft", False)]


# -- registration gate ------------------------------------------------------

def test_kill_switch_removes_the_tools(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_BROWSER_AUTOMATION", False)
    importlib.reload(sm)
    try:
        names = {t["name"] for t in sm.SkillManager().tool_definitions()}
        assert not any(n.startswith("browser_") for n in names)
    finally:
        importlib.reload(sm)


def test_the_flag_alone_is_not_enough(monkeypatch):
    """Flag on but playwright not installed: the tools must stay out. The
    prompt tells the model to prefer them, so registering them here would make
    every web request open with a call that cannot succeed."""
    monkeypatch.setattr(config, "ENABLE_BROWSER_AUTOMATION", True)
    monkeypatch.setattr(sm, "browser_automation_available", lambda: False, raising=False)
    import core.browser

    monkeypatch.setattr(core.browser, "_playwright", lambda: (None, "not installed"))
    importlib.reload(sm)
    try:
        names = {t["name"] for t in sm.SkillManager().tool_definitions()}
        assert "browser_navigate" not in names
    finally:
        monkeypatch.undo()
        importlib.reload(sm)


def test_flag_on_and_package_present_registers_all_six(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_BROWSER_AUTOMATION", True)
    import core.browser

    monkeypatch.setattr(core.browser, "_playwright", lambda: (object(), None))
    importlib.reload(sm)
    try:
        names = {t["name"] for t in sm.SkillManager().tool_definitions()}
        assert {
            "browser_navigate",
            "browser_read",
            "browser_click",
            "browser_type",
            "browser_scroll",
            "browser_close",
        } <= names
    finally:
        monkeypatch.undo()
        importlib.reload(sm)


def test_availability_is_false_without_the_package(monkeypatch):
    import core.browser

    monkeypatch.setattr(core.browser, "_playwright", lambda: (None, "nope"))
    assert browser_automation_available() is False


# -- RefTable (pure) --------------------------------------------------------

def test_refs_are_listed_with_role_and_name():
    table = RefTable()
    listing = table.build([("link", "Home", "a"), ("button", "Send", "b")])
    assert '[1:0] link "Home"' in listing
    assert '[1:1] button "Send"' in listing


def test_a_ref_resolves_back_to_its_element():
    table = RefTable()
    table.build([("link", "Home", "a"), ("button", "Send", "b")])
    assert table.resolve("1:1") == "b"


def test_a_stale_ref_never_resolves_against_a_newer_snapshot():
    """The whole reason refs carry a generation. Untagged, '1' from the old
    page would silently resolve to whatever now sits at index 1 — clicking the
    wrong thing while reporting success."""
    table = RefTable()
    table.build([("link", "Home", "old-0"), ("button", "Send", "old-1")])
    table.build([("link", "Profile", "new-0"), ("button", "Delete", "new-1")])

    assert table.resolve("1:1") is None
    assert table.resolve("2:1") == "new-1"


def test_the_listing_is_capped_and_says_what_it_left_out():
    table = RefTable()
    elements = [("link", f"Item {i}", i) for i in range(10)]
    listing = table.build(elements, max_elements=4)

    assert listing.count("[") == 4
    assert "6 more not shown" in listing
    assert "browser_scroll" in listing


def test_an_uncapped_listing_has_no_truncation_note():
    table = RefTable()
    listing = table.build([("link", "Only", 1)], max_elements=4)
    assert "more not shown" not in listing


def test_an_empty_page_says_so_rather_than_returning_nothing():
    assert "No interactive elements" in RefTable().build([])


def test_resolve_tolerates_the_whitespace_a_model_adds():
    table = RefTable()
    table.build([("link", "Home", "a")])
    assert table.resolve(" 1:0 ") == "a"


# -- accessible names -------------------------------------------------------

class _FakeLocator:
    def __init__(self, attrs=None, text=None):
        self.attrs = attrs or {}
        self.text = text

    def get_attribute(self, name):
        return self.attrs.get(name)

    def inner_text(self):
        if self.text is None:
            raise RuntimeError("no text node")
        return self.text


def test_aria_label_wins_over_visible_text():
    locator = _FakeLocator(attrs={"aria-label": "Send message"}, text="Send")
    assert _accessible_name(locator) == "Send message"


def test_the_name_falls_through_to_the_next_source():
    locator = _FakeLocator(attrs={"placeholder": "Search"}, text=None)
    assert _accessible_name(locator) == "Search"


def test_a_nameless_element_is_dropped_not_listed_blank():
    assert _accessible_name(_FakeLocator()) == ""


def test_a_long_name_is_trimmed():
    """DM rows carry the whole last message. The first words identify the row;
    the rest is padding the model pays for."""
    locator = _FakeLocator(attrs={"aria-label": "x" * 500})
    assert len(_accessible_name(locator)) == 80


def test_whitespace_in_a_name_is_collapsed():
    locator = _FakeLocator(text="  Send \n  message  ")
    assert _accessible_name(locator) == "Send message"


# -- controller: health, crashes, timeouts ----------------------------------

class _StubbedController(BrowserController):
    """A controller whose thread serves jobs without ever touching Playwright."""

    def _run(self):
        self._page = object()
        self._ready.set()
        self._serve()


def test_the_browser_being_gone_is_recognised():
    assert _is_browser_gone(RuntimeError("Target page, context or browser has been closed"))
    assert _is_browser_gone(RuntimeError("Connection closed"))


def test_an_ordinary_failure_is_not_mistaken_for_a_dead_browser():
    assert not _is_browser_gone(RuntimeError("Timeout 15000ms exceeded waiting for locator"))


def test_a_failed_action_is_reported_not_raised():
    controller = _StubbedController()
    try:
        out = controller.submit(lambda: (_ for _ in ()).throw(RuntimeError("no such element")))
        assert "no such element" in out
        assert controller.is_running()
    finally:
        controller.close()


def test_a_closed_browser_resets_so_the_next_call_relaunches():
    """A real window can be closed by the user, or by ODIN's own close_window
    matching 'chrome'. The next call must start fresh, not keep throwing
    against handles pointing at a dead process."""
    controller = _StubbedController()
    try:
        out = controller.submit(
            lambda: (_ for _ in ()).throw(RuntimeError("Target closed"))
        )
        assert "browser window was closed" in out

        deadline = time.time() + 2.0
        while controller.is_running() and time.time() < deadline:
            time.sleep(0.01)
        assert not controller.is_running()

        assert controller.submit(lambda: "back up") == "back up"
        assert controller.is_running()
    finally:
        controller.close()


def test_a_hung_job_times_out_instead_of_blocking_forever(monkeypatch):
    monkeypatch.setattr(config, "BROWSER_ACTION_TIMEOUT_SECONDS", 0.0)
    monkeypatch.setattr("core.browser.SUBMIT_GRACE_SECONDS", 0.2)

    controller = _StubbedController()
    release = threading.Event()
    try:
        started = time.monotonic()
        out = controller.submit(lambda: release.wait(30) or "eventually")
        assert "didn't respond in time" in out
        assert time.monotonic() - started < 5.0
    finally:
        release.set()
        controller.close()


def test_a_launch_failure_is_reported_rather_than_retried_forever():
    """No playwright installed is the expected first state. It must come back
    as the install instruction, not a traceback."""
    controller = BrowserController()
    import core.browser

    original = core.browser._playwright
    core.browser._playwright = lambda: (None, "Browser automation needs the 'playwright' package.")
    try:
        out = controller.navigate("https://example.com")
        assert "playwright" in out
    finally:
        core.browser._playwright = original
        controller.close()


def test_closing_an_idle_browser_is_a_no_op():
    assert BrowserController().close() == "The browser is already closed."


def test_closing_does_not_launch_a_browser_to_close_it():
    """close() must never go through the job queue: a wedged page cannot be
    allowed to stop the user turning this off."""
    controller = BrowserController()
    controller.close()
    assert not controller.is_running()


def test_an_unstarted_controller_is_not_healthy():
    assert BrowserController().is_healthy() is False


def test_the_singleton_is_shared():
    assert get_browser_controller() is get_browser_controller()


# -- scroll targets the panel, not the document ------------------------------

class _RecordingPage:
    def __init__(self):
        self.wheels = []
        self.moves = []
        self.viewport_size = {"width": 1000, "height": 800}

        page = self

        class _Mouse:
            def wheel(self, dx, dy):
                page.wheels.append((dx, dy))

            def move(self, x, y):
                page.moves.append((x, y))

        self.mouse = _Mouse()


class _HoverableElement:
    def __init__(self):
        self.hovered = False

    def hover(self):
        self.hovered = True


def test_scrolling_a_ref_hovers_it_first():
    """A chat app scrolls an inner overflow panel, not the document. A wheel
    event lands on whatever is under the cursor, so the hover is what makes
    this work at all — window.scrollBy would silently do nothing here."""
    controller = BrowserController()
    element = _HoverableElement()
    controller._refs.build([("list", "Conversations", element)])
    controller._page = _RecordingPage()

    out = controller._scroll_impl(500, "1:0")

    assert element.hovered
    assert controller._page.wheels == [(0, 500)]
    assert controller._page.moves == []
    assert "down" in out


def test_scrolling_without_a_ref_falls_back_to_the_viewport_centre():
    controller = BrowserController()
    controller._page = _RecordingPage()

    controller._scroll_impl(-300, "")

    assert controller._page.moves == [(500.0, 400.0)]
    assert controller._page.wheels == [(0, -300)]


def test_scrolling_a_stale_ref_asks_for_a_fresh_read():
    controller = BrowserController()
    controller._page = _RecordingPage()
    out = controller._scroll_impl(200, "9:9")
    assert "browser_read" in out
    assert controller._page.wheels == []


# -- click / type against fake elements --------------------------------------

class _RecordingElement:
    def __init__(self):
        self.events = []

    def click(self):
        self.events.append(("click",))

    def press_sequentially(self, text):
        self.events.append(("type", text))

    def press(self, key):
        self.events.append(("press", key))


def test_typing_sends_real_keystrokes_not_a_bulk_fill():
    """A live-filtering search box runs its own JS per keystroke. fill() sets
    the value in one go and the filter never fires — which is exactly the
    screen this feature exists for."""
    controller = BrowserController()
    element = _RecordingElement()
    controller._refs.build([("searchbox", "Search", element)])

    controller._type_impl("1:0", "alice", submit=False)

    assert element.events == [("click",), ("type", "alice")]


def test_submitting_presses_enter_after_typing():
    controller = BrowserController()
    element = _RecordingElement()
    controller._refs.build([("textbox", "Message", element)])

    controller._type_impl("1:0", "hey", submit=True)

    assert element.events == [("click",), ("type", "hey"), ("press", "Enter")]


def test_clicking_a_stale_ref_asks_for_a_fresh_read():
    controller = BrowserController()
    out = controller._click_impl("4:2")
    assert "browser_read" in out


def test_a_click_result_tells_the_model_to_verify():
    """A click returning no error doesn't mean it worked — the same discipline
    the see_screen guidance already demands."""
    controller = BrowserController()
    controller._refs.build([("button", "Send", _RecordingElement())])
    assert "browser_read" in controller._click_impl("1:0")


# -- reading the page --------------------------------------------------------

class _ReadableLocator:
    def __init__(self, name, visible=True):
        self.name = name
        self.visible = visible

    def is_visible(self):
        return self.visible

    def get_attribute(self, attr):
        return self.name if attr == "aria-label" else None

    def inner_text(self):
        return ""


class _ReadablePage:
    def __init__(self, by_role, title="Instagram", url="https://instagram.com/"):
        self.by_role = by_role
        self._title = title
        self.url = url

    def title(self):
        return self._title

    def get_by_role(self, role):
        locators = self.by_role.get(role, [])

        class _Query:
            def all(self):
                return locators

        return _Query()


def test_reading_lists_visible_named_elements_with_a_header():
    controller = BrowserController()
    controller._page = _ReadablePage({
        "link": [_ReadableLocator("Home")],
        "button": [_ReadableLocator("Send")],
    })

    listing = controller._read_impl()

    assert listing.startswith("Instagram — https://instagram.com/")
    assert '[1:0] link "Home"' in listing
    assert '[1:1] button "Send"' in listing


def test_reading_skips_hidden_and_nameless_elements():
    """A hidden element cannot be clicked, and a nameless one gives the model
    nothing to choose it by — both are noise the turn pays for."""
    controller = BrowserController()
    controller._page = _ReadablePage({
        "link": [
            _ReadableLocator("Visible"),
            _ReadableLocator("Hidden", visible=False),
            _ReadableLocator(""),
        ]
    })

    listing = controller._read_impl()

    assert "Visible" in listing
    assert "Hidden" not in listing
    assert listing.count("[") == 1


def test_reading_stops_walking_a_huge_page(monkeypatch):
    """Every visibility check is a round trip into the browser, and the listing
    is capped anyway — walking a 5000-element feed to the end only buys an
    exact 'N more' count nobody acts on."""
    monkeypatch.setattr(config, "BROWSER_MAX_ELEMENTS", 5)
    controller = BrowserController()
    controller._page = _ReadablePage({
        "link": [_ReadableLocator(f"Item {i}") for i in range(5000)]
    })

    listing = controller._read_impl()

    assert listing.count("[") == 5
    assert "more not shown" in listing


def test_a_page_with_no_title_still_reports_its_url():
    controller = BrowserController()
    controller._page = _ReadablePage({}, title="", url="https://example.com/x")
    assert controller._read_impl().startswith("https://example.com/x")
