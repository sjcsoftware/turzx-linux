"""USB transport and high-level device API for TURZX USB screens."""

from __future__ import annotations

import io
import logging
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass

import usb.core
import usb.util
from PIL import Image

from .protocol import (
    MAX_PAYLOAD,
    Cmd,
    Response,
    StorageInfo,
    build_header,
    size_args,
)

log = logging.getLogger(__name__)

VENDOR_ID = 0x1CBE
EP_OUT = 0x01
EP_IN = 0x81


@dataclass(frozen=True)
class Model:
    name: str
    pid: int
    #: Size of the frames the firmware expects, portrait (width, height).
    native: tuple[int, int]
    #: Pixels actually visible on the glass, portrait. Frames are padded from
    #: this to ``native`` on the right-hand side.
    visible: tuple[int, int]

    @property
    def verified(self) -> bool:
        return self.pid in VERIFIED_PIDS


def _m(name: str, pid: int, w: int, h: int, vis_w: int | None = None) -> Model:
    return Model(name, pid, (w, h), (vis_w or w, h))


MODELS: dict[int, Model] = {
    m.pid: m
    for m in (
        _m('Turing 1.6" square', 0x0005, 400, 400),
        _m('Turing 2.8" square', 0x0016, 400, 400),
        _m('Turing 2.1" round', 0x0021, 480, 480),
        _m('Turing 2.8" round', 0x0028, 480, 480),
        _m('Turing 3.4" square', 0x0034, 480, 480),
        _m('Turing 4" square', 0x0040, 720, 720),
        _m('Turing 4.6"', 0x0046, 320, 960),
        _m('Turing 5.2"', 0x0050, 720, 1280),
        _m('Turing 8"', 0x0080, 800, 1280),
        _m('Turing 8.8" (HW rev 1.x)', 0x0088, 480, 1920),
        # The 9.2" display layers are 464x1920: wider JPEGs overflow and wrap
        # their tail onto the first rows. About 462 columns reach the glass.
        # Verified on firmware turzx_0001_0015.
        _m('Turing 9.2" (TURZX V2)', 0x0092, 464, 1920, vis_w=462),
        _m('Turing 12.3"', 0x0123, 720, 1920),
        _m('Turing 2.88" round', 0x0288, 480, 480),
    )
}

VERIFIED_PIDS = {0x0092}


class DeviceNotFound(RuntimeError):
    pass


class DeviceError(RuntimeError):
    pass


def find_devices(any_pid: bool = False) -> list[usb.core.Device]:
    """Attached screens. Unknown PIDs are skipped unless ``any_pid``: VID 0x1CBE is TI's
    Stellaris vendor id and is shared with development boards we must not talk to."""
    found = usb.core.find(find_all=True, idVendor=VENDOR_ID) or []
    return [d for d in found if any_pid or d.idProduct in MODELS]


