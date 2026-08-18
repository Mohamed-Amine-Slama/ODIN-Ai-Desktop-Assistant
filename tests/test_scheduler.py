"""Tests for schedule-spec parsing and the recurring TaskScheduler
(core/scheduler.py) — the machinery behind the schedule_task skill."""
import datetime

import pytest

from core.scheduler import TaskScheduler, next_fire_at, parse_schedule

# Fixed reference points, not time.time()-derived, so these never depend on
# when the suite happens to run. Deliberately not epoch 0 / negative — Windows'
# datetime.fromtimestamp() can raise for a local time that predates 1970.
_PAST = datetime.datetime(2000, 1, 1).timestamp()
_FUTURE = datetime.datetime(2099, 1, 1).timestamp()


# -- parse_schedule -----------------------------------------------------------

def test_parse_daily():
    days, (h, m) = parse_schedule("daily 08:00")
    assert days == {0, 1, 2, 3, 4, 5, 6}
    assert (h, m) == (8, 0)


def test_parse_weekdays():
    days, _ = parse_schedule("weekdays 18:30")
    assert days == {0, 1, 2, 3, 4}


def test_parse_weekends():
    days, _ = parse_schedule("weekends 09:00")
    assert days == {5, 6}


def test_parse_explicit_day_list():
    days, (h, m) = parse_schedule("mon,wed,fri 18:30")
    assert days == {0, 2, 4}
    assert (h, m) == (18, 30)


def test_parse_is_case_insensitive_and_trims_whitespace():
    days, (h, m) = parse_schedule("  DAILY  08:00  ")
    assert days == {0, 1, 2, 3, 4, 5, 6}
    assert (h, m) == (8, 0)


@pytest.mark.parametrize(
    "spec",
    [
        "",
        "daily",
        "08:00",
        "daily 8am",
        "daily 25:00",
        "daily 08:60",
        "someday 08:00",
        "xyz,mon 08:00",
    ],
)
def test_parse_rejects_bad_specs(spec):
    with pytest.raises(ValueError):
        parse_schedule(spec)


# -- next_fire_at -------------------------------------------------------------

def test_next_fire_at_same_day_if_time_not_yet_passed():
    anchor = datetime.datetime(2026, 8, 10, 7, 0)  # a Monday, 7am
    assert anchor.weekday() == 0

    fire = next_fire_at({0, 1, 2, 3, 4, 5, 6}, (8, 0), anchor.timestamp())

    fired = datetime.datetime.fromtimestamp(fire)
    assert fired.date() == anchor.date()
    assert (fired.hour, fired.minute) == (8, 0)


def test_next_fire_at_rolls_to_next_matching_day():
    anchor = datetime.datetime(2026, 8, 10, 9, 0)  # Monday, already past 8am

    fire = next_fire_at({0}, (8, 0), anchor.timestamp())  # Mondays only

    fired = datetime.datetime.fromtimestamp(fire)
    assert fired.date() == anchor.date() + datetime.timedelta(days=7)


def test_next_fire_at_is_strictly_after_the_anchor():
    """An anchor sitting exactly on the fire time must roll to the *next*
    matching slot, not return itself — otherwise a task could refire the
    slot it just fired for."""
    anchor = datetime.datetime(2026, 8, 10, 8, 0)

    fire = next_fire_at({0, 1, 2, 3, 4, 5, 6}, (8, 0), anchor.timestamp())

    assert fire > anchor.timestamp()


# -- TaskScheduler -------------------------------------------------------------

class _FakeStore:
    def __init__(self, rows):
        self._rows = rows
        self.marked: list[tuple[int, float]] = []

    def list_scheduled_tasks(self, enabled_only=False):
        return [r for r in self._rows if not enabled_only or r["enabled"]]

    def mark_task_run(self, task_id, ts):
        self.marked.append((task_id, ts))
        for r in self._rows:
            if r["id"] == task_id:
                r["last_run"] = ts


def _row(task_id, prompt, schedule, last_run=None, enabled=1, created=_PAST):
    return {
        "id": task_id,
        "prompt": prompt,
        "schedule": schedule,
        "last_run": last_run,
        "enabled": enabled,
        "created": created,
    }


def test_run_due_fires_a_due_task_and_marks_it_run():
    store = _FakeStore([_row(1, "give me a briefing", "daily 00:00", created=_PAST)])
    ran = []
    scheduler = TaskScheduler(store, ran.append)

    count = scheduler.run_due()

    assert count == 1
    assert ran == ["give me a briefing"]
    assert store.marked and store.marked[0][0] == 1


def test_run_due_skips_a_task_not_yet_due():
    store = _FakeStore([_row(1, "later", "daily 00:00", created=_FUTURE)])
    ran = []
    scheduler = TaskScheduler(store, ran.append)

    assert scheduler.run_due() == 0
    assert ran == []


def test_run_due_does_not_refire_the_same_slot_twice():
    store = _FakeStore([_row(1, "briefing", "daily 00:00", created=_PAST)])
    ran = []
    scheduler = TaskScheduler(store, ran.append)

    scheduler.run_due()
    second_pass = scheduler.run_due()

    assert ran == ["briefing"]
    assert second_pass == 0, "last_run was just set to 'now', so the next slot is in the future"


def test_run_due_skips_disabled_tasks():
    store = _FakeStore([_row(1, "off", "daily 00:00", created=_PAST, enabled=0)])
    scheduler = TaskScheduler(store, lambda p: None)

    assert scheduler.run_due() == 0


def test_run_due_skips_an_unparsable_schedule_without_raising():
    store = _FakeStore([_row(1, "broken", "not a valid schedule", created=_PAST)])
    scheduler = TaskScheduler(store, lambda p: None)

    assert scheduler.run_due() == 0


def test_a_failing_task_does_not_stop_the_others_from_running():
    store = _FakeStore(
        [
            _row(1, "boom", "daily 00:00", created=_PAST),
            _row(2, "fine", "daily 00:00", created=_PAST),
        ]
    )
    ran = []

    def run_task(prompt):
        if prompt == "boom":
            raise RuntimeError("nope")
        ran.append(prompt)

    scheduler = TaskScheduler(store, run_task)
    count = scheduler.run_due()

    assert count == 2, "both are counted as attempted even though one raised"
    assert ran == ["fine"]
