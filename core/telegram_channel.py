"""Optional Telegram bridge: text the assistant from your phone and get
replies back.

Long-polls Telegram's plain HTTPS Bot API directly with `requests` — no extra
dependency (no python-telegram-bot), consistent with the rest of Jarvis. Off
unless TELEGRAM_BOT_TOKEN is set.

Locked to one chat by TELEGRAM_CHAT_ID. This is a real security boundary, not
a convenience: a personal assistant that can read/write files, run shell
commands, and control the PC must not answer whoever happens to find the bot.
Until TELEGRAM_CHAT_ID is set, the bot replies to a first message with the
chat id to paste into .env and otherwise ignores it; once set, every chat
other than that one is silently ignored — not even acknowledged, so a stray
message reveals nothing about whether the bot is even alive.
"""
import threading
import time

import requests

import config

_API = "https://api.telegram.org/bot{token}/{method}"
_POLL_TIMEOUT_SECONDS = 25
_ERROR_BACKOFF_SECONDS = 5


class TelegramChannel:
    def __init__(self, on_message):
        """on_message: callable(text: str) -> str, returning the reply to send back.
        Runs on this channel's own polling thread — see main.py/app.py for how
        the callback is wired to Brain.ask() with an unattended confirm."""
        self.on_message = on_message
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._offset = 0

    def start(self) -> bool:
        if not config.TELEGRAM_BOT_TOKEN:
            return False
        if self._thread is not None:
            return True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()

    def send(self, chat_id, text: str) -> None:
        # Telegram caps a message at 4096 UTF-16 code units; comfortably safe
        # to just cap on characters given the short, spoken-style replies
        # this assistant produces.
        if len(text) > 4000:
            text = text[:4000] + "\n\n[truncated]"
        try:
            requests.post(
                _API.format(token=config.TELEGRAM_BOT_TOKEN, method="sendMessage"),
                json={"chat_id": chat_id, "text": text},
                timeout=15,
            )
        except requests.RequestException as e:
            print(f"[telegram] couldn't send a reply: {e}")

    # -- internals -----------------------------------------------------------

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                updates = self._get_updates()
            except requests.RequestException as e:
                print(f"[telegram] {e}")
                self._stop.wait(_ERROR_BACKOFF_SECONDS)
                continue
            for update in updates:
                self._handle_update(update)

    def _get_updates(self) -> list[dict]:
        resp = requests.get(
            _API.format(token=config.TELEGRAM_BOT_TOKEN, method="getUpdates"),
            params={"offset": self._offset, "timeout": _POLL_TIMEOUT_SECONDS},
            timeout=_POLL_TIMEOUT_SECONDS + 10,
        )
        resp.raise_for_status()
        return resp.json().get("result", [])

    def _handle_update(self, update: dict) -> None:
        self._offset = update["update_id"] + 1
        message = update.get("message") or {}
        text = message.get("text")
        chat_id = (message.get("chat") or {}).get("id")
        if not text or chat_id is None:
            return

        allowed = config.TELEGRAM_CHAT_ID
        if not allowed:
            print(f"[telegram] chat_id={chat_id} messaged the bot; TELEGRAM_CHAT_ID is not set yet.")
            self.send(chat_id, f"Set TELEGRAM_CHAT_ID={chat_id} in .env and restart to link this chat.")
            return
        if str(chat_id) != str(allowed):
            return  # not the linked chat — ignored, not even acknowledged

        try:
            reply = self.on_message(text)
        except Exception as e:  # noqa: BLE001 - a bad turn must not kill the poll loop
            reply = f"Something went wrong: {e}"
        self.send(chat_id, reply or "Done.")
