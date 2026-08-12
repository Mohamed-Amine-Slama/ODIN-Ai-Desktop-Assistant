"""Email + calendar backends for Google and Microsoft.

Both are optional infrastructure, same philosophy as core.knowledge: the
google-auth-oauthlib/google-api-python-client and msal packages are only
imported inside the functions that need them, so this module always imports
cleanly, and skills/email_skills.py turns a missing package or missing OAuth
setup into a plain-English reply instead of a crash.

Each provider needs one interactive browser consent per account before it can
be used — that's connect(), invoked from main.py's `/connect google` and
`/connect microsoft` commands, not from a skill (OAuth needs a real browser
and a human present, which a model-driven tool call can't provide).

Token storage: data/oauth/ — gitignored, holds refresh tokens. Losing it just
means running /connect again, not losing anything durable.
"""
import json
import os
import time

import requests

import config

GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/calendar",
]
MS_SCOPES = ["Mail.Read", "Mail.Send", "Calendars.ReadWrite"]
GRAPH_ROOT = "https://graph.microsoft.com/v1.0"


def _oauth_dir() -> str:
    path = os.path.join(config.DATA_DIR, "oauth")
    os.makedirs(path, exist_ok=True)
    return path


def _google_credentials_path() -> str:
    return os.path.join(_oauth_dir(), "google_credentials.json")


def _google_token_path() -> str:
    return os.path.join(_oauth_dir(), "google_token.json")


def _ms_token_cache_path() -> str:
    return os.path.join(_oauth_dir(), "ms_token_cache.json")


def google_configured() -> bool:
    """Whether Google can at least attempt /connect (a client-secrets file
    is present) or is already connected (a token exists)."""
    return os.path.exists(_google_credentials_path()) or os.path.exists(_google_token_path())


def google_ready() -> bool:
    """Whether Google is fully connected — a token already exists."""
    return os.path.exists(_google_token_path())


def microsoft_configured() -> bool:
    return bool(os.getenv("MS_OAUTH_CLIENT_ID", "")) or os.path.exists(_ms_token_cache_path())


def microsoft_ready() -> bool:
    return os.path.exists(_ms_token_cache_path())


class ProviderError(Exception):
    """Raised with a user-facing message; skills turn this into a plain reply."""


# -- Google ------------------------------------------------------------------


