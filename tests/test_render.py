import json

import pytest

from turzx.config import preset_dir
from turzx.render import WIDGETS, Renderer
from turzx.render.layout import new_layout

PRESETS = sorted(preset_dir().glob("*.json"))


def test_presets_exist():
    names = {p.stem for p in PRESETS}
    assert {"classic", "gauges", "minimal", "terminal", "graphs", "photo-clock", "neon"} <= names


@pytest.mark.parametrize("path", PRESETS, ids=lambda p: p.stem)
def test_preset_renders(ctx, path):
    layout = json.loads(path.read_text())
    assert all(w["type"] in WIDGETS for w in layout["widgets"])
    r = Renderer(ctx)
    img = r.render(layout, tuple(layout["canvas"]))
    assert img.size == tuple(layout["canvas"]) and img.mode == "RGB"
    assert img.getextrema() != ((0, 0), (0, 0), (0, 0))  # not blank
    # other screen sizes scale the layout
    assert r.render(layout, (1920, 480)).size == (1920, 480)


@pytest.mark.parametrize("type_", sorted(WIDGETS))
def test_every_widget_renders_with_defaults(ctx, type_):
    lay = new_layout("t")
    lay["widgets"] = [WIDGETS[type_].create(10, 10)]
    lay["widgets"][0]["props"]["glow"] = 4.0  # exercise the padded-layer path too
    if type_ == "mirror":
        pytest.importorskip("mss")
        pytest.skip("needs a display")
    Renderer(ctx).render(lay, (800, 400))


def test_broken_widget_does_not_blank_screen(ctx):
    lay = new_layout("t")
    lay["widgets"] = [{"id": "x", "type": "ring", "x": 0, "y": 0, "w": 50, "h": 50, "props": {"metric": 42}},
                      {"id": "y", "type": "nonexistent"}]
    Renderer(ctx).render(lay, (200, 100))


def test_palette_reference(ctx):
    from turzx.render.theme import Palette

    p = Palette({"accent": "#102030"})
    assert p.resolve("@accent") == (0x10, 0x20, 0x30, 255)
    assert p.resolve("@accent/0.5")[3] == 128
    assert p.resolve("#11223344") == (0x11, 0x22, 0x33, 0x44)
