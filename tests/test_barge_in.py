"""Tests for core/barge_in.py's sustained-energy interrupt watcher.

No real audio hardware or numpy dependency: the microphone and rms() are both
faked, so these run without requirements-voice.txt installed.
"""
import queue
import time

from core.barge_in import BargeInWatcher, make_watcher


class _FakeMic:
    def __init__(self):
        self.q: "queue.Queue" = queue.Queue()
        self._np = None  # unused; rms() is faked below
        self.unsubscribed = False

    def subscribe(self):
        return self.q

    def unsubscribe(self, q):  # noqa: ARG002
        self.unsubscribed = True


def _watcher(monkeypatch, levels=None, threshold=0.05):
    """levels maps a sentinel block value to the RMS level a faked rms()
    should report for it, so no real audio math is needed."""
    levels = levels or {}
    monkeypatch.setattr("core.barge_in.rms", lambda block, np: levels.get(block, 0.0))  # noqa: ARG005
    mic = _FakeMic()
    fired = []
    watcher = BargeInWatcher(mic, on_interrupt=lambda: fired.append(True), threshold=threshold)
    return mic, watcher, fired


def _wait_until(predicate, timeout=2.0):
    deadline = time.time() + timeout
    while not predicate() and time.time() < deadline:
        time.sleep(0.02)


def test_sustained_loud_blocks_trigger_interrupt(monkeypatch):
    mic, watcher, fired = _watcher(monkeypatch, {"loud": 0.5}, threshold=0.05)
    watcher.start()
    try:
        for _ in range(5):
            mic.q.put("loud")
        _wait_until(lambda: fired)
        assert fired == [True]
    finally:
        watcher.stop()


def test_a_single_spike_does_not_trigger(monkeypatch):
    """A cough or a chair creak must not cut Jarvis off."""
    mic, watcher, fired = _watcher(monkeypatch, {"loud": 0.5, "quiet": 0.0}, threshold=0.05)
    watcher.start()
    try:
        mic.q.put("loud")
        mic.q.put("quiet")
        mic.q.put("quiet")
        time.sleep(0.3)
        assert fired == []
    finally:
        watcher.stop()


def test_quiet_blocks_never_trigger(monkeypatch):
    mic, watcher, fired = _watcher(monkeypatch, {"quiet": 0.0}, threshold=0.05)
    watcher.start()
    try:
        for _ in range(10):
            mic.q.put("quiet")
        time.sleep(0.2)
        assert fired == []
    finally:
        watcher.stop()


def test_stop_unsubscribes_and_ends_the_thread(monkeypatch):
    mic, watcher, _fired = _watcher(monkeypatch)
    watcher.start()
    watcher.stop()
    assert mic.unsubscribed is True
    assert watcher._thread is None


def test_start_is_a_noop_while_already_running(monkeypatch):
    mic, watcher, _fired = _watcher(monkeypatch)
    watcher.start()
    try:
        first_thread = watcher._thread
        watcher.start()
        assert watcher._thread is first_thread
    finally:
        watcher.stop()


def test_stop_before_any_start_does_not_raise(monkeypatch):
    _mic, watcher, _fired = _watcher(monkeypatch)
    watcher.stop()  # must not raise


def test_make_watcher_returns_none_without_a_microphone():
    assert make_watcher(None, on_interrupt=lambda: None) is None


def test_make_watcher_builds_a_working_watcher(monkeypatch):
    monkeypatch.setattr("core.barge_in.rms", lambda block, np: 0.0)  # noqa: ARG005
    mic = _FakeMic()
    watcher = make_watcher(mic, on_interrupt=lambda: None)
    assert isinstance(watcher, BargeInWatcher)
