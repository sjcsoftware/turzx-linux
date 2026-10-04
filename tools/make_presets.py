#!/usr/bin/env python3
"""Generate the built-in layouts in src/turzx/presets/ (run after editing this file).

    python tools/make_presets.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from turzx.render.layout import new_layout  # noqa: E402
from turzx.render.widgets import WIDGETS  # noqa: E402

OUT = ROOT / "src" / "turzx" / "presets"


def W(type_: str, x, y, w, h, name: str | None = None, **props):
    d = WIDGETS[type_].create(x, y, w, h, **props)
    d["id"] = f"{type_}-{len(_ids)}"
    _ids.append(d["id"])
    if name:
        d["name"] = name
    # store only what differs from the defaults, so presets stay readable
    defaults = {f.key: f.default for f in WIDGETS[type_].schema()}
    d["props"] = {k: v for k, v in d["props"].items() if defaults.get(k) != v}
    return d


_ids: list[str] = []


def layout(id_: str, name: str, description: str, widgets: list, canvas=(1920, 462), **extra):
    lay = new_layout(name, canvas)
    lay.update(description=description, author="turzx", **extra)
    lay["widgets"] = widgets
    return id_, lay


# --------------------------------------------------------------------------- classic
def classic():
    m, gap = 14, 14
    weights = [("cpu", 1.0), ("gpu", 1.0), ("memory", 0.62), ("io", 0.62), ("clock", 0.52)]
    avail = 1920 - 2 * m - gap * (len(weights) - 1)
    total = sum(w for _, w in weights)
    x, widgets = float(m), []
    for kind, weight in weights:
        width = avail * weight / total
        widgets.append(W("card", round(x), m, round(width), 462 - 2 * m, kind.upper() if kind != "io" else "NETWORK",
                         kind=kind))
        x += width + gap
    return layout("classic", "Classic Cards",
                  "Five system cards: CPU, GPU, memory, network + disk and a clock. The default.", widgets)


def classic_portrait():
    m, gap = 14, 14
    weights = [("cpu", 1.0), ("gpu", 1.0), ("memory", 0.72), ("io", 1.0), ("clock", 0.8)]
    avail = 1920 - 2 * m - gap * (len(weights) - 1)
    total = sum(w for _, w in weights)
    y, widgets = float(m), []
    for kind, weight in weights:
        height = avail * weight / total
        widgets.append(W("card", m, round(y), 462 - 2 * m, round(height), kind.upper(), kind=kind))
        y += height + gap
    return layout("classic-portrait", "Classic Cards (portrait)",
                  "The classic cards stacked for a screen standing upright.", widgets, canvas=(462, 1920))


# --------------------------------------------------------------------------- gauges
def gauges():
    widgets = [W("shape", 0, 0, 1920, 462, "Backdrop", fill="#0a0d14", fill2="#05070a", radius=0, border_width=0)]
    specs = [
        ("cpu.util", "CPU", "{value:.0f}%", "@cpu", "{cpu.freq:.1f} GHz", 0, 0),
        ("cpu.temp", "CPU °C", "{value:.0f}°", "@cpu", "{cpu.name}", 70, 85),
        ("gpu.util", "GPU", "{value:.0f}%", "@gpu", "{gpu.power:.0f} W", 0, 0),
        ("gpu.temp", "GPU °C", "{value:.0f}°", "@gpu", "{gpu.clock:.2f} GHz", 70, 83),
        ("mem.percent", "RAM", "{value:.0f}%", "@mem", "{mem.used|gib:.1f} / {mem.total|gib:.0f} GB", 85, 95),
        ("gpu.vram_percent", "VRAM", "{value:.0f}%", "@mem", "{gpu.vram_used|gib:.1f} / {gpu.vram_total|gib:.0f} GB",
         85, 95),
    ]
    size, step, x0, y0 = 220, 252, 40, 64
    for i, (metric, label, value, col, sub, warn, hot) in enumerate(specs):
        x = x0 + i * step
        widgets.append(W("ring", x, y0, size, size, label, metric=metric, label=label, value=value, color=col,
                         thickness=18, value_size=56, warn_at=warn, hot_at=hot))
        widgets.append(W("text", x - 10, y0 + size + 22, size + 20, 30, f"{label} detail", text=sub, size=19,
                         weight="semibold", color="@dim", align="center"))
        widgets.append(W("graph", x + 10, y0 + size + 70, size - 20, 60, f"{label} history", metric=metric,
                         color=col, style="area", line_width=2, fill_opacity=0.18, samples=90))
    widgets += [
        W("shape", 1560, 40, 2, 382, "Divider", fill="@card_border", radius=0, border_width=0),
        W("clock", 1590, 70, 300, 130, "Time", text="{time:%H:%M}", size=104),
        W("clock", 1590, 196, 300, 36, "Seconds", text="{time:%S}", size=26, color="@dim", weight="semibold"),
        W("text", 1590, 250, 300, 34, "Day", text="{time:%A}", size=26, align="center", weight="semibold"),
        W("text", 1590, 286, 300, 30, "Date", text="{time:%d %B %Y}", size=20, align="center", color="@dim",
          weight="medium"),
        W("text", 1590, 352, 300, 30, "Net down", text="↓ {net.down|rate}", size=20, align="center",
          color="@net", weight="semibold"),
        W("text", 1590, 384, 300, 30, "Net up", text="↑ {net.up|rate}", size=20, align="center", color="@net_up",
          weight="semibold"),
    ]
    return layout("gauges", "Gauges", "Six ring gauges with history sparklines and a clock.", widgets)


# --------------------------------------------------------------------------- minimal
def minimal():
    widgets = []
    cols = [
        ("CPU", "{cpu.util:.0f}", "{cpu.temp:.0f}°C  ·  {cpu.freq:.1f} GHz", "cpu.util", "@cpu"),
        ("GPU", "{gpu.util:.0f}", "{gpu.temp:.0f}°C  ·  {gpu.power:.0f} W", "gpu.util", "@gpu"),
        ("MEMORY", "{mem.percent:.0f}", "{mem.used|gib:.1f} of {mem.total|gib:.0f} GB", "mem.percent", "@mem"),
    ]
    x0, colw = 70, 400
    for i, (label, value, sub, metric, col) in enumerate(cols):
        x = x0 + i * colw
        widgets += [
            W("text", x, 70, 340, 30, f"{label} label", text=label, size=22, weight="semibold", color="@dim",
              fit=False, valign="top"),
            W("text", x, 110, 340, 180, f"{label} value", text=value + "%", size=190, weight="light",
              color="@text", valign="top"),
            W("bar", x, 320, 320, 4, f"{label} bar", metric=metric, color=col, radius=2),
            W("text", x, 346, 340, 30, f"{label} detail", text=sub, size=20, weight="medium", color="@dim",
              fit=False, valign="top"),
        ]
    widgets += [
        W("clock", 1330, 70, 520, 220, "Time", text="{time:%H:%M}", size=200, weight="light", align="right",
          valign="top"),
        W("text", 1330, 320, 520, 34, "Date", text="{time:%A, %d %B}", size=26, weight="regular", align="right",
          color="@dim", valign="top"),
        W("text", 1330, 360, 520, 30, "Net", text="↓ {net.down|rate}   ↑ {net.up|rate}", size=20, weight="medium",
          align="right", color="@dim", valign="top"),
    ]
    return layout("minimal", "Minimal", "Big light numerals on pure black, thin accent bars.", widgets,
                  palette={"background": "#000000", "dim": "#6b7280", "text": "#f5f5f5"})


# --------------------------------------------------------------------------- terminal
def terminal():
    G, DIM = "#39ff7a", "#1f8f47"
    mono = "JetBrains Mono, Ubuntu Mono, DejaVu Sans Mono"
    glow = {"glow": 5, "glow_color": "#39ff7a66"}

    def t(x, y, w, h, text, size=34, color=G, name=None, **kw):
        return W("text", x, y, w, h, name or text[:20], text=text, size=size, color=color, family=mono,
                 weight="medium", fit=False, valign="top", **glow, **kw)

    def bar(x, y, metric, name):
        return W("bar", x, y, 520, 30, name, metric=metric, color=G, track="#0f2a18", segments=26, gap=4, radius=1,
                 warn_at=75, warn_color="#e8ff5a", hot_at=90, hot_color="#ff5a5a", **glow)

    widgets = [
        W("shape", 0, 0, 1920, 462, "Screen", fill="#020a04", fill2="#000000", radius=0, border_width=0),
        t(40, 26, 1300, 40, "{host.name}:~$ turzx status --live", 30, DIM, "Prompt"),
        t(1540, 26, 340, 40, "{time:%H:%M:%S}", 30, DIM, "Clock", align="right"),
        t(40, 96, 120, 44, "CPU", name="CPU label"), bar(170, 104, "cpu.util", "CPU bar"),
        t(720, 96, 1160, 44, "{cpu.util:5.1f}%  {cpu.temp:3.0f}°C  {cpu.freq:.2f}GHz  load {cpu.load1:.2f}",
          name="CPU values"),
        t(40, 166, 120, 44, "GPU", name="GPU label"), bar(170, 174, "gpu.util", "GPU bar"),
        t(720, 166, 1160, 44, "{gpu.util:5.1f}%  {gpu.temp:3.0f}°C  {gpu.power:4.0f}W   vram {gpu.vram_percent:.0f}%",
          name="GPU values"),
        t(40, 236, 120, 44, "MEM", name="MEM label"), bar(170, 244, "mem.percent", "MEM bar"),
        t(720, 236, 1160, 44, "{mem.percent:5.1f}%  {mem.used|gib:.1f}/{mem.total|gib:.0f}G  swap {swap.percent:.0f}%",
          name="MEM values"),
        t(40, 306, 120, 44, "DSK", name="DSK label"), bar(170, 314, "disk.percent", "DSK bar"),
        t(720, 306, 1160, 44, "{disk.percent:5.1f}%  {disk.temp:3.0f}°C  r {disk.read|bytes}/s  w {disk.write|bytes}/s",
          name="DSK values"),
        t(40, 380, 1840, 44, "NET  ↓ {net.down|rate}   ↑ {net.up|rate}     up {host.uptime|duration}     {host.kernel}",
          30, DIM, "Footer"),
    ]
    return layout("terminal", "Terminal", "Phosphor-green monospace readout with segmented bars and CRT glow.",
                  widgets, palette={"background": "#000000", "accent": G, "text": G, "dim": DIM, "track": "#0f2a18"},
                  font=mono)


# --------------------------------------------------------------------------- graphs
def graphs():
    widgets = []
    m, gap = 14, 14
    pw = (1920 - 2 * m - 2 * gap) / 3
    panels = [
        ("CPU", "cpu.util", "", "{cpu.util:.0f}%", "{cpu.temp:.0f}°C · {cpu.freq:.1f} GHz", "@cpu", 100, 0),
        ("GPU", "gpu.util", "", "{gpu.util:.0f}%", "{gpu.temp:.0f}°C · {gpu.power:.0f} W", "@gpu", 100, 0),
        ("NETWORK", "net.down", "net.up", "↓ {net.down|rate}", "↑ {net.up|rate}", "@net", 0, 125000),
    ]
    for i, (title, metric, metric2, value, sub, col, vmax, floor) in enumerate(panels):
        x = round(m + i * (pw + gap))
        w = round(pw)
        widgets += [
            W("shape", x, m, w, 462 - 2 * m, f"{title} panel"),
            W("text", x + 26, m + 22, 300, 30, f"{title} title", text=title, size=22, weight="bold", fit=False,
              valign="top"),
            W("text", x + w - 26 - 380, m + 14, 380, 66, f"{title} value", text=value, size=52, weight="bold",
              align="right", valign="top"),
            W("text", x + w - 26 - 380, m + 80, 380, 28, f"{title} detail", text=sub, size=19, weight="medium",
              color="@dim", align="right", valign="top", fit=False),
            W("graph", x + 2, m + 130, w - 4, 462 - 2 * m - 132, f"{title} graph", metric=metric, metric2=metric2,
              color=col, max=vmax, floor=floor, samples=240, grid=3, line_width=2.5, fill_opacity=0.22),
        ]
    return layout("graphs", "Graphs", "Three large live charts: CPU, GPU and network throughput.", widgets)


# --------------------------------------------------------------------------- photo clock
def photo():
    widgets = [
        W("clock", 70, 40, 900, 260, "Time", text="{time:%H:%M}", size=250, weight="bold", align="left",
          valign="top", stroke=0),
        W("text", 80, 300, 900, 50, "Date", text="{time:%A, %d %B %Y}", size=40, weight="medium", fit=False,
          valign="top", color="#ffffffcc"),
        W("shape", 1330, 60, 530, 342, "Glass panel", fill="#ffffff1f", border_color="#ffffff33", radius=28),
        W("stat", 1370, 96, 220, 90, "CPU", label="CPU", value="{cpu.util:.0f}%", label_color="#ffffffaa",
          value_size=46, metric="cpu.util", warn_at=75, hot_at=90),
        W("stat", 1620, 96, 220, 90, "GPU", label="GPU", value="{gpu.util:.0f}%", label_color="#ffffffaa",
          value_size=46, metric="gpu.util", warn_at=75, hot_at=90),
        W("stat", 1370, 214, 220, 90, "CPU temp", label="CPU TEMP", value="{cpu.temp:.0f}°C",
          label_color="#ffffffaa", value_size=46, metric="cpu.temp", warn_at=75, hot_at=88),
        W("stat", 1620, 214, 220, 90, "RAM", label="MEMORY", value="{mem.percent:.0f}%", label_color="#ffffffaa",
          value_size=46),
        W("text", 1370, 326, 470, 22, "Now playing label", text="{media.status|upper}", size=15,
          weight="semibold", color="#ffffffaa", valign="top", fit=False, hide_missing=True),
        W("text", 1370, 348, 470, 34, "Now playing", text="{media.title} · {media.artist}", size=22,
          weight="semibold", color="#ffffff", valign="top", hide_missing=True),
    ]
    return layout("photo-clock", "Photo Clock",
                  "A big clock and a glass stats panel over a gradient. Set your own photo as the background.",
                  widgets, palette={"text": "#ffffff", "dim": "#ffffffaa"},
                  background={"color": "#3b1d6e", "color2": "#0e3a5f", "vertical": False, "image": "",
                              "fit": "cover", "dim": 0.25})


# --------------------------------------------------------------------------- neon
def neon():
    P, C, Y = "#ff3df2", "#22e4ff", "#fff35c"
    glow = 10
    widgets = [
        W("ring", 40, 41, 380, 380, "CPU ring", metric="cpu.util", label="CPU", value="{value:.0f}%", color=P,
          track="#2a1238", thickness=26, value_size=96, label_size=22, glow=glow, glow_color=P + "aa"),
        W("ring", 440, 41, 380, 380, "GPU ring", metric="gpu.util", label="GPU", value="{value:.0f}%", color=C,
          track="#0e2a38", thickness=26, value_size=96, label_size=22, glow=glow, glow_color=C + "aa"),
        W("clock", 860, 40, 620, 200, "Time", text="{time:%H:%M}", size=190, color="#ffffff", glow=14,
          glow_color=P + "cc"),
        W("text", 860, 236, 620, 40, "Date", text="{time:%A  %d.%m.%Y}", size=30, align="center", color=C,
          weight="semibold", glow=6, glow_color=C + "99"),
        W("graph", 880, 300, 580, 120, "CPU+GPU graph", metric="cpu.util", metric2="gpu.util", color=P, color2=C,
          max=100, style="line", line_width=3, glow=6, glow_color="#ffffff55"),
        W("stat", 1520, 50, 360, 90, "CPU temp", label="CPU TEMP", value="{cpu.temp:.0f}°C", color=P,
          label_color="#9b8cff", value_size=48, glow=6, glow_color=P + "88"),
        W("stat", 1520, 150, 360, 90, "GPU temp", label="GPU TEMP", value="{gpu.temp:.0f}°C", color=C,
          label_color="#9b8cff", value_size=48, glow=6, glow_color=C + "88"),
        W("stat", 1520, 250, 360, 90, "RAM", label="MEMORY", value="{mem.used|gib:.1f} GB", color=Y,
          label_color="#9b8cff", value_size=48, glow=6, glow_color=Y + "88"),
        W("text", 1520, 360, 360, 40, "Net", text="↓ {net.down|rate}  ↑ {net.up|rate}", size=22, color="#9b8cff",
          weight="semibold", valign="top"),
    ]
    return layout("neon", "Neon", "Glowing synthwave gauges and chart on deep indigo.", widgets,
                  palette={"background": "#0b0618", "text": "#ffffff", "dim": "#9b8cff", "accent": P},
                  background={"color": "#120a2a", "color2": "#05030d", "vertical": True, "image": "", "fit": "cover",
                              "dim": 0.0})


PRESETS = [classic, gauges, minimal, terminal, graphs, photo, neon, classic_portrait]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("*.json"):
        old.unlink()
    for i, fn in enumerate(PRESETS):
        _ids.clear()
        id_, lay = fn()
        lay["order"] = i
        (OUT / f"{id_}.json").write_text(json.dumps(lay, indent=2, ensure_ascii=False) + "\n")
        print("wrote", id_)


if __name__ == "__main__":
    main()
