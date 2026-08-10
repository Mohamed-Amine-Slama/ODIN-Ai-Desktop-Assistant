"""Durable reminder scheduler.

The original used `threading.Timer(daemon=True)`, so every pending reminder was
lost the moment the process exited — silently. This polls SQLite instead, which
means reminders survive restarts and anything that came due while Jarvis was
closed fires as soon as it starts again.
"""
import threading
import time

import config

POLL_SECONDS = 15


class ReminderScheduler:
    def __init__(self, store, notify=None, poll_seconds: float = POLL_SECONDS):
        self.store = store
        self.notify = notify or _desktop_notification
        self.poll_seconds = poll_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def fire_due(self) -> int:
        """Fire everything currently due. Called on the poll loop and once at
        startup, which is what catches reminders missed while Jarvis was off."""
        fired = 0
        for row in self.store.due_reminders():
            late = time.time() - row["fire_at"]
            message = row["message"]
            if late > 120:
                message = f"{message} (was due {_ago(late)})"
            try:
                self.notify(message)
            except Exception as e:
                print(f"[reminder] couldn't notify: {e}")
            # Mark fired regardless — a notification we couldn't deliver should
            # not replay on every single poll.
            self.store.mark_fired(row["id"])
            fired += 1
        return fired

    def _loop(self) -> None:
        while not self._stop.wait(self.poll_seconds):
            try:
                self.fire_due()
            except Exception as e:
                print(f"[reminder] scheduler error: {e}")


def _desktop_notification(message: str) -> None:
    try:
        from plyer import notification
    except ImportError:
        notification = None

    if notification is not None:
        notification.notify(
            title=f"{config.ASSISTANT_NAME} reminder", message=message, timeout=15
        )
    else:
        print(f"\n[REMINDER] {message}\n")


def _ago(seconds: float) -> str:
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    days = hours // 24
    return f"{days} day{'s' if days != 1 else ''} ago"