class GoogleProvider:
    name = "google"

    def __init__(self):
        self._service_cache: dict[str, object] = {}

    def connect(self) -> str:
        """Interactive one-time browser consent. Never called from a skill —
        only from main.py's /connect command, which has a real human at the
        keyboard to complete it."""
        try:
            from google_auth_oauthlib.flow import InstalledAppFlow
        except ImportError as e:
            raise ProviderError(
                "Google needs the 'google-auth-oauthlib' and "
                "'google-api-python-client' packages. Run: "
                "pip install -r requirements-email.txt"
            ) from e

        secrets = _google_credentials_path()
        if not os.path.exists(secrets):
            raise ProviderError(
                "No Google OAuth client found. Create one at "
                "console.cloud.google.com (OAuth client ID, type 'Desktop app'), "
                f"download it, and save it as {secrets}."
            )

        flow = InstalledAppFlow.from_client_secrets_file(secrets, GOOGLE_SCOPES)
        creds = flow.run_local_server(port=0)
        with open(_google_token_path(), "w", encoding="utf-8") as f:
            f.write(creds.to_json())
        return "Google account connected."

    def _credentials(self):
        try:
            from google.auth.transport.requests import Request
            from google.oauth2.credentials import Credentials
        except ImportError as e:
            raise ProviderError(
                "Google needs the 'google-auth-oauthlib' and "
                "'google-api-python-client' packages. Run: "
                "pip install -r requirements-email.txt"
            ) from e

        token_path = _google_token_path()
        if not os.path.exists(token_path):
            raise ProviderError("Google isn't connected yet. Run /connect google first.")

        creds = Credentials.from_authorized_user_file(token_path, GOOGLE_SCOPES)
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            with open(token_path, "w", encoding="utf-8") as f:
                f.write(creds.to_json())
        return creds

    def _service(self, name: str, version: str):
        if name not in self._service_cache:
            try:
                from googleapiclient.discovery import build
            except ImportError as e:
                raise ProviderError(
                    "Google needs the 'google-auth-oauthlib' and "
                    "'google-api-python-client' packages. Run: "
                    "pip install -r requirements-email.txt"
                ) from e

            self._service_cache[name] = build(
                name, version, credentials=self._credentials(), cache_discovery=False
            )
        return self._service_cache[name]

    def list_recent_mail(self, n: int) -> list[dict]:
        gmail = self._service("gmail", "v1")
        try:
            listing = gmail.users().messages().list(userId="me", maxResults=n).execute()
        except Exception as e:
            raise ProviderError(f"Couldn't list Gmail: {e}") from e

        out = []
        for item in listing.get("messages", []):
            msg = (
                gmail.users()
                .messages()
                .get(userId="me", id=item["id"], format="metadata",
                     metadataHeaders=["From", "Subject", "Date"])
                .execute()
            )
            headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
            out.append({
                "from": headers.get("From", ""),
                "subject": headers.get("Subject", "(no subject)"),
                "date": headers.get("Date", ""),
                "snippet": msg.get("snippet", ""),
            })
        return out

    def send_mail(self, to: str, subject: str, body: str) -> None:
        import base64
        from email.mime.text import MIMEText

        gmail = self._service("gmail", "v1")
        mime = MIMEText(body)
        mime["to"] = to
        mime["subject"] = subject
        raw = base64.urlsafe_b64encode(mime.as_bytes()).decode("ascii")
        try:
            gmail.users().messages().send(userId="me", body={"raw": raw}).execute()
        except Exception as e:
            raise ProviderError(f"Couldn't send that email: {e}") from e

    def list_events(self, days_ahead: int) -> list[dict]:
        import datetime

        calendar = self._service("calendar", "v3")
        now = datetime.datetime.utcnow()
        time_min = now.isoformat() + "Z"
        time_max = (now + datetime.timedelta(days=days_ahead)).isoformat() + "Z"
        try:
            result = (
                calendar.events()
                .list(calendarId="primary", timeMin=time_min, timeMax=time_max,
                      singleEvents=True, orderBy="startTime")
                .execute()
            )
        except Exception as e:
            raise ProviderError(f"Couldn't list calendar events: {e}") from e

        return [
            {
                "id": e["id"],
                "title": e.get("summary", "(untitled)"),
                "start": e.get("start", {}).get("dateTime", e.get("start", {}).get("date", "")),
                "end": e.get("end", {}).get("dateTime", e.get("end", {}).get("date", "")),
            }
            for e in result.get("items", [])
        ]

    def create_event(self, title: str, start: str, end: str, description: str = "") -> str:
        calendar = self._service("calendar", "v3")
        body = {
            "summary": title,
            "description": description,
            "start": {"dateTime": start},
            "end": {"dateTime": end},
        }
        try:
            created = calendar.events().insert(calendarId="primary", body=body).execute()
        except Exception as e:
            raise ProviderError(f"Couldn't create that event: {e}") from e
        return created["id"]

    def delete_event(self, event_id: str) -> None:
        calendar = self._service("calendar", "v3")
        try:
            calendar.events().delete(calendarId="primary", eventId=event_id).execute()
        except Exception as e:
            raise ProviderError(f"Couldn't delete that event: {e}") from e


# -- Microsoft -----------------------------------------------------------------


