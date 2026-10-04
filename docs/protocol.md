# TURZX USB protocol notes (VID 0x1CBE)

What this driver knows about the "new" Turing / TURZX screens that show up as a vendor-specific USB device
instead of a serial port. Everything marked **verified** was confirmed on a 9.2" TURZX V2
(`1cbe:0092`, firmware `turzx_0001_0015`) with photos of the panel. The command set itself comes from
[turing-smart-screen-python](https://github.com/mathoudebine/turing-smart-screen-python) (`lcd_comm_turing_usb.py`)
and the protocol notes of [bezel](https://github.com/slipalison/bezel).

## Transport

- USB 2.0 high speed, one vendor-class interface, bulk OUT `0x01` and bulk IN `0x81`, 512-byte packets.
- No kernel driver binds to it; libusb (pyusb) talks to it directly. On Linux the device node needs to be
  accessible to the user (see `packaging/60-turzx.rules`).
- **verified:** every command is answered by one 512-byte packet **followed by a zero-length packet**. Read
  with a 1024-byte buffer and the ZLP is consumed in the same transfer. If you read 512 bytes at a time the ZLP stays
  queued and every later reply appears to be "one command late".

## Command packet

```
plaintext (500 bytes)
  [0]      command id
  [1]      0x00
  [2..3]   0x1a 0x6d
  [4..7]   timestamp, little endian (ms since local midnight; not checked by the firmware)
  [8..]    arguments (integers big endian unless noted)

wire packet (512 bytes)
  [0..503]   DES-CBC(plaintext + 4 zero bytes), key = IV = "slv3tuzx"
  [504..509] 0x00
  [510..511] 0xa1 0x1a
```

A payload (image, video chunk, file data) follows the 512 bytes **unencrypted in the same bulk write**.

Reply: `[0]` echoes the command id, `[1] == 0xC8` means OK.

## Commands used by this driver

| id | name | args | notes |
|---|---|---|---|
| 10 | sync | | reply `[8..39]` = firmware string, e.g. `turzx_0001_0015` |
| 14 | brightness | `[8]` = 0..102 | not persistent |
| 101 | show JPEG | `[8..11]` = size | payload: baseline JPEG ≤ 1 MiB |
| 102 | show PNG | `[8..11]` = size | payload: **RGBA** PNG ≤ 1 MiB |
| 111 | stop local playback | | stops the screen's own wallpaper video |
| 112 | playback busy? | | `[8] != 0` while still stopping |
| 100 | storage info | | six LE32 KiB values: card total/used/free, internal total/used/free |
| 15, 17, 121, 122, 123 | H.264 streaming | | frame rate, chunk size, chunk, queue depth, stop (not used by default) |

Commands that write storage, reboot, change persistent settings or switch to "desktop mode" exist (see the
references) but are never sent by this project.

## Display model (verified on the 9.2")

These findings are what make the image sharp and correctly placed; the reference implementations get them wrong for
this model:

1. **Frames are 464×1920**, not 480×1920. The 9.2" display layers are 464 pixels wide (29 × 16, a hardware JPEG
   decoder alignment). A 480-wide JPEG is decoded with the wrong stride: the image is roughly right, but its last rows
   wrap around onto the first ~70 rows (garbage with diagonal streaks at one end) and the other end is never
   updated. About 462 of the 464 columns are visible behind the bezel.
2. **Two layers.** JPEG frames (command 101) and the device's own video go to a base layer. PNG frames
   (command 102) go to an **overlay layer above it**, with per-pixel alpha. The overlay keeps its last content,
   even across reconnects, so an old opaque PNG hides every later JPEG. Send a fully transparent 464×1920 PNG
   once when taking over the screen (this is what the vendor app does before streaming).
3. **PNG must be RGBA.** An RGB PNG is decoded as if it had 4 bytes per pixel: the image smears sideways and
   whatever lands in the alpha channel makes parts of it transparent.
4. JPEG chroma subsampling 4:4:4 and 4:2:0 both decode correctly once the width is right (4:2:2 untested).
   This driver uses 4:4:4 quality 90 for crisp coloured text.
5. Throughput: about 20 fps for an 800 KB noisy JPEG and more than 45 fps for simple frames (~12-16 MB/s).

Orientation: the native frame is portrait. With the panel lying on its side the way the factory wallpaper reads,
native `y = 0` is the left end and native `x = 0` is the bottom edge, so a landscape image is rotated 90° clockwise
before sending (`turzx.screen.Orientation.LANDSCAPE`).

## Other models

The model table in `src/turzx/device.py` lists the other product ids from the references. They probably share the
protocol but their exact frame geometry is unverified. If yours misbehaves at one edge, try a width rounded up to a
multiple of 16. Please open an issue with `turzx info` output and a photo of `turzx test-pattern`.
