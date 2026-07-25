"""Home Assistant integration: fetch sensor states via the REST API and
render an e-ink friendly layout (800x480) with Pillow.

Configuration via environment variables:
    HA_URL=http://192.168.0.172:8123
    HA_TOKEN=<long-lived access token>

The REST API endpoint used is ``/api/states`` which returns the state of
every entity. We then auto-discover temperature/humidity sensors and any
weather entity and lay them out in a simple grid.
"""
import json
import logging
import os
import urllib.error
import urllib.request
from datetime import datetime

from PIL import Image, ImageDraw, ImageFont

log = logging.getLogger("ha")

HA_URL = os.environ.get("HA_URL", "").rstrip("/")
HA_TOKEN = os.environ.get("HA_TOKEN", "")

_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed-Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/arial.ttf",
]

_font_cache = {}


def _font(size):
    if size in _font_cache:
        return _font_cache[size]
    for p in _FONT_CANDIDATES:
        try:
            f = ImageFont.truetype(p, size)
            _font_cache[size] = f
            return f
        except Exception:
            continue
    f = ImageFont.load_default()
    _font_cache[size] = f
    return f


def configured():
    return bool(HA_URL and HA_TOKEN)


def get_states():
    """Return the full ``/api/states`` payload from Home Assistant.

    Raises RuntimeError if HA is not configured, or urllib errors on failure.
    """
    if not configured():
        raise RuntimeError("HA_URL and HA_TOKEN env vars must be set")
    req = urllib.request.Request(
        f"{HA_URL}/api/states",
        headers={
            "Authorization": f"Bearer {HA_TOKEN}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())


def _is_number(v):
    try:
        float(v)
        return True
    except (TypeError, ValueError):
        return False


def pick_entities(states):
    """Discover temperature/humidity sensors and weather entities.

    Returns ``(temps, humids, weathers)`` sorted by friendly name.
    """
    temps, humids, weathers = [], [], []
    for s in states:
        eid = s.get("entity_id", "")
        attrs = s.get("attributes", {}) or {}
        state = s.get("state")
        dc = attrs.get("device_class")
        if eid.startswith("weather."):
            weathers.append(s)
        elif dc == "temperature" and _is_number(state):
            temps.append(s)
        elif dc == "humidity" and _is_number(state):
            humids.append(s)

    def name(s):
        return (s.get("attributes", {}) or {}).get("friendly_name", s.get("entity_id", ""))

    temps.sort(key=name)
    humids.sort(key=name)
    weathers.sort(key=name)
    return temps, humids, weathers


WEATHER_GLYPH = {
    "clear": "SUN",
    "cloudy": "CLOUD",
    "rain": "RAIN",
    "snow": "SNOW",
    "thunder": "STORM",
    "fog": "FOG",
    "windy": "WIND",
}


def _weather_label(condition):
    c = (condition or "").lower()
    for k, v in WEATHER_GLYPH.items():
        if k in c:
            return v
    return (condition or "—").upper()


# E-ink palette-friendly colors (RGB)
WHITE = (255, 255, 255)
BLACK = (0, 0, 0)
BLUE = (0, 0, 255)
RED = (255, 0, 0)
YELLOW = (255, 255, 0)
GREEN = (0, 255, 0)


def render(states, width=800, height=480):
    """Render an 800x480 RGB image summarizing Home Assistant sensor state."""
    img = Image.new("RGB", (width, height), WHITE)
    d = ImageDraw.Draw(img)

    temps, humids, weathers = pick_entities(states)

    # Header band
    d.rectangle([0, 0, width, 70], fill=BLACK)
    d.text((24, 16), "HOME ASSISTANT", fill=WHITE, font=_font(34))
    now = datetime.now().strftime("%a %b %d  %H:%M")
    tw = d.textlength(now, font=_font(28))
    d.text((width - tw - 24, 20), now, fill=WHITE, font=_font(28))

    y = 90

    # Weather block (first weather entity)
    if weathers:
        wx = weathers[0]
        attrs = wx.get("attributes", {}) or {}
        cond = wx.get("state", "")
        temp = attrs.get("temperature", "")
        hum = attrs.get("humidity", "")
        label = _weather_label(cond)

        d.rounded_rectangle([20, y, width - 20, y + 110], radius=14,
                            outline=BLACK, width=3, fill=WHITE)
        d.text((40, y + 12), label, fill=YELLOW, font=_font(46))
        parts = []
        if temp != "":
            parts.append(f"{temp}°C")
        if hum != "":
            parts.append(f"{hum}% RH")
        if parts:
            tw = d.textlength("  ".join(parts), font=_font(32))
            d.text((width - tw - 40, y + 24), "  ".join(parts),
                   fill=BLUE, font=_font(32))
        y += 130

    # Sensor grid: 2 columns of cards
    cards = []
    for s in temps[:8]:
        cards.append(("T", s))
    for s in humids[:8]:
        if not any(c[1]["entity_id"] == s["entity_id"] for c in cards):
            cards.append(("H", s))

    col_w = (width - 60) // 2
    card_h = 95
    x0 = 20
    for i, (kind, s) in enumerate(cards[:8]):
        col = i % 2
        row = i // 2
        x = x0 + col * (col_w + 20)
        cy = y + row * (card_h + 15)
        if cy + card_h > height - 40:
            break
        d.rounded_rectangle([x, cy, x + col_w, cy + card_h], radius=12,
                            outline=BLACK, width=2, fill=WHITE)
        attrs = s.get("attributes", {}) or {}
        fname = attrs.get("friendly_name", s.get("entity_id", ""))
        if "." in fname:
            fname = fname.split(".")[-1]
        d.text((x + 16, cy + 10), fname[:26].title(), fill=BLACK, font=_font(22))
        unit = attrs.get("unit_of_measurement", "")
        val = s.get("state", "")
        val_text = f"{val}"
        if unit:
            val_text += f" {unit}"
        color = RED if kind == "T" else BLUE
        d.text((x + 16, cy + 40), val_text, fill=color, font=_font(42))

    # Footer
    d.text((24, height - 28), "via Home Assistant REST API", fill=BLACK, font=_font(16))
    return img


def fetch_and_render(width=800, height=480):
    """Convenience: call the HA API, then render. Returns the PIL image."""
    states = get_states()
    return render(states, width=width, height=height)