class MicrosoftProvider:
    name = "microsoft"

    def __init__(self):
        self._app = None

    def _client_id(self) -> str:
        client_id = os.getenv("MS_OAUTH_CLIENT_ID", "")
        if not client_id:
            raise ProviderError(
                "No Microsoft app registered. Create one (public client, "
                "no secret) at portal.azure.com, then set MS_OAUTH_CLIENT_ID "
                "in .env."
            )
        return client_id

    def _application(self):
        if self._app is None:
            try:
                import msal
            except ImportError as e:
                raise ProviderError(
                    "Microsoft needs the 'msal' package. Run: "
                    "pip install -r requirements-email.txt"
                ) from e

            cache = msal.SerializableTokenCache()
            cache_path = _ms_token_cache_path()
            if os.path.exists(cache_path):
                cache.deserialize(open(cache_path, "r", encoding="utf-8").read())

            tenant = os.getenv("MS_OAUTH_TENANT_ID", "common")
            self._app = msal.PublicClientApplication(
                self._client_id(),
                authority=f"https://login.microsoftonline.com/{tenant}",
                token_cache=cache,
            )
            self._cache = cache
        return self._app

    def _save_cache(self) -> None:
        if getattr(self, "_cache", None) is not None and self._cache.has_state_changed:
            with open(_ms_token_cache_path(), "w", encoding="utf-8") as f:
                f.write(self._cache.serialize())

    def connect(self) -> str:
        """Interactive one-time browser consent (device-code flow — works
        without a locally-registered redirect URI). Only called from
        main.py's /connect command."""
        app = self._application()
        flow = app.initiate_device_flow(scopes=MS_SCOPES)
        if "user_code" not in flow:
            raise ProviderError(f"Couldn't start the Microsoft sign-in flow: {flow}")
        print(flow["message"])  # tells the user the URL + code to enter
        result = app.acquire_token_by_device_flow(flow)
        self._save_cache()
        if "access_token" not in result:
            raise ProviderError(
                f"Microsoft sign-in failed: {result.get('error_description', result)}"
            )
        return "Microsoft account connected."

    def _token(self) -> str:
        app = self._application()
        accounts = app.get_accounts()
        if not accounts:
            raise ProviderError("Microsoft isn't connected yet. Run /connect microsoft first.")
        result = app.acquire_token_silent(MS_SCOPES, account=accounts[0])
        self._save_cache()
        if not result or "access_token" not in result:
            raise ProviderError("Microsoft sign-in expired. Run /connect microsoft again.")
        return result["access_token"]

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._token()}", "Content-Type": "application/json"}

    def list_recent_mail(self, n: int) -> list[dict]:
        try:
            resp = requests.get(
                f"{GRAPH_ROOT}/me/messages",
                headers=self._headers(),
                params={"$top": n, "$select": "from,subject,receivedDateTime,bodyPreview"},
                timeout=15,
            )
            resp.raise_for_status()
        except requests.RequestException as e:
            raise ProviderError(f"Couldn't list mail: {e}") from e

        return [
            {
                "from": (m.get("from") or {}).get("emailAddress", {}).get("address", ""),
                "subject": m.get("subject", "(no subject)"),
                "date": m.get("receivedDateTime", ""),
                "snippet": m.get("bodyPreview", ""),
            }
            for m in resp.json().get("value", [])
        ]

    def send_mail(self, to: str, subject: str, body: str) -> None:
        payload = {
            "message": {
                "subject": subject,
                "body": {"contentType": "Text", "content": body},
                "toRecipients": [{"emailAddress": {"address": to}}],
            }
        }
        try:
            resp = requests.post(
                f"{GRAPH_ROOT}/me/sendMail", headers=self._headers(), json=payload, timeout=15
            )
            resp.raise_for_status()
        except requests.RequestException as e:
            raise ProviderError(f"Couldn't send that email: {e}") from e

    def list_events(self, days_ahead: int) -> list[dict]:
        import datetime

        now = datetime.datetime.utcnow()
        params = {
            "startDateTime": now.isoformat() + "Z",
            "endDateTime": (now + datetime.timedelta(days=days_ahead)).isoformat() + "Z",
            "$select": "id,subject,start,end",
            "$orderby": "start/dateTime",
        }
        try:
            resp = requests.get(
                f"{GRAPH_ROOT}/me/calendarView", headers=self._headers(), params=params, timeout=15
            )
            resp.raise_for_status()
        except requests.RequestException as e:
            raise ProviderError(f"Couldn't list calendar events: {e}") from e

        return [
            {
                "id": e["id"],
                "title": e.get("subject", "(untitled)"),
                "start": e.get("start", {}).get("dateTime", ""),
                "end": e.get("end", {}).get("dateTime", ""),
            }
            for e in resp.json().get("value", [])
        ]

    def create_event(self, title: str, start: str, end: str, description: str = "") -> str:
        payload = {
            "subject": title,
            "body": {"contentType": "Text", "content": description},
            "start": {"dateTime": start, "timeZone": "UTC"},
            "end": {"dateTime": end, "timeZone": "UTC"},
        }
        try:
            resp = requests.post(
                f"{GRAPH_ROOT}/me/events", headers=self._headers(), json=payload, timeout=15
            )
            resp.raise_for_status()
        except requests.RequestException as e:
            raise ProviderError(f"Couldn't create that event: {e}") from e
        return resp.json()["id"]

    def delete_event(self, event_id: str) -> None:
        try:
            resp = requests.delete(
                f"{GRAPH_ROOT}/me/events/{event_id}", headers=self._headers(), timeout=15
            )
            resp.raise_for_status()
        except requests.RequestException as e:
            raise ProviderError(f"Couldn't delete that event: {e}") from e


_providers: dict[str, object] = {}


def get_provider(account: str):
    """Return the cached provider instance for 'google' or 'microsoft'."""
    if account not in _providers:
        _providers[account] = GoogleProvider() if account == "google" else MicrosoftProvider()
    return _providers[account]


def default_account() -> str | None:
    """The account to use when the caller didn't specify one: the only
    configured provider, or None if both/neither are (ambiguous either way —
    the skill asks rather than silently guessing)."""
    google, ms = google_ready(), microsoft_ready()
    if google and not ms:
        return "google"
    if ms and not google:
        return "microsoft"
    return None
