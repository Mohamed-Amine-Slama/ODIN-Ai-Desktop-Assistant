"""Skills that reach out to the web.

On the Anthropic path, web *search* is handled by Anthropic's server-side
`web_search`/`web_fetch` tools (declared in skill_manager.SERVER_TOOLS)
instead of the local WebSearchSkill here — SkillManager drops the local one
whenever a server tool shares its name, since only one of the two may be
declared per request. On every other (OpenAI-compatible) provider, which has
no server-side tools of its own, WebSearchSkill below is what gives Jarvis
real search results instead of just opening a browser tab.

What stays local otherwise is anything that has to happen on *this* machine.
"""
import os
import re
import shutil
import socket
import subprocess
import sys
import urllib.parse
import webbrowser
from html.parser import HTMLParser
from ipaddress import ip_address

import requests

import config
from .base_skill import BaseSkill
from .system_skills import _resolve_windows_app_executable

IS_WINDOWS = sys.platform == "win32"

# Matches a leading URI scheme, e.g. "https:", "file:", "javascript:".
# A bare "localhost:8080" also matches, so the port case is excluded below.
_SCHEME_RE = re.compile(r"^([a-zA-Z][a-zA-Z0-9+.\-]*):(.*)$", re.DOTALL)
_FETCH_MAX_CHARS = 24_000
_FETCH_MAX_BYTES = 1_000_000

def web_search_available() -> bool:
    """Whether the optional 'ddgs' package is installed. No API key, no
    signup, no billing needed — unlike every LLM-provider-hosted search
    option, this needs nothing beyond the package itself."""
    try:
        import ddgs  # noqa: F401
    except ImportError:
        return False
    return True


def _to_web_url(raw: str) -> str | None:
    """Normalise user/model input to an http(s) URL, or None if it isn't one.

    The scheme has to be checked *before* prepending https://, otherwise
    'file:///etc/passwd' becomes 'https://file:///etc/passwd', which parses as
    a valid https URL and sails through validation.
    """
    raw = raw.strip()
    if not raw:
        return None

    match = _SCHEME_RE.match(raw)
    if match:
        scheme, rest = match.group(1).lower(), match.group(2)
        # "localhost:8080" / "example.com:443" are host:port, not a scheme.
        is_port = rest.split("/", 1)[0].isdigit()
        if not is_port:
            if scheme not in ("http", "https"):
                return None
        else:
            raw = "https://" + raw
    else:
        raw = "https://" + raw

    parsed = urllib.parse.urlparse(raw)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return None
    return parsed.geturl()


_BROWSER_EXE_NAMES = {
    "chrome": "chrome.exe",
    "google chrome": "chrome.exe",
    "edge": "msedge.exe",
    "microsoft edge": "msedge.exe",
    "firefox": "firefox.exe",
    "opera": "opera.exe",
    "opera gx": "opera.exe",
    "operagx": "opera.exe",
    "brave": "brave.exe",
}


def _registry_app_path(exe_name: str) -> str | None:
    """Look up an executable's install path via the Windows 'App Paths' key,
    which browser installers register regardless of install location."""
    if not IS_WINDOWS:
        return None
    import winreg

    subkey = rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe_name}"
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(hive, subkey) as key:
                path, _ = winreg.QueryValueEx(key, "")
        except OSError:
            continue
        if path and os.path.exists(path):
            return path
    return None


def _resolve_browser_executable(name: str) -> str | None:
    """Find a specific browser's executable so a URL can be handed to it as an
    argument. os.startfile can launch a browser by name but takes no
    arguments, so opening a page in a NON-default browser needs the real path.

    Checked in order: known non-default install locations (Opera/Opera GX/
    Brave aren't always on PATH or registered), the registry, then PATH.
    """
    key = name.strip().lower()

    resolved = _resolve_windows_app_executable(key)
    if resolved:
        return resolved

    exe = _BROWSER_EXE_NAMES.get(key)
    if not exe:
        return None

    resolved = _registry_app_path(exe)
    if resolved:
        return resolved

    return shutil.which(exe)


