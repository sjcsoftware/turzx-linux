from PIL import Image

from turzx.device import MODELS
from turzx.screen import Orientation, Screen


class FakeDevice:
    def __init__(self, pid=0x0092):
        self.model = MODELS[pid]
        self.frames = []

    def show_native(self, image, quality=90, overlay=False):
        assert image.size == self.model.native
        self.frames.append(image)
        return 0


def test_9_2_geometry():
    m = MODELS[0x0092]
    assert m.native == (464, 1920) and m.visible == (462, 1920)


def test_landscape_mapping():
    dev = FakeDevice()
    s = Screen(dev, Orientation.LANDSCAPE)
    assert s.size == (1920, 462)
    img = Image.new("RGB", s.size)
    img.putpixel((0, 0), (255, 0, 0))  # logical top-left
    s.show(img)
    native = dev.frames[-1]
    # logical (x=0, y=0) lands at native (x=461, y=0): top edge, left end of the panel
    assert native.getpixel((461, 0)) == (255, 0, 0)
    assert native.getpixel((462, 0)) == (0, 0, 0)  # padding column


def test_unchanged_frames_are_skipped():
    dev = FakeDevice()
    s = Screen(dev, Orientation.PORTRAIT)
    img = Image.new("RGB", s.size, (1, 2, 3))
    assert s.show(img) and not s.show(img.copy()) and s.show(img, force=True)
    assert len(dev.frames) == 2
