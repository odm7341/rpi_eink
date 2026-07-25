"""Home Assistant integration: fetch sensor states via the REST API and
render an e-ink friendly layout (800x480) with Pillow.

Configuration via environment variables:
    HA_URL=http://192.168.0.172:8123
    HA_TOKEN=<long-lived access token>

The REST API endpoint used is ``/api/states`` which returns the state of
every entity. We then auto-discover temperature/humidity sensors and any
weather entity and lay them out in a simple grid.
"""
import io
import json
import logging
import math
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone, timedelta

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


# --- Dashboard: chart + camera snapshot -------------------------------------

# Entities for the chart. Override via env if needed.
CHART_ENTITIES = [
    ("sensor.basement_indoor_temperature", "Basement T", RED),
    ("sensor.spare_temp", "Bt-prox T", YELLOW),
    ("sensor.basement_humidity", "Basement H", BLUE),
    ("sensor.spare_humidity", "Bt-prox H", GREEN),
]
CHART_HOURS = int(os.environ.get("HA_CHART_HOURS", "12"))
SNAPSHOT_ENTITY = os.environ.get("HA_SNAPSHOT_ENTITY", "image.frontlawn_person")
MOTION_ENTITY = os.environ.get("HA_MOTION_ENTITY", "binary_sensor.frontlawn_motion")


