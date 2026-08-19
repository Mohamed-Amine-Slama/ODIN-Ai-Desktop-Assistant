import math, random, time
from ui.hud.telemetry import (
    BatterySample, CpuSample, DiskIoSample, DiskSample, MemSample, NetSample,
    NicSample, TelemetryFrame, ThermalSample,
)
def frames(count):
    rng = random.Random(5)
    for i in range(count):
        t = i / 6.0
        yield TelemetryFrame(
            ts=time.time() + i,
            cpu=CpuSample(percent=34 + 26 * math.sin(t), per_core=[30.0] * 16, freq_mhz=2699,
                          processes=386, top=[("chrome.exe", 18.4), ("python.exe", 9.1), ("Code.exe", 6.7)],
                          user_pct=22.0, system_pct=12.0, ctx_per_sec=8600.0),
            mem=MemSample(used_gb=21.5, total_gb=32.0, percent=67 + 6 * math.sin(t), swap_percent=18,
                          available_gb=10.5, top=[("chrome.exe", 4.2), ("Code.exe", 1.8)]),
            disks=[DiskSample(mount="C:", used_gb=447, total_gb=460, percent=97.2)],
            disk_io=DiskIoSample(read_mbs=5.9, write_mbs=10.3),
            net=NetSample(up_kbs=939, down_kbs=2464, total_up_gb=12.4, total_down_gb=88.1,
                          ip="192.168.1.129",
                          nics=[NicSample("Wi-Fi", 900, 2400), NicSample("Ethernet", 38.7, 63.7)]),
            battery=BatterySample(percent=76, plugged=False, secs_left=4500),
            thermals=ThermalSample(cpu_c=63, gpu_c=60, gpu_load=11, gpu_vram_percent=44, fan_rpm=1915),
            uptime_sec=98_400.0)
