"""Wire format of the TURZX / Turing Smart Screen USB generation (VID 0x1CBE).

Every command is a single bulk OUT transfer made of:

* a 512-byte header: a 500-byte plaintext block, DES-CBC encrypted
  (key = IV = ``b"slv3tuzx"``) into 504 bytes, then six zero bytes and the
  trailer ``a1 1a``;
* an optional *unencrypted* payload (PNG, JPEG, H.264, file data) appended to
  the same transfer.

The device answers every command with one 512-byte bulk IN packet followed by a
zero-length packet. ``resp[0]`` echoes the command id and ``resp[1] == 0xC8``
(HTTP-style 200) means success.

This module is pure: it builds and parses bytes and never touches USB.
"""

from __future__ import annotations

import enum
import struct
import time
from dataclasses import dataclass

from Crypto.Cipher import DES

DES_KEY = b"slv3tuzx"
HEADER_PLAIN_SIZE = 500
PACKET_SIZE = 512
TRAILER = b"\xa1\x1a"
STATUS_OK = 0xC8

# Largest payload the firmware accepts in one command; bigger transfers time out.
MAX_PAYLOAD = 1024 * 1024


class Cmd(enum.IntEnum):
    """Command ids. Only the ones marked *safe* are sent implicitly by this project."""

    SYNC = 10  # safe: handshake, returns the firmware version string
    REBOOT = 11
    ROTATION = 13  # persistent on some firmwares
    BRIGHTNESS = 14  # safe: [8] = 0..102
    FRAME_RATE = 15  # safe: [8] = fps for H.264 streaming
    H264_CHUNK_SIZE = 17  # safe query
    FILE_OPEN = 38
    FILE_WRITE = 39
    FILE_DELETE_OR_SMALL_WRITE = 40  # meaning disputed, never sent
    LIST_DIR = 99
    STORAGE_INFO = 100  # safe query
    SHOW_JPEG = 101  # safe: [8..11] BE32 size + JPEG payload
    SHOW_PNG = 102  # safe: [8..11] BE32 size + RGBA PNG payload
    PLAY_STORED_VIDEO = 110
    STOP_PLAYBACK = 111  # safe: stops the device's own background video/image
    PLAYBACK_BUSY = 112  # safe query: resp[8] != 0 while still stopping
    PLAY_STORED_IMAGE = 113
    H264_CHUNK = 121  # safe while streaming
    STREAM_STATUS = 122  # safe query: resp[8] = decoder queue depth
    STOP_STREAM = 123  # safe
    SAVE_SETTINGS = 125  # persistent


def _timestamp_ms() -> int:
    """Milliseconds since local midnight, as the reference implementations send.

    The firmware does not appear to validate it.
    """
    now = time.time()
    lt = time.localtime(now)
    midnight = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))
    return int((now - midnight) * 1000) & 0xFFFFFFFF


def build_header(cmd: int, args: bytes = b"", timestamp: int | None = None) -> bytes:
    """Return the 512-byte encrypted command header.

    ``args`` is written at plaintext offset 8. Offsets 496..503 are unreliable
    (the vendor and Python references pad differently there), so arguments are
    limited to 488 bytes.
    """
    if not 0 <= cmd <= 0xFF:
        raise ValueError(f"command id out of range: {cmd}")
    if len(args) > 488:
        raise ValueError(f"arguments too long: {len(args)} > 488 bytes")

    plain = bytearray(HEADER_PLAIN_SIZE)
    plain[0] = cmd
    plain[2] = 0x1A
    plain[3] = 0x6D
    ts = _timestamp_ms() if timestamp is None else timestamp
    plain[4:8] = struct.pack("<I", ts & 0xFFFFFFFF)
    plain[8 : 8 + len(args)] = args

    padded = bytes(plain) + b"\x00" * (-len(plain) % 8)  # 504 bytes
    cipher = DES.new(DES_KEY, DES.MODE_CBC, DES_KEY).encrypt(padded)

    packet = bytearray(PACKET_SIZE)
    packet[: len(cipher)] = cipher
    packet[-2:] = TRAILER
    return bytes(packet)


def decrypt_header(packet: bytes) -> bytes:
    """Inverse of :func:`build_header` (useful for analysing captures)."""
    if len(packet) < 504:
        raise ValueError("packet too short")
    return DES.new(DES_KEY, DES.MODE_CBC, DES_KEY).decrypt(bytes(packet[:504]))[:HEADER_PLAIN_SIZE]


def be32(value: int) -> bytes:
    return struct.pack(">I", value)


def size_args(size: int) -> bytes:
    """``[8..11]`` = big-endian payload size, used by frame and stream commands."""
    if not 0 <= size <= MAX_PAYLOAD:
        raise ValueError(f"payload of {size} bytes exceeds the {MAX_PAYLOAD}-byte device limit")
    return be32(size)


@dataclass(frozen=True)
class Response:
    raw: bytes

    @property
    def cmd(self) -> int:
        return self.raw[0] if self.raw else -1

    @property
    def ok(self) -> bool:
        return len(self.raw) > 1 and (self.raw[1] == STATUS_OK or (len(self.raw) > 8 and self.raw[8] == STATUS_OK))

    def u8(self, offset: int) -> int:
        return self.raw[offset] if len(self.raw) > offset else 0

    def le32(self, offset: int) -> int:
        return struct.unpack_from("<I", self.raw, offset)[0] if len(self.raw) >= offset + 4 else 0

    def be32(self, offset: int) -> int:
        return struct.unpack_from(">I", self.raw, offset)[0] if len(self.raw) >= offset + 4 else 0

    def text(self, start: int = 8, end: int = 40) -> str:
        return self.raw[start:end].split(b"\x00", 1)[0].decode("utf-8", "replace")


@dataclass(frozen=True)
class StorageInfo:
    """Storage sizes in KiB, as reported by :attr:`Cmd.STORAGE_INFO`."""

    card_total: int
    card_used: int
    card_free: int
    internal_total: int
    internal_used: int
    internal_free: int

    @classmethod
    def parse(cls, resp: Response) -> StorageInfo:
        return cls(*(resp.le32(8 + 4 * i) for i in range(6)))
