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

# Modern dashboard theme (preview-friendly; e-Paper will quantize)
THEME = {
    "bg": (245, 245, 245),
    "card": (255, 255, 255),
    "text": (30, 33, 41),
    "muted": (100, 104, 112),
    "header_bg": (30, 58, 138),
    "header_text": (255, 255, 255),
    "border": (200, 204, 211),
    "grid": (225, 228, 232),
    "caption_bg": (30, 58, 138),
    "caption_text": (255, 255, 255),
}

# Brighter, more pleasing data colors (still quantize to EPD palette)
RED_T = (220, 38, 38)
YELLOW_T = (234, 179, 8)
BLUE_T = (37, 99, 235)
GREEN_T = (34, 197, 94)


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

# Chart entities. Override via env if needed.
CHART_ENTITIES = [
    ("sensor.basement_indoor_temperature", "Bsmt", RED_T),
    ("sensor.spare_temp", "Bt-px", YELLOW_T),
    ("sensor.basement_humidity", "Bsmt", BLUE_T),
    ("sensor.spare_humidity", "Bt-px", GREEN_T),
]
CHART_HOURS = int(os.environ.get("HA_CHART_HOURS", "12"))
SNAPSHOT_ENTITY = os.environ.get("HA_SNAPSHOT_ENTITY", "image.frontlawn_person")
MOTION_ENTITY = os.environ.get("HA_MOTION_ENTITY", "binary_sensor.frontlawn_person_occupancy")


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


