"""System metrics sampled in a background thread.

Every value lives under a dotted key (``cpu.util``, ``gpu.temp``...) listed in
:data:`METRICS`, so layouts can bind any widget to any metric. Numeric metrics
keep a short history for graphs.
"""

from __future__ import annotations

import collections
import glob
import logging
import os
import platform
import shutil
import socket
import subprocess
import threading
import time
from dataclasses import dataclass

import psutil

log = logging.getLogger(__name__)

HISTORY = 300  # samples kept per metric


@dataclass(frozen=True)
class MetricDef:
    key: str
    label: str
    unit: str = ""
    #: percent, temp, bytes, rate, freq, power, number, duration, text
    kind: str = "number"
    #: natural full-scale value for gauges and bars (None = auto)
    max: float | None = None


def _defs(*rows: tuple) -> dict[str, MetricDef]:
    return {r[0]: MetricDef(*r) for r in rows}


METRICS: dict[str, MetricDef] = _defs(
    ("cpu.util", "CPU usage", "%", "percent", 100),
    ("cpu.temp", "CPU temperature", "°C", "temp", 100),
    ("cpu.freq", "CPU clock (fastest core)", "GHz", "freq", None),
    ("cpu.load1", "Load average (1 min)", "", "number", None),
    ("cpu.load5", "Load average (5 min)", "", "number", None),
    ("cpu.threads", "CPU threads", "", "number", None),
    ("cpu.name", "CPU model", "", "text"),
    ("gpu.util", "GPU usage", "%", "percent", 100),
    ("gpu.temp", "GPU temperature", "°C", "temp", 100),
    ("gpu.power", "GPU power", "W", "power", None),
    ("gpu.clock", "GPU clock", "GHz", "freq", None),
    ("gpu.fan", "GPU fan", "%", "percent", 100),
    ("gpu.vram_percent", "VRAM usage", "%", "percent", 100),
    ("gpu.vram_used", "VRAM used", "B", "bytes", None),
    ("gpu.vram_total", "VRAM total", "B", "bytes", None),
    ("gpu.name", "GPU model", "", "text"),
    ("mem.percent", "Memory usage", "%", "percent", 100),
    ("mem.used", "Memory used", "B", "bytes", None),
    ("mem.total", "Memory total", "B", "bytes", None),
    ("swap.percent", "Swap usage", "%", "percent", 100),
    ("swap.used", "Swap used", "B", "bytes", None),
    ("swap.total", "Swap total", "B", "bytes", None),
    ("disk.percent", "Disk usage", "%", "percent", 100),
    ("disk.used", "Disk used", "B", "bytes", None),
    ("disk.total", "Disk total", "B", "bytes", None),
    ("disk.read", "Disk read", "B/s", "rate", None),
    ("disk.write", "Disk write", "B/s", "rate", None),
    ("disk.temp", "Disk temperature", "°C", "temp", 100),
    ("net.down", "Network download", "B/s", "rate", None),
    ("net.up", "Network upload", "B/s", "rate", None),
    ("battery.percent", "Battery", "%", "percent", 100),
    ("host.name", "Hostname", "", "text"),
    ("host.os", "Operating system", "", "text"),
    ("host.kernel", "Kernel", "", "text"),
    ("host.uptime", "Uptime", "s", "duration", None),
    ("host.processes", "Processes", "", "number", None),
    ("media.title", "Now playing: title", "", "text"),
    ("media.artist", "Now playing: artist", "", "text"),
    ("media.status", "Now playing: status", "", "text"),
)

NUMERIC_METRICS = [k for k, d in METRICS.items() if d.kind != "text"]


def _read_float(path: str | None, scale: float = 1.0) -> float | None:
    if not path:
        return None
    try:
        with open(path) as f:
            return float(f.read().strip()) / scale
    except (OSError, ValueError):
        return None


def _hwmon_by_name(*names: str) -> str | None:
    for d in sorted(glob.glob("/sys/class/hwmon/hwmon*")):
        try:
            with open(os.path.join(d, "name")) as f:
                if f.read().strip() in names:
                    return d
        except OSError:
            continue
    return None


