"""Live system telemetry — the native equivalent of ODIN-HUD.md §7.1's
`telemetry` frame, sampled by a QThread and pushed to the GUI thread as one
`TelemetryFrame` per `frame_ready` signal.

All psutil work happens off the GUI thread, uniformly, even where a given
call is usually cheap: `psutil.disk_usage()` can block for real time against
a sleeping, removable, or network drive, and this HUD is meant to run 24/7
(§10) without ever freezing over it. Mirrors the QThread idiom already used
by VoiceListenWorker/BrainWorker in ui/workers.py.
"""
from __future__ import annotations

import socket
import sys
import threading
import time
from dataclasses import dataclass, field

import psutil
from PyQt6.QtCore import QThread, pyqtSignal

import config

IS_WINDOWS = sys.platform == "win32"
MAX_CORE_GROUPS = 16
TOP_PROCESS_COUNT = 3

# nvmlInit() hands back a process-wide handle, not a per-worker one.
# Calling it again from a second TelemetryWorker (e.g. a second
# OdinHudWindow built in the same process — every HUD test does this)
# without a matching nvmlShutdown() was reliably crashing the whole
# interpreter with a native access violation rather than raising a
# catchable Python exception. Guarding init at module scope keeps it to
# at most one real call per process, no matter how many workers get built.
_nvml_ready = False
_nvml_missing = False


@dataclass
class CpuSample:
    percent: float
    per_core: list[float]
    freq_mhz: float | None
    processes: int
    top: list[tuple[str, float]] = field(default_factory=list)


@dataclass
class MemSample:
    used_gb: float
    total_gb: float
    percent: float
    swap_percent: float


@dataclass
class DiskSample:
    mount: str
    used_gb: float
    total_gb: float
    percent: float


@dataclass
class DiskIoSample:
    read_mbs: float
    write_mbs: float


@dataclass
class NetSample:
    up_kbs: float
    down_kbs: float
    total_up_gb: float
    total_down_gb: float
    ip: str | None


@dataclass
class BatterySample:
    percent: float | None
    plugged: bool | None


@dataclass
class ThermalSample:
    cpu_c: float | None
    gpu_c: float | None
    gpu_load: float | None
    gpu_vram_percent: float | None
    fan_rpm: float | None


@dataclass
class TelemetryFrame:
    ts: float
    cpu: CpuSample
    mem: MemSample
    disks: list[DiskSample]
    disk_io: DiskIoSample
    net: NetSample
    battery: BatterySample
    thermals: ThermalSample
    uptime_sec: float


def _aggregate_cores(per_core: list[float], max_groups: int = MAX_CORE_GROUPS) -> list[float]:
    """>16 cores bucket-average into 16 contiguous groups (§6.2); at or
    below that, pass through unchanged."""
    n = len(per_core)
    if n <= max_groups:
        return per_core
    groups = []
    for i in range(max_groups):
        start, end = (i * n) // max_groups, ((i + 1) * n) // max_groups
        chunk = per_core[start:end] or [0.0]
        groups.append(round(sum(chunk) / len(chunk), 1))
    return groups


