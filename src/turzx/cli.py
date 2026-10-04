"""Command line interface: ``turzx <command> ...``."""

from __future__ import annotations

import argparse
import logging
import shutil
import subprocess
import sys
import time

from PIL import Image, ImageDraw, ImageOps

from . import __version__, config
from .device import MODELS, Device, DeviceError, find_devices
from .screen import Orientation, Screen

log = logging.getLogger("turzx")


def _fit(image: Image.Image, size: tuple[int, int], mode: str, background=(0, 0, 0)) -> Image.Image:
    image = ImageOps.exif_transpose(image)
    if image.mode in ("RGBA", "LA", "P"):
        rgba = image.convert("RGBA")
        image = Image.new("RGB", image.size, background)
        image.paste(rgba, mask=rgba.getchannel("A"))
    image = image.convert("RGB")
    if mode == "stretch":
        return image.resize(size, Image.Resampling.LANCZOS)
    if mode == "cover":
        return ImageOps.fit(image, size, Image.Resampling.LANCZOS)
    return ImageOps.pad(image, size, Image.Resampling.LANCZOS, color=background)


def _settings_orientation(args) -> str:
    return args.orientation or config.Settings.load().orientation


def _wait_for_ctrl_c(paused: config.DaemonPause) -> None:
    if paused.active:
        print("The background service is paused while this is shown. Press Ctrl+C to hand the screen back.")
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass


# ------------------------------------------------------------------ device
def cmd_info(args) -> int:
    devs = find_devices(any_pid=True)
    if not devs:
        print("No TURZX USB screen (VID 1cbe) found.")
        return 1
    for d in devs:
        m = MODELS.get(d.idProduct)
        print(f"Bus {d.bus:03d} Device {d.address:03d}: 1cbe:{d.idProduct:04x}  {m.name if m else 'unknown (skipped)'}")
    st = config.read_status()
    if st:
        print(f"\nThe background service (pid {st['pid']}) owns the screen: {st.get('state')}, "
              f"layout '{st.get('layout')}', {st.get('fps')} fps")
        print(f"  model {st.get('model')}, firmware {st.get('firmware')}, serial {st.get('serial')}")
        return 0
    with Device.open(pid=args.pid) as dev:
        m = dev.model
        print(f"  model      : {m.name}{'' if m.verified else '  (not yet verified on hardware)'}")
        print(f"  firmware   : {dev.firmware}")
        print(f"  serial     : {dev.serial}")
        print(f"  frame size : {m.native[0]}x{m.native[1]} (visible {m.visible[0]}x{m.visible[1]}, portrait)")
        try:
            s = dev.storage_info()
            card = f"{s.card_total / 1024:.0f} MiB, {s.card_free / 1024:.0f} MiB free" if s.card_total else "none"
            print(f"  SD card    : {card}")
        except DeviceError as e:
            print(f"  storage    : unavailable ({e})")
    return 0


def cmd_brightness(args) -> int:
    if config.read_status():
        s = config.Settings.load()
        s.brightness = args.level
        s.save()
        print(f"brightness set to {args.level:g}% (saved; the service applies it)")
        return 0
    with Device.open(pid=args.pid) as dev:
        dev.set_brightness(args.level)
    return 0


def cmd_show(args) -> int:
    with config.DaemonPause() as paused, Device.open(pid=args.pid) as dev:
        dev.init_display()
        if args.brightness is not None:
            dev.set_brightness(args.brightness)
        screen = Screen(dev, Orientation(_settings_orientation(args)), quality=args.quality or 90)
        screen.show(_fit(Image.open(args.file), screen.size, args.fit), force=True)
        _wait_for_ctrl_c(paused)
    return 0


def cmd_clear(args) -> int:
    rgb = tuple(int(args.color.lstrip("#")[i: i + 2], 16) for i in (0, 2, 4))
    with config.DaemonPause() as paused, Device.open(pid=args.pid) as dev:
        dev.init_display()
        dev.clear(rgb)  # type: ignore[arg-type]
        _wait_for_ctrl_c(paused)
    return 0


def cmd_off(args) -> int:
    with config.DaemonPause() as paused, Device.open(pid=args.pid) as dev:
        dev.init_display()
        dev.clear()
        dev.set_brightness(0)
        _wait_for_ctrl_c(paused)
    return 0


