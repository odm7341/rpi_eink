"""Small Flask app that accepts an uploaded image and pushes it to the
Waveshare 7.3inch e-Paper HAT (E).

Run on the Raspberry Pi:
    python3 app.py            # serves on all interfaces, port 8000

A hardware refresh takes ~25s. The upload is handled synchronously but the
browser shows a spinner while it waits for the JSON response.
"""

import io
import logging
import os
import threading
import time

from flask import Flask, jsonify, render_template, request, url_for
from PIL import Image


def _load_env_file(path=".env"):
    """Minimal .env loader: KEY=VALUE lines, optional surrounding quotes,
    ignores blanks and # comments. Only sets vars not already in os.environ
    so real env vars always win."""
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), path)
    if not os.path.exists(p):
        return
    with open(p, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k = k.strip()
            v = v.strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
                v = v[1:-1]
            if k and k not in os.environ:
                os.environ[k] = v


_load_env_file()

try:
    from epd_driver import EPD_HEIGHT, EPD_WIDTH, get_epd, PALETTE
except ImportError:  # allow running as a bare module import for tests
    from epd_driver import EPD_HEIGHT, EPD_WIDTH, get_epd, PALETTE  # noqa: F401

try:
    import ha_display
except ImportError:  # noqa: F401
    ha_display = None

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("eink-web")

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # 16 MB upload cap

UPLOAD_DIR = os.path.join(app.static_folder, "uploads")
LAST_FILE = "last.png"
LAST_PATH = os.path.join(UPLOAD_DIR, LAST_FILE)
THUMB_FILE = "last_thumb.png"
THUMB_PATH = os.path.join(UPLOAD_DIR, THUMB_FILE)

# The e-Paper is not safe to refresh concurrently.
_epd_lock = threading.Lock()


def prepare_image(file_bytes):
    """Return an 800x480 RGB image with the uploaded image scaled to fit,
    centered on a white background (preserving aspect ratio)."""
    img = Image.open(io.BytesIO(file_bytes))
    img = img.convert("RGB")

    iw, ih = img.size
    scale = min(EPD_WIDTH / iw, EPD_HEIGHT / ih)
    nw = max(1, int(round(iw * scale)))
    nh = max(1, int(round(ih * scale)))
    img = img.resize((nw, nh), Image.LANCZOS)

    canvas = Image.new("RGB", (EPD_WIDTH, EPD_HEIGHT), (255, 255, 255))
    canvas.paste(img, ((EPD_WIDTH - nw) // 2, (EPD_HEIGHT - nh) // 2))
    return canvas


def quantize_preview(image):
    """Produce a small preview PNG dithered to the panel's 7 colors so the
    web page shows what will actually appear on the screen."""
    pal_image = Image.new("P", (1, 1))
    pal_image.putpalette(PALETTE)
    dithered = image.convert("RGB").quantize(palette=pal_image).convert("RGB")
    preview = dithered.copy()
    preview.thumbnail((480, 288))
    return preview


def render_to_epd(canvas):
    """Push ``canvas`` to the display. Safe to call repeatedly; the panel is
    put to sleep after each refresh to protect the hardware."""
    epd = get_epd()
    with _epd_lock:
        epd.init()
        try:
            buf = epd.getbuffer(canvas)
            epd.display(buf)
        finally:
            try:
                epd.sleep()
            except Exception:  # noqa: BLE001
                log.exception("sleep() failed")


@app.route("/")
def index():
    has_last = os.path.exists(LAST_PATH)
    last_url = ""
    if has_last:
        last_url = url_for("static", filename=f"uploads/{LAST_FILE}") + f"?v={int(time.time())}"
    return render_template("index.html", width=EPD_WIDTH, height=EPD_HEIGHT,
                           has_last=has_last, last_url=last_url)


@app.route("/upload", methods=["POST"])
def upload():
    if "image" not in request.files:
        return jsonify(ok=False, error="No image provided."), 400
    f = request.files["image"]
    if not f.filename:
        return jsonify(ok=False, error="No image selected."), 400

    data = f.read()
    try:
        canvas = prepare_image(data)
    except Exception as exc:  # noqa: BLE001
        log.warning("Bad image upload: %r", exc)
        return jsonify(ok=False, error=f"Could not read image: {exc}"), 400

    os.makedirs(UPLOAD_DIR, exist_ok=True)
    canvas.save(LAST_PATH, "PNG")
    quantize_preview(canvas).save(THUMB_PATH, "PNG")

    try:
        render_to_epd(canvas)
    except Exception as exc:  # noqa: BLE001
        log.exception("EPD refresh failed")
        return jsonify(ok=False, error=f"Display update failed: {exc}"), 500

    thumb = url_for("static", filename=f"uploads/{THUMB_FILE}") + f"?v={int(time.time())}"
    return jsonify(ok=True,
                   preview=thumb,
                   message="Image sent to the display (refresh takes ~25s).")


@app.route("/ha", methods=["POST"])
def homeassistant():
    if ha_display is None:
        return jsonify(ok=False, error="ha_display module not available."), 500
    if not ha_display.configured():
        return jsonify(ok=False,
                       error="Set HA_URL and HA_TOKEN env vars to use Home Assistant."), 400
    try:
        canvas = ha_display.fetch_and_render(EPD_WIDTH, EPD_HEIGHT)
    except Exception as exc:  # noqa: BLE001
        log.exception("Home Assistant fetch/render failed")
        return jsonify(ok=False, error=f"HA request failed: {exc}"), 502

    os.makedirs(UPLOAD_DIR, exist_ok=True)
    canvas.save(LAST_PATH, "PNG")
    quantize_preview(canvas).save(THUMB_PATH, "PNG")

    try:
        render_to_epd(canvas)
    except Exception as exc:  # noqa: BLE001
        log.exception("EPD refresh failed")
        return jsonify(ok=False, error=f"Display update failed: {exc}"), 500

    thumb = url_for("static", filename=f"uploads/{THUMB_FILE}") + f"?v={int(time.time())}"
    return jsonify(ok=True,
                   preview=thumb,
                   message="Home Assistant screen sent to the display (refresh takes ~25s).")


@app.route("/clear", methods=["POST"])
def clear():
    try:
        epd = get_epd()
        with _epd_lock:
            epd.init()
            try:
                epd.Clear(0x11)  # white
            finally:
                try:
                    epd.sleep()
                except Exception:  # noqa: BLE001
                    log.exception("sleep() failed")
    except Exception as exc:  # noqa: BLE001
        log.exception("Clear failed")
        return jsonify(ok=False, error=f"Clear failed: {exc}"), 500
    return jsonify(ok=True, message="Display cleared.")


@app.route("/health")
def health():
    return jsonify(ok=True, width=EPD_WIDTH, height=EPD_HEIGHT,
                   mock=_mock_flag())


def _mock_flag():
    return bool(os.environ.get("EPD_MOCK", "").lower() in ("1", "true", "yes", "on"))


if __name__ == "__main__":
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8000"))
    log.info("Serving on http://%s:%d (mock=%s)", host, port, _mock_flag())
    app.run(host=host, port=port, threaded=True)