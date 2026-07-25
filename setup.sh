#!/usr/bin/env bash
# One-time setup for Raspberry Pi Zero (armhf).
# Run from the project root:  bash setup.sh
set -euo pipefail

cd "$(dirname "$0")"

echo "==> Initializing the Waveshare e-Paper submodule"
git submodule update --init --recursive

echo "==> Installing system packages"
sudo apt-get update
sudo apt-get install -y python3-pip python3-pil python3-numpy

echo "==> Installing Python dependencies"
pip3 install --user --break-system-packages -r requirements.txt -r requirements-pi.txt \
  || pip3 install --user -r requirements.txt -r requirements-pi.txt

echo "==> Checking SPI interface"
if [ ! -e /dev/spidev0.0 ]; then
  echo "  SPI not enabled. Run 'sudo raspi-config' ->"
  echo "    Interfacing Options -> SPI -> Yes, then reboot."
else
  echo "  /dev/spidev0.0 found."
fi

echo
echo "Done. Start the server with:  python3 app.py"
echo "Then open http://<raspberrypi>:8000/ in a browser."