def cmd_test_pattern(args) -> int:
    from .render.draw import font

    with config.DaemonPause() as paused, Device.open(pid=args.pid) as dev:
        dev.init_display()
        dev.set_brightness(args.brightness if args.brightness is not None else 75)
        orientation = _settings_orientation(args)
        screen = Screen(dev, Orientation(orientation))
        w, h = screen.size
        img = Image.new("RGB", (w, h))
        d = ImageDraw.Draw(img)
        bars = [(255, 255, 255), (255, 255, 0), (0, 255, 255), (0, 255, 0), (255, 0, 255), (255, 0, 0), (0, 0, 255)]
        horizontal = w >= h
        for i, c in enumerate(bars):
            if horizontal:
                d.rectangle([i * w // 7, 0, (i + 1) * w // 7, h * 2 // 3], fill=c)
            else:
                d.rectangle([0, i * h // 7, w * 2 // 3, (i + 1) * h // 7], fill=c)
        for i in range(32):
            v = round(i * 255 / 31)
            if horizontal:
                d.rectangle([i * w // 32, h * 2 // 3, (i + 1) * w // 32, h], fill=(v, v, v))
            else:
                d.rectangle([w * 2 // 3, i * h // 32, w, (i + 1) * h // 32], fill=(v, v, v))
        d.rectangle([0, 0, w - 1, h - 1], outline=(255, 0, 0), width=2)
        label = f"{dev.model.name}  {w}x{h}  {orientation}  fw {dev.firmware}"
        f = font(28, "bold")
        tw = d.textlength(label, font=f)
        d.rectangle([w / 2 - tw / 2 - 16, h / 3 - 26, w / 2 + tw / 2 + 16, h / 3 + 26], fill=(0, 0, 0))
        d.text((w / 2, h / 3), label, font=f, fill=(255, 255, 255), anchor="mm")
        d.text((12, 8), "TOP-LEFT", font=font(20, "bold"), fill=(0, 0, 0))
        screen.show(img, force=True)
        _wait_for_ctrl_c(paused)
    return 0


# ----------------------------------------------------------------- layouts
def _overrides(args) -> dict:
    out = {}
    for key in ("orientation", "brightness", "fps", "quality"):
        v = getattr(args, key, None)
        if v is not None:
            out[key] = v
    if getattr(args, "on_exit", None):
        out["on_exit"] = args.on_exit
    return out


def cmd_daemon(args) -> int:
    from .daemon import AlreadyRunning, Daemon

    d = Daemon(settings_override=_overrides(args), service=True)
    try:
        d.acquire_lock()
    except AlreadyRunning as e:
        print(e, file=sys.stderr)
        return 1
    d.install_signal_handlers()
    d.run()
    return 0


def _foreground(args, layout_override=None, layout_data=None, extra=None) -> int:
    from .daemon import Daemon

    with config.DaemonPause():
        d = Daemon(layout_override, {**_overrides(args), **(extra or {})}, layout_data=layout_data)
        d.install_signal_handlers()
        d.run()
    return 0


def cmd_run(args) -> int:
    try:
        config.load_layout(args.layout)
    except (OSError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return _foreground(args, layout_override=args.layout)


def cmd_mirror(args) -> int:
    from .render.layout import new_layout
    from .render.widgets import WIDGETS

    try:
        import mss  # noqa: F401
    except ImportError:
        print("mirror needs the 'mss' package: pip install 'turzx[mirror]'", file=sys.stderr)
        return 2
    lay = new_layout("Mirror")
    lay["background"]["color"] = "#000000"
    lay["widgets"] = [WIDGETS["mirror"].create(0, 0, 1920, 462, monitor=args.display, region=args.region or "",
                                               fit=args.fit)]
    return _foreground(args, layout_data=lay, extra={"supersample": 1, "fps": args.fps or 20})


def cmd_layouts(args) -> int:
    active = config.Settings.load().layout
    for r in config.list_layouts():
        mark = "*" if r.id == active else " "
        kind = "built-in" if r.builtin else "custom  "
        print(f" {mark} {r.id:<22} {kind} {r.canvas[0]}x{r.canvas[1]:<5} {r.name}")
    print("\n * = active.  Use one:  turzx use <id>     Preview to a file:  turzx render <id> out.png")
    return 0


def cmd_use(args) -> int:
    if not config.find_layout(args.layout):
        print(f"error: no layout {args.layout!r}; see `turzx layouts`", file=sys.stderr)
        return 1
    s = config.Settings.load()
    s.layout = args.layout
    s.save()
    print(f"active layout: {args.layout}" + ("" if config.read_status() else "  (start the service to show it)"))
    return 0


def cmd_render(args) -> int:
    from .render import RenderContext, Renderer
    from .sensors import Sensors

    layout = config.load_layout(args.layout)
    sensors = Sensors(interval=0.25).start()
    time.sleep(args.warmup)
    size = tuple(int(v) for v in args.size.split("x")) if args.size else tuple(layout["canvas"])
    Renderer(RenderContext(sensors)).render(layout, size).save(args.output)  # type: ignore[arg-type]
    print(f"wrote {args.output} ({size[0]}x{size[1]})")
    return 0


def cmd_video(args) -> int:
    """Decode with ffmpeg on the host and push JPEG frames (any format ffmpeg reads)."""
    if not shutil.which("ffmpeg"):
        print("video needs ffmpeg (sudo apt install ffmpeg)", file=sys.stderr)
        return 2
    with config.DaemonPause(), Device.open(pid=args.pid) as dev:
        dev.init_display()
        if args.brightness is not None:
            dev.set_brightness(args.brightness)
        screen = Screen(dev, Orientation(_settings_orientation(args)), quality=args.quality or 90)
        w, h = screen.size
        vf = {
            "cover": f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}",
            "contain": f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2",
            "stretch": f"scale={w}:{h}",
        }[args.fit]
        while True:
            cmd = ["ffmpeg", "-nostdin", "-loglevel", "error", *([] if args.fast else ["-re"]), "-i", args.file,
                   "-vf", f"{vf},fps={args.fps}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE)
            frame_bytes, t0, n = w * h * 3, time.monotonic(), 0
            try:
                while True:
                    buf = proc.stdout.read(frame_bytes)
                    if len(buf) < frame_bytes:
                        break
                    screen.show(Image.frombytes("RGB", (w, h), buf), force=True)
                    n += 1
            except KeyboardInterrupt:
                proc.terminate()
                return 0
            finally:
                proc.wait()
            log.info("played %d frames at %.1f fps", n, n / max(time.monotonic() - t0, 1e-6))
            if not args.loop:
                break
    return 0


# ---------------------------------------------------------------- service
def cmd_service(args) -> int:
    from . import service

    if args.action == "install":
        err = service.install()
        print(err or f"installed and started {service.UNIT_PATH}")
        if not service.udev_installed():
            print("\nTip: also install the udev rule, so the screen works for any user and starts the service on "
                  "plug-in:\n  turzx udev | sudo tee /etc/udev/rules.d/60-turzx.rules >/dev/null && "
                  "sudo udevadm control --reload-rules && sudo udevadm trigger")
        return 1 if err else 0
    if args.action == "uninstall":
        service.uninstall()
        print("service removed")
        return 0
    if args.action in ("start", "stop", "restart"):
        err = getattr(service, args.action)()
        print(err or f"{args.action}: ok")
        return 1 if err else 0
    st = service.status()
    print(f"unit      : {service.UNIT_PATH} ({'installed' if st['installed'] else 'not installed'})")
    print(f"enabled   : {st['enabled']}\nactive    : {st['active']}")
    print(f"udev rule : {service.UDEV_PATH} ({'installed' if service.udev_installed() else 'not installed'})")
    for node, ok in service.device_access():
        print(f"device    : {node} {'accessible' if ok else 'NO PERMISSION'}")
    ds = config.read_status()
    if ds:
        print(f"daemon    : pid {ds['pid']} {ds['state']} {ds.get('model') or ''} layout '{ds.get('layout')}' "
              f"{ds.get('fps')} fps {ds.get('error') or ''}")
    return 0


def cmd_udev(args) -> int:
    from . import service

    sys.stdout.write(service.udev_rule_text())
    return 0


def cmd_gui(args) -> int:
    try:
        from .gui.app import main as gui_main
    except ImportError as e:
        print(f"The GUI needs PySide6: pip install 'turzx[gui]'  ({e})", file=sys.stderr)
        return 2
    return gui_main()


# ------------------------------------------------------------------- parser
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="turzx", description="Drive TURZX / Turing Smart Screen USB displays on Linux.")
    p.add_argument("--version", action="version", version=f"turzx {__version__}")
    p.add_argument("-v", "--verbose", action="count", default=0, help="more logging (-vv for debug)")
    p.add_argument("--pid", type=lambda s: int(s, 16), help="USB product id (hex) when several screens are attached")

    view = argparse.ArgumentParser(add_help=False)
    view.add_argument("-o", "--orientation", choices=[o.value for o in Orientation], help="default: from settings")
    view.add_argument("-q", "--quality", type=int, help="JPEG quality 10..100 (default 90)")
    view.add_argument("-b", "--brightness", type=float, help="backlight 0..100 %%")

    fit = argparse.ArgumentParser(add_help=False)
    fit.add_argument("--fit", default="cover", choices=["cover", "contain", "stretch"])

    loop = argparse.ArgumentParser(add_help=False)
    loop.add_argument("--fps", type=float, help="frames per second")
    loop.add_argument("--on-exit", choices=["off", "blank", "keep"], help="what the screen shows afterwards")

    sub = p.add_subparsers(dest="command", required=True, metavar="COMMAND")

    def add(name, help_, func, parents=()):
        s = sub.add_parser(name, help=help_, parents=list(parents), description=help_)
        s.set_defaults(func=func)
        return s

    add("gui", "open the layout editor", cmd_gui)
    add("daemon", "run the background service (what systemd starts)", cmd_daemon)
    add("layouts", "list built-in and custom layouts", cmd_layouts)
    add("use", "make a layout the active one", cmd_use).add_argument("layout")
    s = add("run", "show a layout in the foreground (pauses the service meanwhile)", cmd_run, [view, loop])
    s.add_argument("layout", nargs="?", default="classic", help="layout id or .json file (default: classic)")
    add("monitor", "alias of `run classic`", cmd_run, [view, loop]).set_defaults(layout="classic")
    s = add("render", "render a layout to an image file", cmd_render)
    s.add_argument("layout")
    s.add_argument("output")
    s.add_argument("--size", help="WxH (default: the layout's canvas)")
    s.add_argument("--warmup", type=float, default=2.0, help="seconds of sensor history first")

    add("info", "list screens and show model, firmware and storage", cmd_info)
    add("brightness", "set the backlight level", cmd_brightness).add_argument("level", type=float, help="0..100")
    add("show", "display an image file", cmd_show, [view, fit]).add_argument("file")
    add("clear", "fill the screen with a colour", cmd_clear).add_argument("--color", default="000000")
    add("off", "blank the screen and turn the backlight off", cmd_off)
    add("test-pattern", "colour bars, greyscale ramp and edge markers", cmd_test_pattern, [view])
    s = add("mirror", "mirror a monitor or desktop region (X11)", cmd_mirror, [view, loop])
    s.add_argument("--display", type=int, default=1, help="monitor number (1 = first, 0 = whole desktop)")
    s.add_argument("--region", help="x,y,w,h in desktop pixels instead of a whole monitor")
    s.add_argument("--fit", default="contain", choices=["cover", "contain", "stretch"])
    s = add("video", "play a video file (decoded on the host)", cmd_video, [view, fit])
    s.add_argument("file")
    s.set_defaults(fps=25.0)
    s.add_argument("--fps", type=float, default=25.0)
    s.add_argument("--loop", action="store_true")
    s.add_argument("--fast", action="store_true", help="do not pace to real time")

    s = add("service", "manage the systemd user service", cmd_service)
    s.add_argument("action", nargs="?", default="status",
                   choices=["status", "install", "uninstall", "start", "stop", "restart"])
    add("udev", "print the udev rule (pipe it into /etc/udev/rules.d/60-turzx.rules)", cmd_udev)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    level = logging.DEBUG if args.verbose >= 2 else logging.INFO
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s: %(message)s" if args.verbose
                        else "%(message)s")
    try:
        return args.func(args)
    except DeviceError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