def _cpu_name() -> str:
    try:
        with open("/proc/cpuinfo") as f:
            for line in f:
                if line.startswith("model name"):
                    name = line.split(":", 1)[1].strip()
                    for junk in ("(R)", "(TM)", " CPU", " Processor", "-Core"):
                        name = name.replace(junk, "")
                    parts = name.split("@")[0].split()
                    if parts and parts[-1].isdigit():  # "Ryzen 9 9950X 16" -> drop the core count
                        parts = parts[:-1]
                    return " ".join(parts)
    except OSError:
        pass
    return platform.processor() or "CPU"


def _os_name() -> str:
    try:
        with open("/etc/os-release") as f:
            for line in f:
                if line.startswith("PRETTY_NAME="):
                    return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return platform.system()


class _CpuTemp:
    """CPU package temperature from hwmon: AMD k10temp/zenpower, Intel coretemp, ARM thermal."""

    def __init__(self) -> None:
        self.path: str | None = None
        d = _hwmon_by_name("k10temp", "zenpower", "coretemp", "cpu_thermal", "soc_thermal")
        if not d:
            return
        labels = {}
        for lab in glob.glob(os.path.join(d, "temp*_label")):
            try:
                with open(lab) as f:
                    labels[f.read().strip()] = lab.replace("_label", "_input")
            except OSError:
                pass
        for preferred in ("Tctl", "Tdie", "Package id 0"):
            if preferred in labels:
                self.path = labels[preferred]
                return
        inputs = sorted(glob.glob(os.path.join(d, "temp*_input")))
        self.path = inputs[0] if inputs else None

    def read(self) -> float | None:
        return _read_float(self.path, 1000)


