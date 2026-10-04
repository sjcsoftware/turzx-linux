# Contributing

Thanks for helping! Pick whatever fits you.

## 1. Make another screen model work (most wanted)

Only the 9.2" V2 is verified. Other `1cbe:xxxx` screens most likely speak the same protocol, but each model needs
its exact frame size confirmed.

1. `lsusb | grep 1cbe`: note your product id.
2. `turzx info`: does it connect? Note the firmware string.
3. `turzx test-pattern`: take a photo of the screen.
4. Check the photo:
   - Clean bars with a red border on all four sides: it works. Open an issue or PR marking it verified.
   - Garbage or old content at one end: the frame width is off. Try nearby multiples of 16 for `native` in
     `MODELS` (`src/turzx/device.py`), as with the 9.2" (`464` instead of `480`).
5. Open an issue with the outputs and photo, or a PR changing `MODELS` and adding the id to `VERIFIED_PIDS`.

Not listed at all? Add a `_m(...)` line with your id and resolution and try it. [docs/protocol.md](docs/protocol.md)
explains the protocol.

## 2. Share a layout

Design it in TURZX Studio, then **Export…**. To ship it built-in, add a function to
[`tools/make_presets.py`](tools/make_presets.py), run it, then run `tools/make_screenshots.py --studio`.

## 3. Add a widget

Subclass `Widget` in [`src/turzx/render/widgets.py`](src/turzx/render/widgets.py), list its `fields`, implement
`draw()` and decorate it with `@register`. Studio builds the editor from `fields` automatically. Run
`tools/make_docs.py` afterwards.

## 4. Add sensors

Metrics live in [`src/turzx/sensors.py`](src/turzx/sensors.py) (`METRICS` plus `_sample`). Wanted: Intel GPUs,
per-fan RPM, multiple disks and network interfaces, laptop power draw.

## 5. Other ideas

- Packages: AUR, `.deb`, Flatpak.
- Screen mirroring on Wayland (PipeWire portal).
- Hardware H.264 streaming (commands 121-123) for smooth video.
- Translations of the Studio UI.

## Development

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[gui,nvidia,mirror,dev]"
pytest                      # no hardware needed
ruff check src tests tools
turzx -vv run classic       # foreground run with debug logs (pauses the service)
```

Keep PRs small, run the tests, and don't send commands that write device storage or change persistent settings
unless the user explicitly asks for it.
