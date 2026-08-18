"""Durable reminder scheduler, plus recurring scheduled tasks.

The original reminder scheduler used `threading.Timer(daemon=True)`, so every
pending reminder was lost the moment the process exited — silently. This polls
SQLite instead, which means reminders survive restarts and anything that came
due while Jarvis was closed fires as soon as it starts again.

TaskScheduler (below) is a related but distinct idea: instead of firing a
one-off notification, it runs a full brain turn on a recurring schedule — "every
weekday at 8am, check my email and calendar and brief me" — reusing whatever
skills that prompt calls for, the same as if the user had typed it.
"""
import re
import threading
import time
from datetime import datetime, time as dt_time, timedelta

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


# -- scheduled tasks: schedule spec parsing ----------------------------------
# Deliberately not cron syntax — cron needs either a dependency (croniter) or
# a hand-rolled field-by-field parser, and "every weekday at 8am" doesn't need
# either. A day-selector plus a 24-hour time covers the recurring-briefing
# case this exists for without either cost.

_DAY_ALIASES = {
    "daily": {0, 1, 2, 3, 4, 5, 6},
    "everyday": {0, 1, 2, 3, 4, 5, 6},
    "every day": {0, 1, 2, 3, 4, 5, 6},
    "weekdays": {0, 1, 2, 3, 4},
    "weekday": {0, 1, 2, 3, 4},
    "weekends": {5, 6},
    "weekend": {5, 6},
}
_DAY_ABBR = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
_TIME_RE = re.compile(r"\A\d{1,2}:\d{2}\Z")


def parse_schedule(spec: str) -> tuple[set[int], tuple[int, int]]:
    """Parse a schedule spec into (days-of-week as {0=Mon..6=Sun}, (hour, minute)).

    Accepted forms: 'daily 08:00', 'weekdays 08:00', 'weekends 09:30', or a
    comma-separated list of day abbreviations plus a 24-hour time, e.g.
    'mon,wed,fri 18:30'. Raises ValueError with a user-facing message for
    anything else — the caller (ScheduleTaskSkill) surfaces that directly
    rather than silently storing a spec nothing can ever fire.
    """
    spec = (spec or "").strip().lower()
    if not spec:
        raise ValueError("I need a schedule, like 'daily 08:00' or 'weekdays 18:30'.")
    # Collapse runs of whitespace so a stray double space doesn't leave a
    # trailing space glued onto the day part (rpartition splits on exactly
    # one space character, not a whitespace run).
    spec = re.sub(r"\s+", " ", spec)

    day_part, sep, time_part = spec.rpartition(" ")
    if not sep:
        raise ValueError("A schedule needs a day part and a time, like 'daily 08:00'.")

    if not _TIME_RE.match(time_part):
        raise ValueError("Time must be 24-hour HH:MM, e.g. '08:00' or '18:30'.")
    hour, minute = (int(x) for x in time_part.split(":"))
    if not (0 <= hour < 24 and 0 <= minute < 60):
        raise ValueError("Time must be a valid 24-hour HH:MM.")

    if day_part in _DAY_ALIASES:
        days = set(_DAY_ALIASES[day_part])
    else:
        days = set()
        for token in day_part.split(","):
            token = token.strip()[:3]
            if token not in _DAY_ABBR:
                raise ValueError(
                    f"I don't recognise the day '{token}'. Use mon/tue/wed/thu/fri/sat/sun, "
                    "or 'daily'/'weekdays'/'weekends'."
                )
            days.add(_DAY_ABBR[token])

    return days, (hour, minute)


def next_fire_at(days: set[int], time_of_day: tuple[int, int], after: float) -> float:
    """The earliest epoch-seconds timestamp strictly after `after` that
    matches the schedule. `days` must be non-empty (parse_schedule guarantees
    this) — a week always contains at least one matching day."""
    hour, minute = time_of_day
    after_dt = datetime.fromtimestamp(after)
    for offset in range(8):
        candidate_date = after_dt.date() + timedelta(days=offset)
        if candidate_date.weekday() not in days:
            continue
        candidate = datetime.combine(candidate_date, dt_time(hour, minute))
        if candidate.timestamp() > after:
            return candidate.timestamp()
    raise ValueError("Schedule matches no day of the week.")  # unreachable: days is non-empty


class TaskScheduler:
    """Polls scheduled_tasks and runs the ones that have come due.

    Distinct from ReminderScheduler: instead of firing a one-off notification,
    each run calls `run_task(prompt)` — a full brain turn, with every tool the
    prompt needs available, same as if the user had typed it. A task that
    fires while the process is down is not caught up on restart the way a
    reminder is — it simply resumes from "next slot after now", so reopening
    Jarvis after a few days off doesn't fire a backlog of stale briefings.
    """

    def __init__(self, store, run_task, poll_seconds: float = POLL_SECONDS):
        self.store = store
        self.run_task = run_task  # callable(prompt: str) -> None
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

    def _loop(self) -> None:
        while not self._stop.wait(self.poll_seconds):
            try:
                self.run_due()
            except Exception as e:
                print(f"[scheduled task] scheduler error: {e}")

    def run_due(self) -> int:
        """Run every task currently due. Returns how many ran."""
        now = time.time()
        ran = 0
        for row in self.store.list_scheduled_tasks(enabled_only=True):
            try:
                days, time_of_day = parse_schedule(row["schedule"])
                anchor = row["last_run"] if row["last_run"] is not None else row["created"]
                fire_at = next_fire_at(days, time_of_day, anchor)
            except ValueError:
                continue  # a spec that can no longer be parsed is skipped, not fatal

            if fire_at > now:
                continue

            # Recorded as *now*, not fire_at, before running — so the next
            # computed slot is always strictly in the future regardless of
            # how late this run actually happened, and a task can never fire
            # twice for the same slot.
            self.store.mark_task_run(row["id"], now)
            try:
                self.run_task(row["prompt"])
            except Exception as e:
                print(f"[scheduled task] '{row['prompt']}' failed: {e}")
            ran += 1
        return ran
