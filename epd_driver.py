"""Wrapper around the Waveshare 7.3inch e-Paper HAT (E) driver.

Provides a single :func:`get_epd` factory that returns the real hardware
driver on a Raspberry Pi, and a no-op :class:`MockEPD` everywhere else (or
when the ``EPD_MOCK`` environment variable is set). This lets the web app
run and be tested on any machine without the e-Paper HAT attached.

The real driver comes from the vendored Waveshare ``e-Paper`` git submodule
at ``e-Paper/RaspberryPi_JetsonNano/python/lib``.
"""

import logging
import os
import sys

log = logging.getLogger("epd")

# Native resolution of the 7.3inch e-Paper HAT (E).
EPD_WIDTH = 800
EPD_HEIGHT = 480

# 7-color palette used by the panel (BGR byte order in the driver).
# Indexes 0-6 are the usable colors; index 4 is an unused duplicate black.
PALETTE = (
    0, 0, 0,       # 0 black
    255, 255, 255, # 1 white
    255, 255, 0,   # 2 yellow
    255, 0, 0,     # 3 red
    0, 0, 0,       # 4 (unused)
    0, 0, 255,     # 5 blue
    0, 255, 0,     # 6 green
) + (0, 0, 0) * 249

# Driver's packed nibble mapping: palette index -> 4-bit color value
# sent to the display. 0/4 both map to black; 1=white, 2=yellow, 3=red,
# 5=blue, 6=green.
PALETTE_TO_NIBBLE = {0: 0, 1: 2, 2: 4, 3: 6, 4: 1, 5: 3, 6: 5}


def pack_buffer(image, width=EPD_WIDTH, height=EPD_HEIGHT, dither=None):
    """Convert an RGB image into the panel's 4-bit packed buffer.

    This is the same packing performed by the Waveshare driver, but with
    an optional dither setting. By default it matches the driver (Floyd-
    Steinberg dithering) so uploaded photos look smooth. Pass
    ``dither=Image.Dither.NONE`` for solid, non-dithered dashboard colors.
    """
    from PIL import Image

    pal_image = Image.new("P", (1, 1))
    pal_image.putpalette(PALETTE)

    imwidth, imheight = image.size
    if imwidth == width and imheight == height:
        image_temp = image
    elif imwidth == height and imheight == width:
        image_temp = image.rotate(90, expand=True)
    else:
        log.warning("Invalid image dimensions: %dx%d, expected %dx%d",
                    imwidth, imheight, width, height)
        image_temp = image

    kwargs = {"palette": pal_image}
    if dither is not None:
        kwargs["dither"] = dither
    image_7color = image_temp.convert("RGB").quantize(**kwargs)
    pixels = image_7color.load()
    buf = [0x00] * (width * height // 2)
    for y in range(height):
        for x in range(width):
            val = PALETTE_TO_NIBBLE.get(pixels[x, y], 0)
            if (x % 2) == 0:
                buf[x // 2 + y * width // 2] = val << 4
            else:
                buf[x // 2 + y * width // 2] |= val
    return buf


class MockEPD:
    """Software stand-in for the hardware driver.

    Mirrors the API of ``waveshare_epd.epd7in3e.EPD`` and uses the same
    non-dithering buffer packing so off-device previews match the panel.
    """

    def __init__(self):
        self.width = EPD_WIDTH
        self.height = EPD_HEIGHT
        # Expose the named colors like the real driver for convenience.
        self.BLACK = 0x000000
        self.WHITE = 0xFFFFFF
        self.YELLOW = 0x00FFFF
        self.RED = 0x0000FF
        self.BLUE = 0xFF0000
        self.GREEN = 0x00FF00

    def init(self):
        log.info("[mock] EPD init")
        return 0

    def getbuffer(self, image):
        return pack_buffer(image, self.width, self.height)

    def display(self, image):
        log.info("[mock] display() called (%d bytes)", len(image))

    def Clear(self, color=0x11):
        log.info("[mock] Clear(%#x)", color)

    def sleep(self):
        log.info("[mock] EPD sleep")


def _mock_requested():
    return os.environ.get("EPD_MOCK", "").lower() in ("1", "true", "yes", "on")


def get_epd():
    """Return an EPD-like object, real hardware if available else a mock."""
    if _mock_requested():
        log.info("EPD_MOCK set; using MockEPD")
        return MockEPD()

    try:
        here = os.path.dirname(os.path.abspath(__file__))
        lib = os.path.join(here, "e-Paper", "RaspberryPi_JetsonNano", "python", "lib")
        if lib not in sys.path:
            sys.path.insert(0, lib)
        from waveshare_epd import epd7in3e
        epd = epd7in3e.EPD()
        log.info("Using hardware EPD (waveshare_epd.epd7in3e)")
        return epd
    except Exception as exc:  # noqa: BLE001 - fall back to mock off-device
        log.warning("Could not load hardware EPD driver (%r); falling back to MockEPD", exc)
        return MockEPD()