class Device:
    """One TURZX USB screen.

    All commands are serialised by an internal lock so a Device can be shared
    between threads (for instance a renderer and a brightness control).
    """

    def __init__(self, usb_dev: usb.core.Device):
        self._dev = usb_dev
        self._lock = threading.RLock()
        pid = usb_dev.idProduct
        self.model = MODELS.get(pid) or Model(f"Unknown TURZX (PID {pid:04x})", pid, (480, 1920), (480, 1920))
        self.firmware: str | None = None
        self._claimed = False

    # ------------------------------------------------------------------ setup
    @classmethod
    def open(cls, pid: int | None = None, serial: str | None = None) -> Device:
        """Open the first matching screen and perform the handshake."""
        try:
            candidates = find_devices(any_pid=pid is not None)
        except usb.core.NoBackendError as e:
            raise DeviceError("libusb-1.0 is not installed (apt install libusb-1.0-0)") from e
        for d in candidates:
            if pid is not None and d.idProduct != pid:
                continue
            if serial is not None and usb.util.get_string(d, d.iSerialNumber) != serial:
                continue
            dev = cls(d)
            dev._claim()
            dev.sync()
            return dev
        raise DeviceNotFound(
            "no TURZX USB screen found (VID 1cbe). Check the cable, and on Linux that the udev rule is installed."
        )

    def _claim(self) -> None:
        d = self._dev
        try:
            if d.is_kernel_driver_active(0):
                d.detach_kernel_driver(0)
        except (usb.core.USBError, NotImplementedError):
            pass
        try:
            d.set_configuration()
        except usb.core.USBError as e:
            if e.errno == 13:  # EACCES
                raise DeviceError(
                    "permission denied opening the screen; install packaging/99-turzx.rules (see README)"
                ) from e
            if e.errno != 16:  # EBUSY: already configured is fine
                raise
        try:
            usb.util.claim_interface(d, 0)
        except usb.core.USBError as e:
            if e.errno == 16:
                raise DeviceError("the screen is in use by another process (is turzx-monitor running?)") from e
            raise
        self._claimed = True
        self._drain()

    def close(self) -> None:
        with self._lock:
            if self._claimed:
                try:
                    usb.util.release_interface(self._dev, 0)
                except usb.core.USBError:
                    pass
                self._claimed = False
            usb.util.dispose_resources(self._dev)

    def __enter__(self) -> Device:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    @property
    def serial(self) -> str | None:
        try:
            return usb.util.get_string(self._dev, self._dev.iSerialNumber)
        except (usb.core.USBError, ValueError):
            return None

    # -------------------------------------------------------------- transport
    def _drain(self) -> None:
        """Discard stale responses and zero-length packets left by a previous session."""
        for _ in range(16):
            try:
                self._dev.read(EP_IN, 1024, 50)
            except usb.core.USBError:
                return

    def command(self, cmd: int, args: bytes = b"", payload: bytes = b"", timeout: int = 3000) -> Response:
        """Send one command and return its response.

        The device replies with a 512-byte packet plus a zero-length packet;
        reading into a 1024-byte buffer consumes both. Replies that do not echo
        ``cmd`` are stale and skipped.
        """
        data = build_header(cmd, args) + payload
        with self._lock:
            try:
                self._dev.write(EP_OUT, data, timeout)
                deadline = time.monotonic() + timeout / 1000
                while True:
                    remaining = max(1, int((deadline - time.monotonic()) * 1000))
                    raw = bytes(self._dev.read(EP_IN, 1024, remaining))
                    if raw and raw[0] == (cmd & 0xFF):
                        return Response(raw)
                    if time.monotonic() >= deadline:
                        raise DeviceError(f"no reply to command {cmd}")
            except usb.core.USBTimeoutError as e:
                raise DeviceError(f"timeout waiting for command {cmd}") from e
            except usb.core.USBError as e:
                raise DeviceError(f"USB error on command {cmd}: {e}") from e

    # ---------------------------------------------------------- basic control
    def sync(self) -> str:
        """Handshake; returns and caches the firmware version (e.g. ``turzx_0001_0015``)."""
        self.firmware = self.command(Cmd.SYNC).text()
        return self.firmware

    def set_brightness(self, percent: float) -> None:
        """Backlight level 0..100 (%). 0 turns the backlight off."""
        if not 0 <= percent <= 100:
            raise ValueError("brightness must be within 0..100")
        self.command(Cmd.BRIGHTNESS, bytes([round(percent * 102 / 100)]))

    def stop_playback(self, wait: float = 1.0) -> None:
        """Stop the screen's built-in background video/image so frames are not overlaid on it."""
        self.command(Cmd.STOP_PLAYBACK)
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            if self.command(Cmd.PLAYBACK_BUSY).u8(8) == 0:
                return
            time.sleep(0.1)

    def clear_overlay(self) -> None:
        """Make the PNG overlay layer fully transparent.

        The firmware composites PNG frames on a layer *above* JPEG frames and
        the device's own video, and that layer keeps its last content (even
        across reconnects), so it must be cleared before streaming JPEGs.
        """
        self.show_png(encode_png(Image.new("RGBA", self.model.native, (0, 0, 0, 0))))

    def init_display(self) -> None:
        """Take over the screen: stop built-in playback and clear the overlay."""
        self.stop_playback()
        self.clear_overlay()

    def storage_info(self) -> StorageInfo:
        return StorageInfo.parse(self.command(Cmd.STORAGE_INFO))

    def reboot(self) -> None:
        """Reboot the screen. It disappears from USB for a few seconds; reopen afterwards."""
        with self._lock:
            try:
                self._dev.write(EP_OUT, build_header(Cmd.REBOOT), 2000)
            except usb.core.USBError:
                pass

    # ----------------------------------------------------------------- frames
    def show_jpeg(self, data: bytes) -> None:
        resp = self.command(Cmd.SHOW_JPEG, size_args(len(data)), data)
        if not resp.ok:
            raise DeviceError(f"screen rejected JPEG frame ({len(data)} bytes)")

    def show_png(self, data: bytes) -> None:
        """Show a PNG. It *must* be RGBA: the firmware misreads RGB PNGs."""
        resp = self.command(Cmd.SHOW_PNG, size_args(len(data)), data)
        if not resp.ok:
            raise DeviceError(f"screen rejected PNG frame ({len(data)} bytes)")

    def show_native(self, image: Image.Image, *, quality: int = 90, overlay: bool = False) -> int:
        """Send an image already in native portrait orientation and size.

        Opaque frames go out as JPEG (fastest) to the base layer. With
        ``overlay=True`` an RGBA PNG goes to the overlay layer instead, where
        transparent pixels show the base layer (a JPEG frame or the device's
        own video) underneath. Returns the number of bytes transferred.
        """
        if image.size != self.model.native:
            raise ValueError(f"frame is {image.size}, the screen expects {self.model.native}")
        if overlay:
            data = encode_png(image)
            if len(data) <= MAX_PAYLOAD:
                self.show_png(data)
                return len(data)
        data = encode_jpeg(image, quality)
        self.show_jpeg(data)
        return len(data)

    def clear(self, color: tuple[int, int, int] = (0, 0, 0)) -> None:
        self.show_native(Image.new("RGB", self.model.native, color))

    # ------------------------------------------------------------ H.264 video
    def stream_h264(self, chunks: Iterator[bytes], fps: int = 25) -> None:
        """Stream an Annex-B H.264 elementary stream (no B-frames) to the hardware decoder."""
        self.command(Cmd.FRAME_RATE, bytes([fps]))
        try:
            for chunk, last in _with_last(chunks):
                args = size_args(len(chunk)) + (b"\x01" if last else b"\x00")
                resp = self.command(Cmd.H264_CHUNK, args, chunk, timeout=5000)
                depth = resp.u8(8)
                while depth > 3:
                    time.sleep(0.05)
                    depth = self.command(Cmd.STREAM_STATUS).u8(8)
        finally:
            self.command(Cmd.STOP_STREAM)

    def h264_chunk_size(self) -> int:
        size = self.command(Cmd.H264_CHUNK_SIZE).be32(8)
        return size if 0 < size <= MAX_PAYLOAD else 202_752


def _with_last(it: Iterator[bytes]) -> Iterator[tuple[bytes, bool]]:
    it = iter(it)
    try:
        prev = next(it)
    except StopIteration:
        return
    for item in it:
        yield prev, False
        prev = item
    yield prev, True


def encode_jpeg(image: Image.Image, quality: int = 90) -> bytes:
    if image.mode != "RGB":
        image = image.convert("RGB")
    for q in range(quality, 9, -10):
        buf = io.BytesIO()
        # 4:4:4 keeps small coloured text crisp; the link has bandwidth to spare.
        image.save(buf, "JPEG", quality=q, subsampling=0 if q >= 70 else 2)
        if buf.tell() <= MAX_PAYLOAD:
            return buf.getvalue()
    raise DeviceError("could not encode a JPEG frame under the 1 MiB device limit")


def encode_png(image: Image.Image) -> bytes:
    buf = io.BytesIO()
    image.convert("RGBA").save(buf, "PNG", compress_level=1)
    return buf.getvalue()
