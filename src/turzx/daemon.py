"""The background service: owns the screen and shows the active layout.

* waits for a screen and (re)connects whenever one is plugged in, reset or the host resumes;
* reloads settings and the layout as soon as their files change (the GUI just saves);
* shows the GUI's live preview while an editor is open;
* hands the screen over to one-shot CLI commands that request a pause.
"""

from __future__ import annotations

import fcntl
import logging
import os
import signal
import threading
import time
from pathlib import Path
from typing import Any

from . import config
from .config import Settings
from .device import Device, DeviceError, DeviceNotFound
from .render import RenderContext, Renderer
from .render.layout import new_layout
from .screen import Orientation, Screen
from .sensors import Sensors

log = logging.getLogger(__name__)

RELOAD_EVERY = 0.5  # seconds between file checks
STATUS_EVERY = 2.0


class AlreadyRunning(RuntimeError):
    pass


class Daemon:
    def __init__(self, layout_override: str | None = None, settings_override: dict[str, Any] | None = None, *,
                 layout_data: dict[str, Any] | None = None, service: bool = False):
        """``service=True`` is the background service: it takes the single-instance lock, writes
        the status file and honours pause requests. Otherwise this is a foreground run."""
        self.layout_override = layout_override
        self.layout_data = layout_data
        self.service = service
        self.settings_override = settings_override or {}
        self.stop_event = threading.Event()
        self.reload_event = threading.Event()
        self.settings = self._load_settings()
        self.sensors = Sensors(self.settings.sensor_interval, self.settings.disk).start()
        self.renderer = Renderer(RenderContext(self.sensors, scale=self.settings.supersample))
        self.screen: Screen | None = None
        self.layout: dict[str, Any] = new_layout()
        self.layout_name = ""
        self._settings_mtime = 0.0
        self._layout_path = None
        self._layout_mtime = 0.0
        self._applied_brightness: float | None = None
        self._state = "starting"
        self._error = ""
        self._fps = 0.0
        self._lock_fd: int | None = None
        self._load_layout()

    # ---------------------------------------------------------------- setup
    def _load_settings(self) -> Settings:
        s = Settings.load()
        for k, v in self.settings_override.items():
            setattr(s, k, v)
        return s

    def acquire_lock(self) -> None:
        config.RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        fd = os.open(config.LOCK_FILE, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            st = config.read_status()
            raise AlreadyRunning(f"turzx daemon is already running (pid {st.get('pid') if st else '?'})") from None
        self._lock_fd = fd

    def install_signal_handlers(self) -> None:
        def stop(signum, _frame):
            log.info("signal %d: stopping", signum)
            self.stop_event.set()

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        signal.signal(signal.SIGHUP, lambda *_: self.reload_event.set())

    # ------------------------------------------------------------ reloading
    def _load_layout(self) -> None:
        if self.layout_data is not None:
            self.layout = self.layout_data
            self.layout_name = self.layout_data.get("name", "")
            return
        name = self.layout_override or self.settings.layout
        try:
            if self.layout_override and (name.endswith(".json") or os.sep in name):
                path = os.path.expanduser(name)
            else:
                ref = config.find_layout(name) or config.find_layout("classic")
                path = str(ref.path) if ref else None
            if path is None:
                raise FileNotFoundError(name)
            self.layout = config.load_layout(path)
            self._layout_path = path
            self._layout_mtime = config.mtime(Path(path))
            self.layout_name = self.layout.get("name", name)
            log.info("layout: %s", self.layout_name)
        except (OSError, ValueError) as e:
            log.error("cannot load layout %r: %s", name, e)
            self._error = f"layout {name!r}: {e}"

    def _check_reload(self) -> None:
        sm = config.mtime(config.SETTINGS_FILE)
        if sm != self._settings_mtime or self.reload_event.is_set():
            self._settings_mtime = sm
            old = self.settings
            self.settings = self._load_settings()
            if self.settings.layout != old.layout or self.reload_event.is_set():
                self._load_layout()
            if self.settings.supersample != old.supersample:
                self.renderer.ctx.scale = self.settings.supersample
            if self.settings.sensor_interval != old.sensor_interval:
                self.sensors.interval = self.settings.sensor_interval
            if self.settings.disk != old.disk:
                self.sensors.disk_path = self.settings.disk
            if self.screen and self.settings.orientation != self.screen.orientation.value:
                self.screen.orientation = Orientation(self.settings.orientation)
                self.screen._last = None
            if self.screen:
                self.screen.quality = self.settings.quality
            self.reload_event.clear()
        if self._layout_path and config.mtime(Path(self._layout_path)) != self._layout_mtime:
            self._load_layout()

    def _apply_brightness(self) -> None:
        if not self.screen:
            return
        b = self.settings.effective_brightness()
        if b != self._applied_brightness:
            self.screen.device.set_brightness(b)
            self._applied_brightness = b

    # --------------------------------------------------------------- status
    def _write_status(self) -> None:
        if not self.service:
            return
        dev = self.screen.device if self.screen else None
        try:
            config.write_json(config.STATUS_FILE, {
                "pid": os.getpid(),
                "updated": time.time(),
                "state": self._state,
                "error": self._error,
                "layout": self.layout_name,
                "layout_id": self.layout_override or self.settings.layout,
                "fps": round(self._fps, 2),
                "model": dev.model.name if dev else None,
                "pid_usb": f"{dev.model.pid:04x}" if dev else None,
                "firmware": dev.firmware if dev else None,
                "serial": dev.serial if dev else None,
                "size": list(self.screen.size) if self.screen else None,
                "preview": config.read_preview() is not None,
            })
        except OSError as e:
            log.debug("cannot write status: %s", e)

    # ------------------------------------------------------------------ run
    def _connect(self) -> None:
        pid = int(self.settings.device_pid, 16) if self.settings.device_pid else None
        dev = Device.open(pid=pid)
        log.info("connected: %s, firmware %s, serial %s", dev.model.name, dev.firmware, dev.serial)
        if not dev.model.verified:
            log.warning("%s is not yet verified with this driver; please report how it works", dev.model.name)
        dev.init_display()
        self.screen = Screen(dev, Orientation(self.settings.orientation), quality=self.settings.quality)
        self._applied_brightness = None
        self._apply_brightness()
        self._state = "running"
        self._error = ""

    def _disconnect(self, final: bool = False) -> None:
        if not self.screen:
            return
        dev = self.screen.device
        if final:
            try:
                if self.settings.on_exit == "off":
                    dev.clear()
                    dev.set_brightness(0)
                elif self.settings.on_exit == "blank":
                    dev.clear()
            except DeviceError:
                pass
        dev.close()
        self.screen = None

    def run(self) -> None:
        last_check = last_status = 0.0
        waiting_logged = False
        frames, fps_t0 = 0, time.monotonic()
        next_frame = time.monotonic()
        self._settings_mtime = config.mtime(config.SETTINGS_FILE)
        try:
            while not self.stop_event.is_set():
                now = time.monotonic()
                if now - last_check >= RELOAD_EVERY:
                    last_check = now
                    self._check_reload()
                if now - last_status >= STATUS_EVERY:
                    last_status = now
                    self._write_status()

                # a CLI command asked for the screen
                owner = config.pause_owner() if self.service else None
                if owner is not None:
                    if self.screen:
                        log.info("pausing: screen lent to pid %d", owner)
                        self._disconnect()
                    if self._state != "paused":
                        self._state = "paused"
                        self._write_status()
                    self.stop_event.wait(0.3)
                    continue

                if self.screen is None:
                    try:
                        self._connect()
                        waiting_logged = False
                        self._write_status()
                    except DeviceNotFound as e:
                        self._state, self._error = "waiting", str(e)
                        if not waiting_logged:
                            log.warning("%s; waiting for a screen", e)
                            waiting_logged = True
                        self.stop_event.wait(1.0)
                        continue
                    except DeviceError as e:
                        self._state, self._error = "error", str(e)
                        log.warning("cannot open the screen: %s", e)
                        self.stop_event.wait(3.0)
                        continue

                try:
                    layout = (config.read_preview() if self.service else None) or self.layout
                    image = self.renderer.render(layout, self.screen.size)
                    self._apply_brightness()
                    self.screen.show(image)
                except DeviceError as e:
                    log.warning("screen connection lost (%s); reconnecting", e)
                    self._state, self._error = "waiting", str(e)
                    self._disconnect()
                    self.stop_event.wait(1.0)
                    continue

                frames += 1
                if time.monotonic() - fps_t0 >= 5:
                    self._fps = frames / (time.monotonic() - fps_t0)
                    frames, fps_t0 = 0, time.monotonic()
                period = 1.0 / max(0.1, self.settings.fps)
                next_frame += period
                delay = next_frame - time.monotonic()
                if delay < -1.0:  # fell far behind (suspend, slow render): resync
                    next_frame = time.monotonic()
                elif delay > 0:
                    self.stop_event.wait(delay)
        finally:
            self._disconnect(final=True)
            self._state = "stopped"
            if self.service:
                try:
                    config.STATUS_FILE.unlink()
                except OSError:
                    pass