class OpenWebsiteSkill(BaseSkill):
    name = "open_website"
    description = (
        "Open a website in the user's browser on their PC. Use for 'open "
        "YouTube', 'pull up GitHub', or to show the user a specific page. Pass "
        "'browser' to open it in a specific browser instead of the system "
        "default — needed for requests like 'open Instagram in Opera GX'. "
        "This only opens the page — it does not read it. To answer a question "
        "using the web, use web_search instead."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "site": {
                "type": "string",
                "description": "URL or well-known site name, e.g. 'youtube' or 'https://news.ycombinator.com'.",
            },
            "browser": {
                "type": "string",
                "description": (
                    "Optional. Open in this browser instead of the default, "
                    "e.g. 'chrome', 'opera gx', 'firefox', 'edge', 'brave'."
                ),
            },
        },
        "required": ["site"],
    }

    KNOWN = {
        "youtube": "https://youtube.com",
        "github": "https://github.com",
        "gmail": "https://mail.google.com",
        "google": "https://google.com",
        "reddit": "https://reddit.com",
        "twitter": "https://x.com",
        "x": "https://x.com",
        "maps": "https://maps.google.com",
        "drive": "https://drive.google.com",
        "calendar": "https://calendar.google.com",
        "instagram": "https://instagram.com",
        "facebook": "https://facebook.com",
        "whatsapp": "https://web.whatsapp.com",
        "discord": "https://discord.com/app",
        "linkedin": "https://linkedin.com",
        "tiktok": "https://tiktok.com",
    }

    def run(self, site: str, browser: str = "") -> str:
        key = site.strip().lower()
        raw = self.KNOWN.get(key, site.strip())

        # webbrowser.open hands the URL to the OS handler, so a model-supplied
        # 'file:///...' or 'javascript:' must never reach it.
        url = _to_web_url(raw)
        if url is None:
            return f"'{site}' isn't a web address I can open."

        if browser:
            exe = _resolve_browser_executable(browser)
            if exe:
                try:
                    subprocess.Popen([exe, url], shell=False)
                    return f"Opening {urllib.parse.urlparse(url).netloc} in {browser}."
                except OSError as e:
                    return f"Found {browser} but couldn't launch it: {e}"
            webbrowser.open(url)
            return (
                f"I couldn't find '{browser}' installed, so I opened "
                f"{urllib.parse.urlparse(url).netloc} in your default browser instead."
            )

        webbrowser.open(url)
        return f"Opening {urllib.parse.urlparse(url).netloc}."


class SearchInBrowserSkill(BaseSkill):
    name = "search_in_browser"
    description = (
        "Open a Google search results page in the user's browser. Use ONLY when "
        "the user explicitly wants to browse results themselves ('google that "
        "for me', 'show me search results'). To answer a question yourself "
        "using the web, use web_search instead."
    )
    input_schema = {
        "type": "object",
        "properties": {"query": {"type": "string", "description": "What to search for."}},
        "required": ["query"],
    }

    def run(self, query: str) -> str:
        url = "https://www.google.com/search?q=" + urllib.parse.quote_plus(query)
        webbrowser.open(url)
        return f"Opened a search for {query}."


class WebSearchSkill(BaseSkill):
    """DuckDuckGo search (via the `ddgs` package), exposed as a normal local
    tool. No API key, no signup, no billing — the only search option here
    that needs nothing beyond the package itself.

    Returns raw results (title/url/snippet) rather than a pre-synthesized
    answer — ddgs is a plain search library with no LLM behind it, so the
    calling model reads the results and writes the answer itself, same as it
    would for any other tool_result.
    """

    name = "web_search"
    description = (
        "Search the live web and return the top results (title, URL, "
        "snippet) for you to read and answer from — cite sources by URL. Use "
        "for current events, recent releases, live prices, and any fact that "
        "may have changed since training."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The current-information question or search query.",
            }
        },
        "required": ["query"],
    }

    def run(self, query: str) -> str:
        query = query.strip()
        if not query:
            return "I need something to search for."

        try:
            results = web_search(query)
        except RuntimeError as exc:
            return str(exc)
        if not results:
            return "That search returned nothing usable."
        return "\n".join(
            f"- {r['title']} ({r['url']}): {r['snippet']}" for r in results
        )


def web_search(query: str, count: int = 6) -> list[dict]:
    """Raw DuckDuckGo search results: [{title, url, snippet}]. Raises
    RuntimeError with a user-facing message on failure. Shared by
    WebSearchSkill and core.research's deep_learn pipeline, so both search
    the same way."""
    try:
        from ddgs import DDGS
    except ImportError as e:
        raise RuntimeError("Web search needs the 'ddgs' package. Run: pip install ddgs") from e

    try:
        with DDGS() as ddgs:
            raw = list(ddgs.text(query, max_results=count))
        return [
            {"title": r.get("title", ""), "url": r.get("href", ""), "snippet": r.get("body", "")}
            for r in raw
        ]
    except Exception as e:
        # Covers a malformed/unexpected result shape from ddgs too, not just
        # the search call itself — deep_learn's callers only catch
        # RuntimeError (see core/research.py) to skip one subtopic rather
        # than aborting the whole run, so any other exception type here
        # would defeat that.
        raise RuntimeError(f"I couldn't search the web: {e}") from e


