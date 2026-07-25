# rpi_eink

A small web app for the **Waveshare 7.3inch e-Paper HAT (E)** E6 color
E-Ink display, running on a **Raspberry Pi Zero (armhf)**.

Open a browser on any device on your network, upload an image, and it
appears on the e-Paper display. The image is automatically scaled to the
panel's native `800x480` resolution and dithered to the 7 colors supported
by the panel (black, white, yellow, red, blue, green + an unused slot).

| | |
|---|---|
| Panel | 7.3inch e-Paper HAT (E) |
| Resolution | 800 x 480 |
| Colors | 6 (E6 full color) |
| Refresh time | ~25s |
| Interface | SPI |

The hardware driver is the official [Waveshare e-Paper](https://github.com/waveshare/e-Paper)
library, included here as a git submodule.

## Hardware setup

Plug the HAT onto the 40-pin header of the Raspberry Pi Zero and enable SPI:

```bash
sudo raspi-config   # Interfacing Options -> SPI -> Yes
sudo reboot
ls /dev/spidev0.0    # should exist after reboot
```

## Install

```bash
git clone --recurse-submodules https://github.com/<you>/rpi_eink.git
cd rpi_eink
bash setup.sh
```

If you cloned without `--recurse-submodules`, populate it with:

```bash
git submodule update --init --recursive
```

## Run

```bash
python3 app.py
```

Then open `http://<raspberrypi>:8000/` and drag an image onto the page.
Press **Send to display** (or **Clear screen** to blank it).

### Environment variables

| Variable | Default | Description |
|---|---|---|
| `HOST` | `0.0.0.0` | Bind address |
| `PORT` | `8000` | Listen port |
| `EPD_MOCK` | unset | When `1`, use a no-hardware mock (lets the app run/test anywhere) |
| `LOG_LEVEL` | `INFO` | Logging verbosity |
| `HA_URL` | unset | Home Assistant base URL, e.g. `http://192.168.0.172:8123` |
| `HA_TOKEN` | unset | Long-lived access token from HA (Profile → Long-Lived Access Tokens) |

## Home Assistant

The **Home Assistant** button fetches sensor state from your HA instance
via the REST API and renders an e-ink friendly dashboard summary (time,
weather, temperature/humidity cards) with Pillow — lightweight enough
for the Pi Zero (no headless browser needed).

To set it up:

1. In HA: Profile → **Long-Lived Access Tokens** → Create Token → name it `eink`.
2. Copy the token, then run the app with:

   ```bash
   HA_URL=http://192.168.0.172:8123 \
   HA_TOKEN=eyJhbGciOi... \
   python3 app.py
   ```

3. Open the web page and press **Home Assistant**.

The layout auto-discovers `device_class: temperature` and `device_class:
humidity` sensors, plus any `weather.*` entity, up to 8 cards. If no
weather entity is present it is skipped. The token is read from an
environment variable only — nothing is committed to the repo.

## Mock mode

If the hardware driver can't be loaded (e.g. you're developing on a laptop),
the app automatically falls back to a `MockEPD`. Set `EPD_MOCK=1` to force it.
Uploads are still processed and the dithered preview is shown on the page,
but nothing is written to SPI.

## Autostart as a service (optional)

Copy and enable the bundled systemd unit:

```bash
sudo cp eink-web.service /etc/systemd/system/
sudo sed -i "s/%i/$USER/" /etc/systemd/system/eink-web.service
sudo systemctl daemon-reload
sudo systemctl enable --now eink-web.service
```

## Notes & precautions

- A refresh takes ~25s and blocks the request; the UI shows a spinner.
- To protect the panel the display is put to **sleep** after every refresh,
  and re-initialized on the next one. Don't leave the panel powered/high-voltage
  for long periods without refreshing (see Waveshare's precautions).
- Avoid rapid repeated refreshes; Waveshare recommends >=180s between refreshes.

## How it works

1. `app.py` (Flask) serves an upload page and handles `/upload`.
2. The uploaded image is scaled to fit `800x480` (aspect preserved, centered
   on white) with Pillow.
3. `epd_driver.get_epd()` returns the real Waveshare `epd7in3e` driver on the Pi,
   or a `MockEPD` elsewhere. The driver quantizes the image to 7 colors and
   packs it 4 bits per pixel before sending it over SPI.

## License

MIT for the code in this repository. The vendored Waveshare driver (under
`e-Paper/`, a git submodule) retains its own license.