def _local_ip() -> str | None:
    """The "UDP connect to a public address" trick — it never actually
    sends a packet, just asks the OS which local interface would carry it,
    which is enough to report the LAN IP without an external request."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        return None
    finally:
        sock.close()


class TelemetryWorker(QThread):
    """One continuous sampling loop, off the GUI thread. `.stop()` then
    `.wait()` from the GUI thread to shut it down cleanly."""

    frame_ready = pyqtSignal(object)  # TelemetryFrame

    def __init__(self, parent=None):
        super().__init__(parent)
        self._stop_event = threading.Event()

        # Rate-diffed counters: cumulative psutil counters, not rates, need
        # the previous sample plus elapsed time to become one (§7.4).
        self._prev_net = None
        self._prev_net_ts: float | None = None
        self._prev_disk_io = None
        self._prev_disk_io_ts: float | None = None

        # psutil.Process.cpu_percent(None) only reports a real number on the
        # *second* call against the *same* object — process_iter() hands
        # back a fresh object every call, so these must be persisted here,
        # not re-created each tick.
        self._proc_handles: dict[int, psutil.Process] = {}

        # disk_usage() is polled on its own slower cadence and cached
        # between polls (§7.4).
        self._disk_cache: list[DiskSample] = []
        self._disk_cache_ts: float = 0.0

        # Lazily-imported optional sensor backends; None until first probed.
        # (NVML's ready/missing flags are process-wide module state, not
        # per-instance — see the comment by _nvml_ready above.)
        self._wmi_lhm = None
        self._wmi_missing = False

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        # Prime the non-blocking cpu_percent baseline before the loop starts
        # emitting, so the very first frame isn't a meaningless 0.0.
        psutil.cpu_percent(interval=None)
        psutil.cpu_percent(interval=None, percpu=True)
        interval = config.HUD_TELEMETRY_INTERVAL_MS / 1000
        while not self._stop_event.is_set():
            try:
                frame = self._collect()
            except Exception:  # noqa: BLE001 - one bad tick must not kill a 24/7 worker
                self._stop_event.wait(interval)
                continue
            self.frame_ready.emit(frame)
            self._stop_event.wait(interval)

    # -- collection ----------------------------------------------------

    def _collect(self) -> TelemetryFrame:
        now = time.time()
        processes, top = self._collect_processes()
        freq = psutil.cpu_freq()
        cpu = CpuSample(
            percent=psutil.cpu_percent(interval=None),
            per_core=_aggregate_cores(psutil.cpu_percent(interval=None, percpu=True)),
            freq_mhz=round(freq.current) if freq else None,
            processes=processes,
            top=top,
        )

        vm = psutil.virtual_memory()
        swap = psutil.swap_memory()
        mem = MemSample(
            used_gb=round(vm.used / 1024**3, 1),
            total_gb=round(vm.total / 1024**3, 1),
            percent=vm.percent,
            swap_percent=swap.percent,
        )

        return TelemetryFrame(
            ts=now,
            cpu=cpu,
            mem=mem,
            disks=self._disks(now),
            disk_io=self._disk_io_sample(now),
            net=self._net_sample(now),
            battery=self._battery_sample(),
            thermals=self._thermals(),
            uptime_sec=now - psutil.boot_time(),
        )

    def _collect_processes(self) -> tuple[int, list[tuple[str, float]]]:
        current = {p.info["pid"]: p for p in psutil.process_iter(["pid"])}

        for pid, proc in current.items():
            if pid not in self._proc_handles:
                try:
                    proc.cpu_percent(None)  # prime; this call's own value is discarded
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
                self._proc_handles[pid] = proc

        for pid in list(self._proc_handles):
            if pid not in current:
                del self._proc_handles[pid]

        top: list[tuple[str, float]] = []
        for pid, proc in list(self._proc_handles.items()):
            try:
                top.append((proc.name(), proc.cpu_percent(None)))
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                del self._proc_handles[pid]
        top.sort(key=lambda item: item[1], reverse=True)
        return len(current), top[:TOP_PROCESS_COUNT]

    def _disks(self, now: float) -> list[DiskSample]:
        if self._disk_cache_ts and now - self._disk_cache_ts < config.HUD_DISK_POLL_SECONDS:
            return self._disk_cache

        samples = []
        for part in psutil.disk_partitions(all=False):
            if IS_WINDOWS and ("cdrom" in part.opts or not part.fstype):
                continue
            try:
                usage = psutil.disk_usage(part.mountpoint)
            except (PermissionError, OSError):
                continue
            samples.append(DiskSample(
                mount=part.mountpoint.rstrip("\\/"),
                used_gb=round(usage.used / 1024**3, 1),
                total_gb=round(usage.total / 1024**3, 1),
                percent=usage.percent,
            ))
        self._disk_cache, self._disk_cache_ts = samples, now
        return samples

    def _disk_io_sample(self, now: float) -> DiskIoSample:
        counters = psutil.disk_io_counters()
        if counters is None:
            return DiskIoSample(read_mbs=0.0, write_mbs=0.0)

        read_mbs = write_mbs = 0.0
        if self._prev_disk_io is not None and self._prev_disk_io_ts is not None:
            elapsed = max(now - self._prev_disk_io_ts, 1e-6)
            read_mbs = max((counters.read_bytes - self._prev_disk_io.read_bytes) / 1024**2 / elapsed, 0.0)
            write_mbs = max((counters.write_bytes - self._prev_disk_io.write_bytes) / 1024**2 / elapsed, 0.0)
        self._prev_disk_io, self._prev_disk_io_ts = counters, now
        return DiskIoSample(read_mbs=round(read_mbs, 2), write_mbs=round(write_mbs, 2))

    def _net_sample(self, now: float) -> NetSample:
        counters = psutil.net_io_counters()
        up_kbs = down_kbs = 0.0
        if self._prev_net is not None and self._prev_net_ts is not None:
            elapsed = max(now - self._prev_net_ts, 1e-6)
            up_kbs = max((counters.bytes_sent - self._prev_net.bytes_sent) / 1024 / elapsed, 0.0)
            down_kbs = max((counters.bytes_recv - self._prev_net.bytes_recv) / 1024 / elapsed, 0.0)
        self._prev_net, self._prev_net_ts = counters, now
        return NetSample(
            up_kbs=round(up_kbs, 1),
            down_kbs=round(down_kbs, 1),
            total_up_gb=round(counters.bytes_sent / 1024**3, 2),
            total_down_gb=round(counters.bytes_recv / 1024**3, 2),
            ip=_local_ip(),
        )

    @staticmethod
    def _battery_sample() -> BatterySample:
        try:
            battery = psutil.sensors_battery()
        except (AttributeError, NotImplementedError):
            battery = None
        if battery is None:
            return BatterySample(percent=None, plugged=None)
        return BatterySample(percent=round(battery.percent, 1), plugged=bool(battery.power_plugged))

    def _thermals(self) -> ThermalSample:
        """§10: never fabricate. Every field stays None — rendering `--` —
        unless the matching optional backend (requirements.txt) is both
        installed and actually reachable."""
        global _nvml_ready, _nvml_missing
        cpu_c = fan_rpm = None
        gpu_c = gpu_load = gpu_vram_percent = None

        if not self._wmi_missing:
            try:
                import wmi  # optional: requirements.txt

                if self._wmi_lhm is None:
                    self._wmi_lhm = wmi.WMI(namespace="root\\LibreHardwareMonitor")
                for sensor in self._wmi_lhm.Sensor():
                    if sensor.SensorType == "Temperature" and "CPU" in sensor.Name and cpu_c is None:
                        cpu_c = sensor.Value
                    elif sensor.SensorType == "Fan" and fan_rpm is None:
                        fan_rpm = sensor.Value
            except Exception:  # noqa: BLE001 - not installed, or LHM not running; both are "--"
                self._wmi_missing = True

        if not _nvml_missing:
            try:
                import pynvml  # optional: requirements.txt (nvidia-ml-py)

                if not _nvml_ready:
                    pynvml.nvmlInit()
                    _nvml_ready = True
                handle = pynvml.nvmlDeviceGetHandleByIndex(0)
                gpu_c = pynvml.nvmlDeviceGetTemperature(handle, pynvml.NVML_TEMPERATURE_GPU)
                gpu_load = pynvml.nvmlDeviceGetUtilizationRates(handle).gpu
                mem = pynvml.nvmlDeviceGetMemoryInfo(handle)
                gpu_vram_percent = round(mem.used / mem.total * 100, 1)
            except Exception:  # noqa: BLE001 - not installed, or no NVIDIA GPU; both are "--"
                _nvml_missing = True

        return ThermalSample(cpu_c, gpu_c, gpu_load, gpu_vram_percent, fan_rpm)
