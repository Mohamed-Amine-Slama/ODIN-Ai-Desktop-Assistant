"""ui/hud/telemetry.py — TelemetryWorker._collect(), tested directly
(not through the QThread loop) with psutil monkeypatched, per
ODIN-HUD.md §10's "never fabricate" rule and §7.4's rate-diffing notes.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock

from ui.hud.telemetry import TelemetryWorker, _aggregate_cores


def _counters(sent=0, recv=0):
    return SimpleNamespace(bytes_sent=sent, bytes_recv=recv)


def _disk_io(read=0, write=0):
    return SimpleNamespace(read_bytes=read, write_bytes=write)


def test_aggregate_cores_passes_through_at_or_below_16():
    values = [10.0] * 16
    assert _aggregate_cores(values) == values


def test_aggregate_cores_buckets_above_16():
    values = list(range(32))  # 0..31, two per bucket
    result = _aggregate_cores(values, max_groups=16)
    assert len(result) == 16
    assert result[0] == 0.5  # mean of [0, 1]


def test_net_sample_diffs_cumulative_counters(monkeypatch):
    monkeypatch.setattr("ui.hud.telemetry.psutil.net_io_counters", lambda: _counters(1024 * 10, 1024 * 20))
    monkeypatch.setattr("ui.hud.telemetry._local_ip", lambda: "10.0.0.5")
    worker = TelemetryWorker()

    first = worker._net_sample(now=100.0)
    assert first.up_kbs == 0.0 and first.down_kbs == 0.0  # nothing to diff against yet

    monkeypatch.setattr("ui.hud.telemetry.psutil.net_io_counters", lambda: _counters(1024 * 20, 1024 * 40))
    second = worker._net_sample(now=101.0)  # 1 second later, 10 KB up / 20 KB down
    assert second.up_kbs == 10.0
    assert second.down_kbs == 20.0
    assert second.ip == "10.0.0.5"


def test_disk_io_sample_diffs_and_never_goes_negative(monkeypatch):
    monkeypatch.setattr("ui.hud.telemetry.psutil.disk_io_counters", lambda: _disk_io(1024**2 * 5, 1024**2 * 2))
    worker = TelemetryWorker()
    worker._disk_io_sample(now=0.0)

    # A counter that appears to go backward (rare, but psutil doesn't
    # guarantee monotonicity across some virtualized disks) must clamp to 0,
    # not report a fabricated negative rate.
    monkeypatch.setattr("ui.hud.telemetry.psutil.disk_io_counters", lambda: _disk_io(1024**2 * 4, 1024**2 * 1))
    sample = worker._disk_io_sample(now=1.0)
    assert sample.read_mbs == 0.0
    assert sample.write_mbs == 0.0


def test_disk_io_sample_handles_no_counters(monkeypatch):
    monkeypatch.setattr("ui.hud.telemetry.psutil.disk_io_counters", lambda: None)
    worker = TelemetryWorker()
    sample = worker._disk_io_sample(now=0.0)
    assert sample.read_mbs == 0.0 and sample.write_mbs == 0.0


def test_process_cpu_percent_uses_the_same_persisted_handle_across_ticks(monkeypatch):
    """psutil.Process.cpu_percent(None) only reports a real number when
    called repeatedly on the *same* Process object. process_iter() hands
    back a fresh object every call by default, which would permanently
    report 0% — the collector must persist handles by pid instead."""
    proc = MagicMock()
    proc.info = {"pid": 42}
    proc.cpu_percent.return_value = 0.0
    proc.name.return_value = "python.exe"

    monkeypatch.setattr("ui.hud.telemetry.psutil.process_iter", lambda fields: iter([proc]))
    worker = TelemetryWorker()

    worker._collect_processes()
    calls_after_first_tick = proc.cpu_percent.call_count
    assert calls_after_first_tick >= 1
    assert worker._proc_handles[42] is proc  # the exact same object, not a new one

    proc.cpu_percent.return_value = 12.5
    _, top = worker._collect_processes()
    assert proc.cpu_percent.call_count > calls_after_first_tick  # called again, same object
    assert top == [("python.exe", 12.5)]


def test_process_that_exits_is_dropped(monkeypatch):
    proc = MagicMock()
    proc.info = {"pid": 42}
    proc.cpu_percent.return_value = 0.0
    monkeypatch.setattr("ui.hud.telemetry.psutil.process_iter", lambda fields: iter([proc]))
    worker = TelemetryWorker()
    worker._collect_processes()
    assert 42 in worker._proc_handles

    monkeypatch.setattr("ui.hud.telemetry.psutil.process_iter", lambda fields: iter([]))
    worker._collect_processes()
    assert 42 not in worker._proc_handles


def test_disk_usage_failure_is_skipped_not_fatal(monkeypatch):
    part = SimpleNamespace(mountpoint="Z:\\", opts="fixed", fstype="NTFS")
    monkeypatch.setattr("ui.hud.telemetry.psutil.disk_partitions", lambda all: [part])

    def _raise(_mount):
        raise OSError("device not ready")

    monkeypatch.setattr("ui.hud.telemetry.psutil.disk_usage", _raise)
    worker = TelemetryWorker()
    disks = worker._disks(now=0.0)
    assert disks == []  # skipped, not a crash — and never a fabricated reading


def test_thermals_default_to_none_when_backends_are_unavailable(monkeypatch):
    """Every thermal field must come back None (rendered as "--"), never a
    fabricated number (ODIN-HUD.md §10), when neither optional sensor
    backend is available — forced here via sys.modules rather than relying
    on the dev/CI machine actually lacking pynvml/wmi (this one has both,
    plus a real GPU, once requirements.txt is fully installed)."""
    import sys

    monkeypatch.setitem(sys.modules, "pynvml", None)
    monkeypatch.setitem(sys.modules, "wmi", None)

    worker = TelemetryWorker()
    thermals = worker._thermals()
    assert thermals.cpu_c is None
    assert thermals.gpu_c is None
    assert thermals.gpu_load is None
    assert thermals.gpu_vram_percent is None
    assert thermals.fan_rpm is None