def _resample_history(history, hours=CHART_HOURS):
    """Average each entity's history into one point per hour.

    Returns ``{entity_id: [(bucket_index, value), ...]}`` for the last
    ``hours`` buckets, with values forward-filled when an hour is empty.
    """
    now = datetime.now(timezone.utc)
    buckets = {i: now - timedelta(hours=i) for i in range(hours - 1, -1, -1)}
    out = {}
    for eid, pts in history.items():
        bucket_vals = {i: [] for i in range(hours)}
        for ts_iso, v in pts:
            try:
                dt = datetime.fromisoformat(ts_iso.replace("Z", ""))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
            except Exception:  # noqa: BLE001
                continue
            idx = int((now - dt).total_seconds() // 3600)
            if 0 <= idx < hours:
                bucket_vals[hours - 1 - idx].append(v)
        # average each bucket, forward-fill empties
        res = []
        last = None
        for i in range(hours):
            vals = bucket_vals[i]
            val = sum(vals) / len(vals) if vals else last
            if val is not None:
                last = val
            res.append((i, val))
        out[eid] = res
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
    if not ts_iso:
        return "—"
    try:
        dt = datetime.fromisoformat(ts_iso.replace("Z", ""))
        return dt.strftime("%a %d %H:%M").replace(" 0", " ")
    except Exception:  # noqa: BLE001
        return ts_iso[:19]


def _round_range(vmin, vmax):
    """Pad min/max slightly and return to one decimal."""
    span = (vmax - vmin) or 1
    pad = span * 0.08
    return max(vmin - pad, 0), vmax + pad


def _draw_grid(d, plot_x0, plot_y0, plot_w, plot_h, vmin, vmax, unit="", steps=3):
    """Draw horizontal grid lines and y-axis labels."""
    t = THEME
    for i in range(steps + 1):
        frac = i / steps
        y = plot_y0 + plot_h * (1 - frac)
        v = vmin + (vmax - vmin) * frac
        # dotted grid line
        for x in range(plot_x0, plot_x0 + plot_w, 12):
            d.line([x, y, x + 3, y], fill=t["grid"], width=1)
        label = f"{v:.1f}" if unit in ("", "°F") else f"{v:.0f}"
        tw = d.textlength(label, font=_font(12))
        d.text((plot_x0 - tw - 6, y - 6), label, fill=t["muted"], font=_font(12))


def _draw_subchart(d, x, y, w, h, title, unit, series, max_points=12):
    """Draw a single clean line chart on the existing canvas.

    ``series`` is ``[(label, color, [(x_idx, value), ...]), ...]``.
    """
    t = THEME
    pad = 4
    # card background
    d.rounded_rectangle([x + pad, y + pad, x + w - pad, y + h - pad],
                        radius=12, outline=t["border"], width=1, fill=t["card"])

    # layout
    margin_left = 50
    margin_right = 14
    margin_top = 34
    margin_bottom = 22
    plot_x0 = x + margin_left
    plot_y0 = y + margin_top
    plot_w = w - margin_left - margin_right
    plot_h = h - margin_top - margin_bottom

    # title
    d.text((x + 16, y + 10), title, fill=t["text"], font=_font(18))

    all_vals = [v for _, _, pts in series for _, v in pts if v is not None]
    if not all_vals:
        d.text((x + 20, y + 60), "No data", fill=t["muted"], font=_font(16))
        return

    vmin, vmax = _round_range(min(all_vals), max(all_vals))
    if vmin == vmax:
        vmax = vmin + 1

    # axes
    d.line([plot_x0, plot_y0, plot_x0, plot_y0 + plot_h], fill=t["border"], width=1)
    d.line([plot_x0, plot_y0 + plot_h, plot_x0 + plot_w, plot_y0 + plot_h],
           fill=t["border"], width=1)

    _draw_grid(d, plot_x0, plot_y0, plot_w, plot_h, vmin, vmax, unit)

    # x-axis labels
    d.text((plot_x0, plot_y0 + plot_h + 6), "-12h", fill=t["muted"], font=_font(12))
    d.text((plot_x0 + plot_w - 22, plot_y0 + plot_h + 6), "now",
           fill=t["muted"], font=_font(12))

    # plot lines
    for label, color, pts in series:
        xy = []
        for idx, v in pts:
            if v is None:
                continue
            px = plot_x0 + (idx / (max_points - 1)) * plot_w
            py = plot_y0 + plot_h * (1 - (v - vmin) / (vmax - vmin))
            xy.append((px, py))
        if len(xy) >= 2:
            d.line(xy, fill=color, width=3)
            # latest dot
            d.ellipse([xy[-1][0] - 4, xy[-1][1] - 4,
                       xy[-1][0] + 4, xy[-1][1] + 4], fill=color)
            # current value label near the latest dot
            latest = pts[-1][1]
            if latest is not None:
                text = f"{latest:.1f}{unit}"
                d.text((xy[-1][0] + 8, xy[-1][1] - 8), text,
                       fill=color, font=_font(12))

    # legend (top right)
    leg_x = x + w - 80
    leg_y = y + 10
    for i, (label, color, pts) in enumerate(series):
        ly = leg_y + i * 16
        d.line([leg_x, ly + 5, leg_x + 16, ly + 5], fill=color, width=3)
        d.text((leg_x + 22, ly), label, fill=t["text"], font=_font(12))


def render_combined(width=800, height=480):
    """Render the full dashboard: stacked charts on the left, latest camera
    detection snapshot on the right."""
    t = THEME
    img = Image.new("RGB", (width, height), t["bg"])
    d = ImageDraw.Draw(img)

    header_h = 36
    gap = 6

    # Sleek header
    d.rectangle([0, 0, width, header_h], fill=t["header_bg"])
    d.text((14, 8), "Front Lawn E-Ink", fill=t["header_text"], font=_font(20))
    ts_now = datetime.now().strftime("%a %d %H:%M").replace(" 0", " ")
    tw = d.textlength(ts_now, font=_font(16))
    d.text((width - tw - 14, 10), ts_now, fill=t["header_text"], font=_font(16))

    # Left panel: two charts
    left_x = 0
    left_y = header_h + gap
    left_w = 510
    left_h = height - left_y - gap
    chart_h = (left_h - gap) // 2

    entity_ids = [eid for eid, _, _ in CHART_ENTITIES]
    try:
        raw = get_history(entity_ids)
        history = _resample_history(raw)
    except Exception as exc:  # noqa: BLE001
        log.exception("history fetch failed")
        history = {}

    temp_series = [(label, color, history.get(eid, []))
                   for eid, label, color in CHART_ENTITIES if "temp" in eid]
    hum_series = [(label, color, history.get(eid, []))
                  for eid, label, color in CHART_ENTITIES if "humid" in eid]

    _draw_subchart(d, left_x, left_y, left_w, chart_h,
                   "Temperature", "°F", temp_series)
    _draw_subchart(d, left_x, left_y + chart_h + gap, left_w, chart_h,
                   "Humidity", "%", hum_series)

    # Right panel: snapshot
    right_x = left_x + left_w + gap
    right_y = header_h + gap
    right_w = width - right_x - gap
    right_h = height - right_y - gap

    snap_img, ts = get_detection_snapshot()
    if snap_img is not None:
        # Fit snapshot, leaving room for caption bar
        cap_h = 26
        avail_h = right_h - cap_h
        sw, sh = snap_img.size
        scale = min(right_w / sw, avail_h / sh)
        nw = max(1, int(sw * scale))
        nh = max(1, int(sh * scale))
        snap_img = snap_img.resize((nw, nh), Image.LANCZOS)
        px = right_x + (right_w - nw) // 2
        py = right_y + (avail_h - nh) // 2

        # snapshot border
        d.rounded_rectangle([px - 2, py - 2, px + nw + 2, py + nh + 2],
                            radius=10, outline=t["border"], width=2)
        img.paste(snap_img, (px, py))

        # caption bar
        bar_y = right_y + right_h - cap_h
        d.rounded_rectangle([right_x, bar_y, right_x + right_w, right_y + right_h],
                            radius=8, fill=t["caption_bg"])
        txt = "Detected " + _fmt_ts(ts)
        font = _font(14)
        tw = d.textlength(txt, font=font)
        if tw > right_w - 8:
            txt = _fmt_ts(ts)
            tw = d.textlength(txt, font=font)
        d.text((right_x + (right_w - tw) // 2, bar_y + 4),
               txt, fill=t["caption_text"], font=font)
    else:
        d.rounded_rectangle([right_x, right_y, right_x + right_w, right_y + right_h],
                            radius=10, outline=t["border"], width=2)
        d.text((right_x + 20, right_y + 40), "No snapshot", fill=t["muted"],
               font=_font(18))

    return img


def fetch_and_render_dashboard(width=800, height=480):
    """Fetch history + snapshot from HA and render the combined dashboard."""
    return render_combined(width=width, height=height)