class _Gpu:
    """NVIDIA through NVML (nvidia-ml-py) or nvidia-smi; AMD through amdgpu sysfs."""

    def __init__(self) -> None:
        self.kind: str | None = None
        self._nvml = None
        self._handle = None
        self._amd: str | None = None
        try:
            import pynvml

            pynvml.nvmlInit()
            self._handle = pynvml.nvmlDeviceGetHandleByIndex(0)
            self._nvml = pynvml
            self.kind = "nvml"
            return
        except Exception:
            pass
        if shutil.which("nvidia-smi"):
            self.kind = "nvidia-smi"
            return
        for card in sorted(glob.glob("/sys/class/drm/card*/device")):
            if os.path.exists(os.path.join(card, "gpu_busy_percent")):
                total = _read_float(os.path.join(card, "mem_info_vram_total")) or 0
                if total > 1 << 30:  # skip iGPUs with a tiny carve-out
                    self._amd = card
                    self.kind = "amdgpu"
                    break

    def read(self) -> dict[str, float | str | None]:
        try:
            if self.kind == "nvml":
                return self._read_nvml()
            if self.kind == "nvidia-smi":
                return self._read_smi()
            if self.kind == "amdgpu":
                return self._read_amd()
        except Exception as e:  # a driver hiccup must not take the screen down
            log.debug("GPU read failed: %s", e)
        return {}

    def _read_nvml(self) -> dict:
        n, h = self._nvml, self._handle
        name = n.nvmlDeviceGetName(h)
        mem = n.nvmlDeviceGetMemoryInfo(h)
        out = {
            "gpu.name": name.decode() if isinstance(name, bytes) else name,
            "gpu.util": float(n.nvmlDeviceGetUtilizationRates(h).gpu),
            "gpu.temp": float(n.nvmlDeviceGetTemperature(h, n.NVML_TEMPERATURE_GPU)),
            "gpu.vram_used": float(mem.used),
            "gpu.vram_total": float(mem.total),
        }
        for key, fn in (
            ("gpu.power", lambda: n.nvmlDeviceGetPowerUsage(h) / 1000),
            ("gpu.clock", lambda: n.nvmlDeviceGetClockInfo(h, n.NVML_CLOCK_GRAPHICS) / 1000),
            ("gpu.fan", lambda: float(n.nvmlDeviceGetFanSpeed(h))),
        ):
            try:
                out[key] = fn()
            except Exception:
                pass
        return out

    def _read_smi(self) -> dict:
        q = "name,utilization.gpu,temperature.gpu,memory.used,memory.total,power.draw,clocks.gr,fan.speed"
        res = subprocess.run(
            ["nvidia-smi", f"--query-gpu={q}", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=3,
        ).stdout.splitlines()
        if not res:
            return {}
        f = [x.strip() for x in res[0].split(",")]

        def num(v: str) -> float | None:
            try:
                return float(v)
            except ValueError:
                return None

        clock = num(f[6])
        return {
            "gpu.name": f[0],
            "gpu.util": num(f[1]),
            "gpu.temp": num(f[2]),
            "gpu.vram_used": (num(f[3]) or 0) * 1024**2,
            "gpu.vram_total": (num(f[4]) or 0) * 1024**2,
            "gpu.power": num(f[5]),
            "gpu.clock": clock / 1000 if clock is not None else None,
            "gpu.fan": num(f[7]),
        }

    def _read_amd(self) -> dict:
        c = self._amd
        hw = (glob.glob(os.path.join(c, "hwmon", "hwmon*")) or [""])[0]
        sclk = _read_float(os.path.join(hw, "freq1_input"), 1e9)
        return {
            "gpu.name": "AMD Radeon",
            "gpu.util": _read_float(os.path.join(c, "gpu_busy_percent")),
            "gpu.temp": _read_float(os.path.join(hw, "temp1_input"), 1000),
            "gpu.vram_used": _read_float(os.path.join(c, "mem_info_vram_used")),
            "gpu.vram_total": _read_float(os.path.join(c, "mem_info_vram_total")),
            "gpu.power": _read_float(os.path.join(hw, "power1_average"), 1e6)
            or _read_float(os.path.join(hw, "power1_input"), 1e6),
            "gpu.clock": sclk,
        }


class _Media:
    """Now-playing info from any MPRIS player through playerctl, when installed."""

    def __init__(self) -> None:
        self.available = shutil.which("playerctl") is not None
        self._next = 0.0
        self._cache: dict[str, str] = {}

    def read(self) -> dict[str, str]:
        if not self.available or time.monotonic() < self._next:
            return self._cache
        self._next = time.monotonic() + 2
        try:
            out = subprocess.run(
                ["playerctl", "metadata", "--format", "{{status}}\t{{artist}}\t{{title}}"],
                capture_output=True, text=True, timeout=1,
            ).stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            out = ""
        status, artist, title = (out.split("\t") + ["", "", ""])[:3] if out else ("", "", "")
        self._cache = {"media.status": status, "media.artist": artist, "media.title": title}
        return self._cache


def _max_core_freq() -> float | None:
    """Fastest current core clock in GHz (psutil's average hides boost)."""
    best = None
    for p in glob.glob("/sys/devices/system/cpu/cpu[0-9]*/cpufreq/scaling_cur_freq"):
        v = _read_float(p, 1e6)
        if v is not None and (best is None or v > best):
            best = v
    return best


def _psutil_freq() -> float | None:
    f = psutil.cpu_freq()
    return f.current / 1000 if f and f.current else None


class Sensors:
    """Samples every metric each ``interval`` seconds in a daemon thread."""

    def __init__(self, interval: float = 1.0, disk_path: str = "/"):
        self.interval = interval
        self.disk_path = disk_path
        self._cpu_temp = _CpuTemp()
        d = _hwmon_by_name("nvme", "drivetemp")
        self._disk_temp_path = os.path.join(d, "temp1_input") if d else None
        self._gpu = _Gpu()
        self._media = _Media()
        self._static = {
            "cpu.name": _cpu_name(),
            "cpu.threads": float(psutil.cpu_count() or 0),
            "host.name": socket.gethostname(),
            "host.os": _os_name(),
            "host.kernel": platform.release(),
        }
        self._lock = threading.Lock()
        self._values: dict[str, object] = dict(self._static)
        self._cores: list[float] = []
        self._history: dict[str, collections.deque] = collections.defaultdict(
            lambda: collections.deque(maxlen=HISTORY)
        )
        self._stop = threading.Event()
        self._prev_net = psutil.net_io_counters()
        self._prev_disk = psutil.disk_io_counters()
        self._prev_t = time.monotonic()
        psutil.cpu_percent(percpu=True)  # prime the counters
        self._started = False

    @property
    def has_gpu(self) -> bool:
        return self._gpu.kind is not None

    def start(self) -> Sensors:
        if not self._started:
            self._started = True
            self._sample()
            threading.Thread(target=self._run, name="turzx-sensors", daemon=True).start()
        return self

    def stop(self) -> None:
        self._stop.set()

    def values(self) -> dict[str, object]:
        with self._lock:
            return dict(self._values)

    def get(self, key: str, default=None):
        with self._lock:
            v = self._values.get(key)
        return default if v is None else v

    def history(self, key: str) -> list[float]:
        with self._lock:
            return list(self._history.get(key, ()))

    def cores(self) -> list[float]:
        with self._lock:
            return list(self._cores)

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                self._sample()
            except Exception:
                log.exception("sensor sampling failed")

    def _sample(self) -> None:
        now = time.monotonic()
        dt = max(now - self._prev_t, 1e-3)
        self._prev_t = now

        cores = psutil.cpu_percent(percpu=True)
        vm = psutil.virtual_memory()
        sw = psutil.swap_memory()
        net = psutil.net_io_counters()
        disk = psutil.disk_io_counters()
        v: dict[str, object] = dict(self._static)
        v.update(
            {
                "cpu.util": sum(cores) / len(cores) if cores else 0.0,
                "cpu.temp": self._cpu_temp.read(),
                "cpu.freq": _max_core_freq() or _psutil_freq(),
                "cpu.load1": os.getloadavg()[0],
                "cpu.load5": os.getloadavg()[1],
                "mem.used": float(vm.total - vm.available),
                "mem.total": float(vm.total),
                "mem.percent": 100.0 * (vm.total - vm.available) / vm.total if vm.total else 0.0,
                "swap.used": float(sw.used),
                "swap.total": float(sw.total),
                "swap.percent": float(sw.percent),
                "net.down": max(0.0, (net.bytes_recv - self._prev_net.bytes_recv) / dt),
                "net.up": max(0.0, (net.bytes_sent - self._prev_net.bytes_sent) / dt),
                "disk.temp": _read_float(self._disk_temp_path, 1000),
                "host.uptime": time.time() - psutil.boot_time(),
                "host.processes": float(len(psutil.pids())),
            }
        )
        try:
            du = psutil.disk_usage(self.disk_path)
            v.update({"disk.used": float(du.used), "disk.total": float(du.total), "disk.percent": float(du.percent)})
        except OSError:
            pass
        if disk and self._prev_disk:
            v["disk.read"] = max(0.0, (disk.read_bytes - self._prev_disk.read_bytes) / dt)
            v["disk.write"] = max(0.0, (disk.write_bytes - self._prev_disk.write_bytes) / dt)
        self._prev_net, self._prev_disk = net, disk
        try:
            bat = psutil.sensors_battery()
            if bat is not None:
                v["battery.percent"] = float(bat.percent)
        except (AttributeError, OSError):
            pass

        gpu = self._gpu.read()
        v.update(gpu)
        if gpu.get("gpu.vram_total"):
            v["gpu.vram_percent"] = 100.0 * float(gpu.get("gpu.vram_used") or 0) / float(gpu["gpu.vram_total"])
        v.update(self._media.read())

        with self._lock:
            self._values = v
            self._cores = cores
            for key in NUMERIC_METRICS:
                val = v.get(key)
                if isinstance(val, (int, float)):
                    self._history[key].append(float(val))
