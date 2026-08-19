"""DOM-driven browser automation — the fast path for anything inside a web page.

ODIN's other way into a website is the vision loop: screenshot the screen, send
the image to the model, get pixel coordinates back, click them, screenshot
again. That works anywhere, but a task like "open Instagram, find someone in my
DMs, message them" needs 10-20 sequential round trips and each one carries a
full screenshot. This module replaces the image with a compact text list of the
page's interactive elements, and the guessed pixel coordinates with a
reference the model can name — same task, a fraction of the tokens, and no
mis-clicks from a stale screenshot.

It does not replace the vision path. Native apps still need pixels, and so
does anything a page draws into a canvas or a video, which the accessibility
tree cannot describe.

Two layers, kept apart so the interesting logic is testable without Playwright
or a browser anywhere nearby:

- `RefTable` is pure. It formats a list of (role, name, element) triples into
  the numbered listing the model reads, and resolves a reference back to an
  element. No Playwright, no I/O.
- `BrowserController` is the impure shell: it owns the Playwright driver, the
  browser process, and the page, on a background thread — the same
  start/stop/singleton shape as core/gesture.py's GestureController.

Why a dedicated thread rather than calling Playwright directly: Playwright's
sync API is greenlet-based internally and must be driven from the one OS
thread that created it for its whole life. A second thread touching it raises.
Callers therefore hand work to that thread through a queue and wait for the
answer, which also serialises browser access for free — two skills can never
interleave half-finished actions on the same page.

The browser is headed (visible) and keeps its own profile under
data/browser_profile/, so the user logs in once, by hand, past whatever 2FA or
CAPTCHA a site demands, and that session survives ODIN restarts. Nothing here
handles credentials.
"""
import os
import queue
import threading

import config

# Roles worth listing. Each one is a separate query into the page, so this is
# deliberately the set a model can actually act on rather than every role the
# ARIA spec defines.
INTERACTIVE_ROLES = (
    "link",
    "button",
    "textbox",
    "searchbox",
    "combobox",
    "checkbox",
    "radio",
    "menuitem",
    "tab",
    "option",
    "switch",
)

# An accessible name is a label, not a document. Instagram's DM rows in
# particular carry the whole last message plus a timestamp; the first few words
# are what identifies the row.
NAME_MAX_CHARS = 80

# Stop walking the page once this many visible elements are in hand. A feed can
# hold thousands, and every one costs a round trip into the browser to check
# visibility — the listing is capped at BROWSER_MAX_ELEMENTS anyway, so
# gathering far past it only buys an accurate "N more" count nobody reads.
SCAN_CAP_MULTIPLE = 3

# How much longer submit() waits than the Playwright-level timeout it wraps.
# The ordering matters: Playwright must give up first so the worker thread
# unwinds on its own, leaving submit()'s timeout as a backstop rather than the
# thing that routinely fires.
SUBMIT_GRACE_SECONDS = 5.0

# Phrases Playwright uses when the page/context/browser is gone. Matched as
# text rather than by exception class so this stays testable without
# Playwright installed, and so it survives Playwright renaming its error
# classes between versions.
_BROWSER_GONE_MARKERS = (
    "target closed",
    "target page, context or browser has been closed",
    "browser has been closed",
    "context has been closed",
    "page has been closed",
    "browser closed",
    "page closed",
    "connection closed",
)


def _playwright():
    """Import Playwright lazily. Returns (module, error_message).

    Importing at module scope would make the whole skill registry depend on an
    optional package — the same reasoning core/gesture.py applies to cv2 and
    mediapipe.
    """
    try:
        from playwright import sync_api
    except ImportError:
        return None, (
            "Browser automation needs the 'playwright' package. Run: "
            "pip install playwright && playwright install chrome"
        )
    except Exception as e:
        return None, f"Browser automation is unavailable: {e}"
    return sync_api, None


def browser_automation_available() -> bool:
    """Whether the playwright package can be imported at all.

    Deliberately cheap: no browser binary probe, because this is called once
    per SkillManager() construction and probing would spawn Playwright's
    driver process every time. A missing *browser* degrades at call time with
    a message that says how to install it; a missing *package* keeps the tools
    out of the prompt entirely, so the model is never told to prefer a tool
    whose every call is guaranteed to fail.
    """
    module, _ = _playwright()
    return module is not None


def _is_browser_gone(error: BaseException) -> bool:
    """Whether this exception means the browser died rather than the action
    failing. The window is a real window — the user can close it, and so can
    ODIN's own close_window skill matching on "chrome"."""
    message = str(error).lower()
    return any(marker in message for marker in _BROWSER_GONE_MARKERS)


