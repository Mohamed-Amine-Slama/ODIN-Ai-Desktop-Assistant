"""Skills that reach out to the web.

Note: actual web *search* is handled by Anthropic's server-side `web_search`
and `web_fetch` tools (declared in skill_manager.SERVER_TOOLS), not by a skill
here. Those run on Anthropic's infrastructure and return real results the model
can answer from — unlike the old WebSearchSkill, which only opened a browser
tab and left the model with nothing to say.

What stays local is anything that has to happen on *this* machine.
"""
import re
import urllib.parse
import webbrowser

import requests

from .base_skill import BaseSkill

# Matches a leading URI scheme, e.g. "https:", "file:", "javascript:".
# A bare "localhost:8080" also matches, so the port case is excluded below.
_SCHEME_RE = re.compile(r"^([a-zA-Z][a-zA-Z0-9+.\-]*):(.*)$", re.DOTALL)


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


class OpenWebsiteSkill(BaseSkill):
    name = "open_website"
    description = (
        "Open a website in the user's browser on their PC. Use for 'open "
        "YouTube', 'pull up GitHub', or to show the user a specific page. "
        "This only opens the page — it does not read it. To answer a question "
        "using the web, use web_search instead."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "site": {
                "type": "string",
                "description": "URL or well-known site name, e.g. 'youtube' or 'https://news.ycombinator.com'.",
            }
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
    }

    def run(self, site: str) -> str:
        key = site.strip().lower()
        raw = self.KNOWN.get(key, site.strip())

        # webbrowser.open hands the URL to the OS handler, so a model-supplied
        # 'file:///...' or 'javascript:' must never reach it.
        url = _to_web_url(raw)
        if url is None:
            return f"'{site}' isn't a web address I can open."

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