def _ha_get(path, binary=False, timeout=20):
    """GET an HA API path. Returns bytes (binary=True) or parsed JSON."""
    req = urllib.request.Request(
        f"{HA_URL}{path}",
        headers={"Authorization": f"Bearer {HA_TOKEN}"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read()
    if binary:
        return data
    return json.loads(data.decode())


def get_history(entity_ids, hours=CHART_HOURS):
    """Return ``{entity_id: [(ts_iso, value), ...]}`` from HA history."""
    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=hours)
    f = ",".join(entity_ids)
    url = (
        f"/api/history/period/{start.strftime('%Y-%m-%dT%H:%M:%S')}"
        f"?filter_entity_id={f}&minimal_response&end_time="
        f"{end.strftime('%Y-%m-%dT%H:%M:%S')}"
    )
    payload = _ha_get(url)
    out = {}
    for group in payload:
        if not group:
            continue
        eid = group[0].get("entity_id")
        pts = []
        for s in group:
            v = s.get("state")
            try:
                v = float(v)
            except (TypeError, ValueError):
                continue
            pts.append((s.get("last_changed", ""), v))
        out[eid] = pts
    return out


def get_detection_snapshot():
    """Return ``(PIL.Image, last_changed_iso)`` or ``(None, None)`` if empty."""
    try:
        data = _ha_get(f"/api/image_proxy/{SNAPSHOT_ENTITY}", binary=True, timeout=20)
        if not data:
            return None, None
        img = Image.open(io.BytesIO(data)).convert("RGB")
    except Exception as exc:  # noqa: BLE001
        log.warning("snapshot fetch failed: %r", exc)
        return None, None
    # The state (last_changed timestamp) comes from /api/states
    ts = None
    try:
        states = _ha_get("/api/states")
        for s in states:
            if s.get("entity_id") == SNAPSHOT_ENTITY:
                ts = s.get("state") or ""
                break
    except Exception:  # noqa: BLE001
        pass
    return img, ts


def _fmt_ts(ts_iso):
    """Render an HA ISO timestamp (e.g. ``2026-07-25T00:40:58.111168``) short."""
    if not ts_iso:
        return "—"
    try:
        dt = datetime.fromisoformat(ts_iso.replace("Z", ""))
        return dt.strftime("%a %d  %H:%M")
    except Exception:  # noqa: BLE001
        return ts_iso[:19]


X_LEFT = 500  # chart width


def _draw_legend(d, x, y, entities):
    """Draw a small color-key legend starting at (x,y)."""
    for i, (eid, label, color) in enumerate(entities):
        ly = y + i * 22
        d.line([x, ly + 8, x + 24, ly + 8], fill=color, width=3)
        d.text((x + 32, ly), label, fill=BLACK, font=_font(18))


def render_chart(history, x0, y0, w, h):
    """Render a dual-y-axis line chart of CHART_ENTITIES onto a new (w,h)
    image and return it (caller pastes at x0,y0)."""
    img = Image.new("RGB", (w, h), WHITE)
    d = ImageDraw.Draw(img)

    temps = [(label, color, history.get(eid, []))
             for eid, label, color in CHART_ENTITIES if "T" in label]
    humids = [(label, color, history.get(eid, []))
              for eid, label, color in CHART_ENTITIES if "H" in label]

    plot_x0, plot_y0 = 48, 36
    plot_w = w - 48 - 48
    plot_h = h - 36 - 36

    d.rectangle([plot_x0, plot_y0, plot_x0 + plot_w, plot_y0 + plot_h],
                outline=BLACK, width=2)

    def allvals(series):
        return [v for _, _, pts in series for _, v in pts]

    t_vals = allvals(temps)
    h_vals = allvals(humids)

    t_min, t_max = (min(t_vals), max(t_vals)) if t_vals else (0, 1)
    h_min, h_max = (min(h_vals), max(h_vals)) if h_vals else (0, 1)
    if t_max == t_min:
        t_max += 1
    if h_max == h_min:
        h_max += 1

    now = datetime.now(timezone.utc)
    start = now - timedelta(hours=CHART_HOURS)
    span = (now - start).total_seconds() or 1

    def x_for(ts_iso):
        try:
            dt = datetime.fromisoformat(ts_iso.replace("Z", ""))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        except Exception:  # noqa: BLE001
            return None
        frac = (dt - start).total_seconds() / span
        if frac < 0 or frac > 1:
            return None
        return plot_x0 + frac * plot_w

    def y_temp(v):
        return plot_y0 + plot_h * (1 - (v - t_min) / (t_max - t_min))

    def y_hum(v):
        return plot_y0 + plot_h * (1 - (v - h_min) / (h_max - h_min))

    # Gridlines (3 horizontal)
    for i in range(1, 3):
        gy = plot_y0 + i * plot_h / 3
        for gx in range(plot_x0, plot_x0 + plot_w, 8):
            d.line([gx, gy, gx + 4, gy], fill=BLACK, width=1)

    # Y axis labels (left: temp, right: humidity %)
    d.text((2, plot_y0 - 4), f"{t_max:.1f}", fill=BLACK, font=_font(14))
    d.text((2, plot_y0 + plot_h - 12), f"{t_min:.1f}", fill=BLACK, font=_font(14))
    d.text((plot_x0 + plot_w + 6, plot_y0 - 4), f"{h_max:.0f}%", fill=BLACK, font=_font(14))
    d.text((plot_x0 + plot_w + 6, plot_y0 + plot_h - 12), f"{h_min:.0f}%", fill=BLACK, font=_font(14))

    # X axis labels (start & end hours)
    d.text((plot_x0, plot_y0 + plot_h + 6), start.strftime("%H:00"),
           fill=BLACK, font=_font(14))
    d.text((plot_x0 + plot_w - 24, plot_y0 + plot_h + 6), "now",
           fill=BLACK, font=_font(14))

    # Plot lines
    for series, yfn in ((temps, y_temp), (humids, y_hum)):
        for label, color, pts in series:
            if len(pts) < 2:
                continue
            xy = []
            for ts_iso, v in pts:
                x = x_for(ts_iso)
                if x is None:
                    continue
                xy.append((x, yfn(v)))
            if len(xy) >= 2:
                d.line(xy, fill=color, width=2)
                # Mark the latest point with a dot
                lx, ly = xy[-1]
                d.ellipse([lx - 3, ly - 3, lx + 3, ly + 3], fill=color)

    # Title
    d.text((plot_x0, 6), f"Last {CHART_HOURS}h", fill=BLACK, font=_font(20))

    # Legend
    _draw_legend(d, plot_x0 + plot_w - 130, 6, CHART_ENTITIES)
    return img


def render_combined(width=800, height=480):
    """Render the full dashboard: chart on the left half, latest camera
    detection snapshot + timestamp on the right half."""
    img = Image.new("RGB", (width, height), WHITE)
    d = ImageDraw.Draw(img)

    # Header
    d.rectangle([0, 0, width, 50], fill=BLACK)
    d.text((16, 10), "FRONTLAWN E-INK", fill=WHITE, font=_font(26))
    ts_now = datetime.now().strftime("%a %d  %H:%M")
    tw = d.textlength(ts_now, font=_font(22))
    d.text((width - tw - 16, 14), ts_now, fill=WHITE, font=_font(22))

    # Chart (left)
    chart_x = 0
    chart_y = 56
    chart_w = X_LEFT
    chart_h = height - chart_y - 8
    entity_ids = [eid for eid, _, _ in CHART_ENTITIES]
    try:
        history = get_history(entity_ids)
    except Exception as exc:  # noqa: BLE001
        log.exception("history fetch failed")
        history = {}
    chart_img = render_chart(history, chart_x, chart_y, chart_w, chart_h)
    img.paste(chart_img, (chart_x, chart_y))

    # Snapshot (right)
    snap_x = X_LEFT + 8
    snap_y = 60
    snap_w = width - snap_x - 8
    snap_h = height - snap_y - 8

    d.text((snap_x, snap_y), "Latest detection", fill=BLACK, font=_font(20))
    snap_img, ts = get_detection_snapshot()
    caption_y = snap_y + 28
    if snap_img is not None:
        # Fit inside (snap_w, snap_h - 60) leaving room for timestamp
        target_w = snap_w
        target_h = snap_h - 60
        sw, sh = snap_img.size
        scale = min(target_w / sw, target_h / sh)
        nw, nh = int(sw * scale), int(sh * scale)
        snap_img = snap_img.resize((nw, nh), Image.LANCZOS)
        px = snap_x + (snap_w - nw) // 2
        py = caption_y
        d.rectangle([px - 2, py - 2, px + nw + 2, py + nh + 2],
                    outline=BLACK, width=2)
        img.paste(snap_img, (px, py))
        d.text((snap_x, py + nh + 6), _fmt_ts(ts), fill=BLUE,
               font=_font(18))
    else:
        d.text((snap_x, caption_y), "No snapshot", fill=RED, font=_font(22))

    return img


def fetch_and_render_dashboard(width=800, height=480):
    """Fetch history + snapshot from HA and render the combined dashboard."""
    return render_combined(width=width, height=height)