def _accessible_name(locator) -> str:
    """Best available human-readable label for an element.

    The order matters: aria-label is what a site author wrote *for* assistive
    tech and is the most reliable, while inner_text picks up the visible label
    that a sighted user (and the model's own idea of the page) would name.
    """
    getters = (
        lambda: locator.get_attribute("aria-label"),
        lambda: locator.inner_text(),
        lambda: locator.get_attribute("alt"),
        lambda: locator.get_attribute("title"),
        lambda: locator.get_attribute("placeholder"),
    )
    for getter in getters:
        try:
            value = getter()
        except Exception:
            continue
        if value:
            name = " ".join(value.split())
            if name:
                return name[:NAME_MAX_CHARS]
    return ""


class RefTable:
    """The numbered element listing the model reads, and the lookup back.

    References are generation-tagged ("3:12", not "12"). Without the
    generation, a reference the model remembered from an older snapshot would
    silently resolve against whatever happens to sit at index 12 in the
    current one — clicking the wrong thing while reporting success. Tagged,
    a stale reference misses cleanly and the model is told to re-read.
    """

    def __init__(self):
        self._generation = 0
        self._elements: dict[str, object] = {}

    def reset(self) -> None:
        self._generation = 0
        self._elements = {}

    @property
    def generation(self) -> int:
        return self._generation

    def build(self, elements, max_elements: "int | None" = None) -> str:
        """Replace the table with a fresh snapshot and format it.

        `elements` is a sequence of (role, name, element) triples; `element` is
        opaque here and only handed back by resolve().
        """
        if max_elements is None:
            max_elements = getattr(config, "BROWSER_MAX_ELEMENTS", 60)

        self._generation += 1
        self._elements = {}

        shown = list(elements)[: max(1, max_elements)]
        lines = []
        for index, (role, name, element) in enumerate(shown):
            ref = f"{self._generation}:{index}"
            self._elements[ref] = element
            lines.append(f'[{ref}] {role} "{name}"')

        if not lines:
            return "No interactive elements are visible on this page."

        hidden = len(elements) - len(shown)
        if hidden > 0:
            lines.append(
                f"... and {hidden} more not shown. Use browser_scroll, then "
                "browser_read again, to reach the rest."
            )
        return "\n".join(lines)

    def resolve(self, ref: str):
        """The element for this reference, or None if it's stale/unknown."""
        return self._elements.get(str(ref).strip())


class _Job:
    """One unit of work for the browser thread, plus somewhere to put the
    answer. Deliberately not a dataclass: threading.Event has no sensible
    default-factory story and this stays clearer written out."""

    __slots__ = ("func", "done", "result", "error")

    def __init__(self, func):
        self.func = func
        self.done = threading.Event()
        self.result: "str | None" = None
        self.error: "str | None" = None


