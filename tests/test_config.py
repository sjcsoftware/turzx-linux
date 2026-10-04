import time


def test_settings_roundtrip(xdg):
    s = xdg.Settings.load()
    assert s.layout == "classic"
    s.brightness = 42
    s.save()
    assert xdg.Settings.load().brightness == 42


def test_night_mode(xdg):
    s = xdg.Settings(brightness=80, night_enabled=True, night_start="23:00", night_end="07:00", night_brightness=5)
    at = lambda h, m: time.mktime((2026, 1, 1, h, m, 0, 0, 0, -1))  # noqa: E731
    assert s.effective_brightness(at(23, 30)) == 5
    assert s.effective_brightness(at(3, 0)) == 5
    assert s.effective_brightness(at(12, 0)) == 80


def test_user_layouts(xdg):
    lay = xdg.blank_layout("My Thing", (1920, 462))
    lid = xdg.save_user_layout(lay)
    assert lid == "my-thing"
    assert xdg.unique_id("My Thing") == "my-thing-2"
    assert xdg.find_layout(lid) and not xdg.find_layout(lid).builtin
    assert xdg.load_layout(lid)["name"] == "My Thing"
    ids = [r.id for r in xdg.list_layouts()]
    assert ids[0] == "classic" and lid in ids
    xdg.delete_user_layout(lid)
    assert xdg.find_layout(lid) is None


def test_preview_expires(xdg):
    xdg.write_preview({"name": "p", "widgets": []}, ttl=60)
    assert xdg.read_preview()["name"] == "p"
    xdg.write_preview({"name": "p", "widgets": []}, ttl=-1)
    assert xdg.read_preview() is None