class WebFetchSkill(BaseSkill):
    """Fetch readable text from a public URL without opening a browser."""

    name = "web_fetch"
    description = (
        "Fetch and read a specific public web page without opening a browser. "
        "Use after web_search when a particular result needs closer inspection."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Public http(s) URL to read."}
        },
        "required": ["url"],
    }

    def run(self, url: str) -> str:
        normalized = _to_web_url(url)
        if normalized is None:
            return "I can only fetch a valid public http(s) URL."
        if not _is_public_url(normalized):
            return "I won't fetch local, private-network, or unresolved addresses."

        try:
            response = requests.get(
                normalized,
                timeout=15,
                stream=True,
                allow_redirects=False,
                headers={"User-Agent": f"{config.ASSISTANT_NAME}/1.0 (personal assistant)"},
            )
        except requests.Timeout:
            return "That page did not respond in time."
        except requests.RequestException as exc:
            return f"I couldn't fetch that page: {exc}"

        try:
            if 300 <= response.status_code < 400:
                return "That page redirects elsewhere; search for the final public URL instead."
            if response.status_code != 200:
                return f"I couldn't fetch that page (status {response.status_code})."

            content_type = response.headers.get("content-type", "").lower()
            if content_type and not any(kind in content_type for kind in ("text/", "json", "xml")):
                return f"That URL returned {content_type}, not readable text."

            raw = bytearray()
            for chunk in response.iter_content(chunk_size=16_384):
                raw.extend(chunk)
                if len(raw) >= _FETCH_MAX_BYTES:
                    break
        finally:
            response.close()

        text = _decode_response(bytes(raw), content_type, response.encoding)
        if "html" in content_type or "<html" in text[:500].lower():
            text = _html_text(text)
        text = " ".join(text.split())
        if not text:
            return "That page had no readable text."
        if len(text) > _FETCH_MAX_CHARS:
            text = text[:_FETCH_MAX_CHARS].rstrip() + "\n\n[Page text truncated.]"
        return text


def _decode_response(raw: bytes, content_type: str, declared_encoding: str | None) -> str:
    """Decode a fetched page's bytes to text.

    requests defaults response.encoding to ISO-8859-1 for any text/* content
    type that doesn't declare its own charset (get_encoding_from_headers) —
    which is almost always wrong, since most modern pages are UTF-8 and
    simply didn't bother declaring it. Trusting that default unconditionally
    turned every such page into mojibake. Only a charset actually present in
    the Content-Type header is trustworthy; otherwise UTF-8 is tried first
    (a real decode failure is a good signal it genuinely isn't UTF-8) before
    falling back to whatever requests guessed.
    """
    if "charset=" in content_type and declared_encoding:
        return raw.decode(declared_encoding, errors="replace")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode(declared_encoding or "utf-8", errors="replace")


class WeatherSkill(BaseSkill):
    name = "get_weather"
    description = (
        "Get the current weather for a city. Faster and cheaper than a web "
        "search for simple 'what's the weather' questions."
    )
    input_schema = {
        "type": "object",
        "properties": {"city": {"type": "string", "description": "City name."}},
        "required": ["city"],
    }

    def run(self, city: str) -> str:
        try:
            resp = requests.get(
                f"https://wttr.in/{urllib.parse.quote(city)}?format=3", timeout=5
            )
        except requests.Timeout:
            return "The weather service didn't respond in time."
        except requests.RequestException as e:
            return f"I couldn't fetch the weather: {e}"

        if resp.status_code == 200:
            return resp.text.strip()
        return f"I couldn't fetch the weather for {city} (status {resp.status_code})."


_DNS_TIMEOUT_SECONDS = 5


def _is_public_url(url: str) -> bool:
    """Refuse loopback, private, and unresolved hosts before an HTTP request."""
    host = urllib.parse.urlparse(url).hostname
    if not host or host.lower() in {"localhost", "localhost.localdomain"}:
        return False
    # getaddrinfo has no timeout parameter of its own; a slow/unresponsive
    # resolver would otherwise block well past the fetch's own 15s timeout,
    # which hasn't even started yet at this point. Scoped narrowly and always
    # restored, since this touches process-global socket state.
    previous_timeout = socket.getdefaulttimeout()
    socket.setdefaulttimeout(_DNS_TIMEOUT_SECONDS)
    try:
        addresses = {info[4][0] for info in socket.getaddrinfo(host, None)}
    except (socket.gaierror, socket.timeout, OSError):
        return False
    finally:
        socket.setdefaulttimeout(previous_timeout)
    try:
        return bool(addresses) and all(ip_address(address).is_global for address in addresses)
    except ValueError:
        return False


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):  # noqa: ARG002
        if tag.lower() in {"script", "style", "noscript", "svg"}:
            self._skip_depth += 1

    def handle_endtag(self, tag):
        if tag.lower() in {"script", "style", "noscript", "svg"} and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data):
        if not self._skip_depth:
            self.parts.append(data)


def _html_text(html: str) -> str:
    parser = _TextExtractor()
    parser.feed(html)
    parser.close()
    return " ".join(parser.parts)
