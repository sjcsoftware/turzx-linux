import pytest


class FakeSensors:
    """Deterministic stand-in for turzx.sensors.Sensors."""

    def __init__(self):
        self.values = {
            "cpu.util": 37.0, "cpu.temp": 61.0, "cpu.freq": 5.12, "cpu.load1": 1.5, "cpu.load5": 1.2,
            "cpu.threads": 16.0, "cpu.name": "Test CPU 9000",
            "gpu.util": 55.0, "gpu.temp": 66.0, "gpu.power": 180.0, "gpu.clock": 2.4, "gpu.fan": 40.0,
            "gpu.vram_used": 4 * 1024**3, "gpu.vram_total": 12 * 1024**3, "gpu.vram_percent": 33.3,
            "gpu.name": "NVIDIA GeForce Test 1000",
            "mem.used": 20 * 1024**3, "mem.total": 64 * 1024**3, "mem.percent": 31.25,
            "swap.used": 0.0, "swap.total": 8 * 1024**3, "swap.percent": 0.0,
            "disk.used": 900e9, "disk.total": 2e12, "disk.percent": 45.0, "disk.read": 12e6, "disk.write": 3e6,
            "disk.temp": 41.0, "net.down": 2.5e6, "net.up": 1.2e5,
            "host.name": "testhost", "host.os": "Test OS", "host.kernel": "6.0.0", "host.uptime": 93784.0,
            "host.processes": 400.0, "media.title": "", "media.artist": "", "media.status": "",
        }

    def get(self, key, default=None):
        v = self.values.get(key)
        return default if v is None else v

    def history(self, key):
        v = self.values.get(key)
        return [float(v) * (0.5 + (i % 10) / 20) for i in range(120)] if isinstance(v, float) else []

    def cores(self):
        return [float((i * 37) % 100) for i in range(16)]


@pytest.fixture
def sensors():
    return FakeSensors()


@pytest.fixture
def ctx(sensors):
    from turzx.render import RenderContext

    return RenderContext(sensors, scale=1)


@pytest.fixture
def xdg(tmp_path, monkeypatch):
    """Point config and runtime dirs at a temp folder."""
    import importlib

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))
    from turzx import config

    importlib.reload(config)
    yield config
    importlib.reload(config)
