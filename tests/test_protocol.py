"""Wire-format tests. Vectors are the ones verified against the reference implementation."""

from turzx.protocol import Cmd, Response, StorageInfo, build_header, decrypt_header, size_args

TS = 0x01020304


def test_sync_vector():
    p = build_header(Cmd.SYNC, timestamp=TS)
    assert len(p) == 512
    assert p[:16].hex(" ") == "cc ff a6 fb 53 02 10 74 fd 8e c7 12 b3 92 c6 a6"
    assert p[496:512].hex(" ") == "ec 54 35 a2 83 9f c6 a3 00 00 00 00 00 00 a1 1a"


def test_brightness_vector():
    p = build_header(Cmd.BRIGHTNESS, bytes([40]), timestamp=TS)
    assert p[:16].hex(" ") == "d9 28 43 51 e5 6c ef 7f 51 32 87 53 23 2e 04 f6"


def test_png_vector():
    p = build_header(Cmd.SHOW_PNG, size_args(3703), timestamp=TS)
    assert p[:16].hex(" ") == "d5 08 e7 5b ad 17 59 cb a6 26 c1 75 90 6a 61 0e"


def test_roundtrip():
    plain = decrypt_header(build_header(99, b"hello", timestamp=TS))
    assert plain[0] == 99 and plain[2:4] == b"\x1a\x6d" and plain[4:8] == TS.to_bytes(4, "little")
    assert plain[8:13] == b"hello"


def test_response_parsing():
    raw = bytes([10, 0xC8]) + bytes(6) + b"turzx_0001_0015" + bytes(512 - 23)
    r = Response(raw)
    assert r.ok and r.cmd == 10 and r.text() == "turzx_0001_0015"
    assert not Response(b"").ok


def test_storage_info():
    raw = bytearray(512)
    for i, v in enumerate((100, 40, 60, 0, 0, 0)):
        raw[8 + 4 * i: 12 + 4 * i] = v.to_bytes(4, "little")
    s = StorageInfo.parse(Response(bytes(raw)))
    assert (s.card_total, s.card_used, s.card_free) == (100, 40, 60)