class BrowserController:
    """A live browser, driven by role/name rather than pixels.

    Every public method returns a plain string meant to go straight back to
    the model — including failures, which are reported rather than raised, so
    one bad selector never takes down a turn.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._jobs: "queue.Queue[_Job]" = queue.Queue()
        self._thread: "threading.Thread | None" = None
        self._page = None
        self._context = None
        self._launch_error: "str | None" = None
        self._refs = RefTable()

    # -- lifecycle ---------------------------------------------------------

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def is_healthy(self) -> bool:
        """Running *and* holding a usable page. The thread can outlive the
        page for the moment between a crash and the loop unwinding."""
        if not self.is_running():
            return False
        page = self._page
        if page is None:
            return False
        try:
            return not page.is_closed()
        except Exception:
            return False

    def profile_dir(self) -> str:
        return os.path.join(config.DATA_DIR, "browser_profile")

    def _ensure_started(self) -> "str | None":
        """Bring the browser up if it isn't. Returns an error message, or None
        if there's a page ready to drive."""
        with self._lock:
            if self.is_running():
                return None
            self._stop.clear()
            self._ready.clear()
            self._launch_error = None
            self._refs.reset()
            self._thread = threading.Thread(target=self._run, daemon=True, name="odin-browser")
            self._thread.start()

        launch_timeout = getattr(config, "BROWSER_NAV_TIMEOUT_SECONDS", 30.0)
        if not self._ready.wait(launch_timeout + SUBMIT_GRACE_SECONDS):
            return "The browser didn't finish starting up in time."
        return self._launch_error

    def close(self) -> str:
        """Shut the browser down. Never launches one to close it, and never
        waits on the job queue — a wedged page must not be able to stop the
        user from turning this off."""
        with self._lock:
            thread = self._thread
            self._thread = None
            if thread is None or not thread.is_alive():
                return "The browser is already closed."
            self._stop.set()

        thread.join(timeout=10.0)
        self._refs.reset()
        return "Closed the browser."

    # Lifecycle alias so app.py's shutdown reads the same as every other
    # background owner it tears down.
    stop = close

    # -- the browser thread ------------------------------------------------

    def _launch(self, playwright):
        """Returns (context, error_message).

        BROWSER_CHANNEL names the real installed Chrome by default: sites that
        fingerprint aggressively (Instagram among them) treat bundled Chromium
        with more suspicion, and the user's own Chrome is what their existing
        logins came from. Falling back to bundled Chromium keeps the feature
        usable on a machine where that channel simply isn't installed.
        """
        profile = self.profile_dir()
        os.makedirs(profile, exist_ok=True)

        channel = (getattr(config, "BROWSER_CHANNEL", "chrome") or "").strip()
        attempts = ([channel] if channel else []) + [""]

        last_error = None
        for candidate in attempts:
            kwargs = {
                "user_data_dir": profile,
                "headless": False,
                "args": ["--disable-blink-features=AutomationControlled"],
            }
            if candidate:
                kwargs["channel"] = candidate
            try:
                return playwright.chromium.launch_persistent_context(**kwargs), None
            except Exception as e:
                last_error = e

        return None, (
            f"Couldn't launch a browser: {last_error}. If playwright is "
            "installed but its browsers aren't, run: playwright install chrome"
        )

    def _run(self) -> None:
        module, problem = _playwright()
        if problem:
            self._launch_error = problem
            self._ready.set()
            return

        driver = None
        context = None
        try:
            driver = module.sync_playwright().start()
            context, problem = self._launch(driver)
            if problem:
                self._launch_error = problem
                self._ready.set()
                return

            page = context.pages[0] if context.pages else context.new_page()
            # Both strictly shorter than submit()'s own wait, so a hung page
            # unwinds here rather than leaving the one worker thread parked
            # behind a job nothing will ever unblock.
            page.set_default_timeout(getattr(config, "BROWSER_ACTION_TIMEOUT_SECONDS", 15.0) * 1000)
            page.set_default_navigation_timeout(
                getattr(config, "BROWSER_NAV_TIMEOUT_SECONDS", 30.0) * 1000
            )

            self._context = context
            self._page = page
            self._ready.set()
            self._serve()
        except Exception as e:  # noqa: BLE001 - a broken launch must report, not crash ODIN
            self._launch_error = f"Couldn't start the browser: {e}"
            self._ready.set()
        finally:
            self._page = None
            self._context = None
            try:
                if context is not None:
                    context.close()
            except Exception:
                pass
            try:
                if driver is not None:
                    driver.stop()
            except Exception:
                pass

    def _serve(self) -> None:
        """Run queued jobs until stopped, or until the browser dies under us."""
        while not self._stop.is_set():
            try:
                job = self._jobs.get(timeout=0.25)
            except queue.Empty:
                continue

            gone = False
            try:
                job.result = job.func()
            except Exception as e:  # noqa: BLE001 - reported to the model, never raised
                gone = _is_browser_gone(e)
                job.error = (
                    "The browser window was closed. Ask me to open the page "
                    "again and I'll start a fresh one."
                    if gone
                    else f"The browser couldn't do that: {e}"
                )
            finally:
                job.done.set()

            if gone:
                # Leave the loop so is_running() goes false and the next
                # navigate relaunches cleanly instead of throwing against
                # handles that point at a dead process.
                return

    def submit(self, func, timeout: "float | None" = None) -> str:
        """Run `func` on the browser thread and wait for its result.

        A timeout here means "stop waiting", not "kill the browser": the job
        stays queued and the thread finishes it in its own time, and because
        jobs are serialised the next call simply queues behind it. Tearing the
        browser down on a slow page would lose the user's session for nothing.
        """
        problem = self._ensure_started()
        if problem:
            return problem

        job = _Job(func)
        self._jobs.put(job)

        if timeout is None:
            timeout = getattr(config, "BROWSER_ACTION_TIMEOUT_SECONDS", 15.0)
        if not job.done.wait(timeout + SUBMIT_GRACE_SECONDS):
            return (
                "The browser didn't respond in time. It may still be loading — "
                "try again, or ask me to close the browser."
            )
        if job.error:
            return job.error
        return job.result if job.result is not None else "Done."

    # -- actions (public: called from any thread) ---------------------------

    def navigate(self, url: str) -> str:
        return self.submit(
            lambda: self._navigate_impl(url),
            timeout=getattr(config, "BROWSER_NAV_TIMEOUT_SECONDS", 30.0),
        )

    def read(self) -> str:
        return self.submit(self._read_impl)

    def click(self, ref: str) -> str:
        return self.submit(lambda: self._click_impl(ref))

    def type_text(self, ref: str, text: str, submit: bool = False) -> str:
        return self.submit(lambda: self._type_impl(ref, text, submit))

    def scroll(self, amount: int, ref: str = "") -> str:
        return self.submit(lambda: self._scroll_impl(amount, ref))

    # -- actions (private: only ever run on the browser thread) -------------

    def _stale(self, ref: str) -> str:
        return (
            f"There's no element {ref} on the page any more. Call browser_read "
            "for a fresh list of refs, then try again."
        )

    def _navigate_impl(self, url: str) -> str:
        page = self._page
        # domcontentloaded, not Playwright's default "load": a chat or social
        # app holding a websocket or long-poll open may never fire load at
        # all, and waiting for it would time out on a page that's been usable
        # for seconds.
        page.goto(url, wait_until="domcontentloaded")
        # The snapshot comes back with the navigation rather than costing a
        # second round trip. Safe here in a way it wouldn't be after a click:
        # the navigation is already settled.
        return f"Opened {page.url}\n\n{self._read_impl()}"

    def _read_impl(self) -> str:
        page = self._page
        max_elements = getattr(config, "BROWSER_MAX_ELEMENTS", 60)
        scan_cap = max(1, max_elements) * SCAN_CAP_MULTIPLE

        collected = []
        for role in INTERACTIVE_ROLES:
            if len(collected) >= scan_cap:
                break
            try:
                locators = page.get_by_role(role).all()
            except Exception:
                continue
            for locator in locators:
                if len(collected) >= scan_cap:
                    break
                try:
                    if not locator.is_visible():
                        continue
                except Exception:
                    continue
                name = _accessible_name(locator)
                if not name:
                    continue
                collected.append((role, name, locator))

        try:
            title = page.title()
        except Exception:
            title = ""
        header = f"{title} — {page.url}" if title else page.url
        return f"{header}\n{self._refs.build(collected, max_elements)}"

    def _click_impl(self, ref: str) -> str:
        element = self._refs.resolve(ref)
        if element is None:
            return self._stale(ref)
        element.click()
        return (
            f"Clicked {ref}. Call browser_read to see what the page shows now — "
            "a click that returned no error hasn't necessarily done what you wanted."
        )

    def _type_impl(self, ref: str, text: str, submit: bool) -> str:
        element = self._refs.resolve(ref)
        if element is None:
            return self._stale(ref)

        element.click()
        # press_sequentially, not fill(): a live-filtering search box (the DM
        # contact search this feature exists for, among many) reacts to real
        # per-keystroke events. fill() sets the value in one go and such a box
        # never runs its own filter, so the results never update.
        element.press_sequentially(text)
        if submit:
            element.press("Enter")
            return f"Typed into {ref} and pressed Enter. Call browser_read to confirm what happened."
        return f"Typed into {ref}. Call browser_read to confirm what the page shows now."

    def _scroll_impl(self, amount: int, ref: str) -> str:
        page = self._page
        # Hover first, then send a real wheel event. window.scrollBy would
        # scroll the document, and modern chat/social apps scroll an inner
        # overflow panel instead — on exactly the screens this feature exists
        # for, a document scroll silently does nothing at all.
        if ref:
            element = self._refs.resolve(ref)
            if element is None:
                return self._stale(ref)
            element.hover()
        else:
            size = page.viewport_size or {"width": 1280, "height": 800}
            page.mouse.move(size["width"] / 2, size["height"] / 2)

        page.mouse.wheel(0, int(amount))
        direction = "down" if amount >= 0 else "up"
        return f"Scrolled {direction}. Call browser_read to see what's visible now."


def make_controller() -> "BrowserController | None":
    """Build the controller, or None if the feature is off. Never raises: a
    broken build here must not stop ODIN from starting."""
    if not getattr(config, "ENABLE_BROWSER_AUTOMATION", False):
        return None
    try:
        return BrowserController()
    except Exception as e:
        print(f"[browser] {e}")
        return None


_CONTROLLER: "BrowserController | None" = None
_CONTROLLER_LOCK = threading.Lock()


def get_browser_controller() -> BrowserController:
    """Process-wide singleton, mirroring core.gesture.get_gesture_controller().
    Falls back to a bare, flag-blind BrowserController if nothing has called
    set_browser_controller() yet, so a skill invoked before any startup wiring
    (e.g. in a test) still gets a real, usable object rather than None."""
    global _CONTROLLER
    with _CONTROLLER_LOCK:
        if _CONTROLLER is None:
            _CONTROLLER = BrowserController()
        return _CONTROLLER


def set_browser_controller(controller: "BrowserController | None") -> None:
    """Replace the process-wide controller. Used by tests, and available to
    app.py should the browser ever need UI wiring the way gesture does."""
    global _CONTROLLER
    with _CONTROLLER_LOCK:
        _CONTROLLER = controller
