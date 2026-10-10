#!/usr/bin/env bash
# Raspberry Pi port - stage 1: build Intel RealSense (librealsense v2.58.4, same as the laptop) with Python bindings
# for the D405 on a Raspberry Pi 5 (64-bit Raspberry Pi OS Bookworm). Camera only - nothing here talks to the CNC.
#
# Run in a terminal on the Pi:   bash stage1_setup_camera.sh
# Takes about 1 hour. It asks for your password for 'sudo' (installing packages, USB rules, swap).
# Creates: ~/henna-venv (Python), ~/librealsense (source + build). Log: ~/henna_stage1_setup.log
set -euo pipefail
LRS_TAG="v2.58.4"
VENV="$HOME/henna-venv"
SRC="$HOME/librealsense"
LOG="$HOME/henna_stage1_setup.log"
exec > >(tee -a "$LOG") 2>&1
echo "=== Henna Studio - Raspberry Pi stage 1 setup - $(date) ==="

[ "$(uname -m)" = "aarch64" ] || { echo "STOP: needs 64-bit Raspberry Pi OS (uname -m = $(uname -m))"; exit 1; }
grep -qi "raspberry pi 5" /proc/device-tree/model 2>/dev/null || echo "Note: this is not a Raspberry Pi 5 ($(tr -d '\0' < /proc/device-tree/model 2>/dev/null))"
echo "Memory: $(free -h | awk '/Mem:/{print $2}')   Python: $(python3 --version)"

echo "--- 1/6 system packages"
sudo apt-get update
sudo apt-get install -y git cmake build-essential pkg-config libssl-dev libusb-1.0-0-dev libudev-dev \
  python3-dev python3-venv libgtk-3-dev libglfw3-dev libgl1-mesa-dev libglu1-mesa-dev

echo "--- 2/6 swap (4 GB Pi: the build needs extra memory)"
if [ -f /etc/dphys-swapfile ]; then
  cur=$(grep -E '^CONF_SWAPSIZE=' /etc/dphys-swapfile | cut -d= -f2 || true)
  if [ "${cur:-0}" -lt 4096 ]; then
    sudo dphys-swapfile swapoff || true
    sudo sed -i 's/^#\?CONF_SWAPSIZE=.*/CONF_SWAPSIZE=4096/' /etc/dphys-swapfile
    grep -q '^CONF_MAXSWAP=' /etc/dphys-swapfile && sudo sed -i 's/^CONF_MAXSWAP=.*/CONF_MAXSWAP=4096/' /etc/dphys-swapfile
    sudo dphys-swapfile setup && sudo dphys-swapfile swapon
  fi
fi
echo "Swap: $(free -h | awk '/Swap:/{print $2}')"

echo "--- 3/6 Python environment $VENV"
[ -d "$VENV" ] || python3 -m venv "$VENV"
"$VENV/bin/pip" install --upgrade pip wheel
"$VENV/bin/pip" install numpy
SITE=$("$VENV/bin/python" -c 'import site; print(site.getsitepackages()[0])')

echo "--- 4/6 librealsense $LRS_TAG source"
if [ ! -d "$SRC/.git" ]; then
  git clone --depth 1 --branch "$LRS_TAG" https://github.com/IntelRealSense/librealsense.git "$SRC"
fi
cd "$SRC"
echo "Source version: $(git describe --tags 2>/dev/null || echo unknown)"

echo "--- 5/6 build (about 45-60 min; RSUSB backend = no kernel patches)"
mkdir -p build && cd build
cmake .. -DCMAKE_BUILD_TYPE=Release -DFORCE_RSUSB_BACKEND=ON -DBUILD_PYTHON_BINDINGS=ON \
  -DPYTHON_EXECUTABLE="$VENV/bin/python" -DPYTHON_INSTALL_DIR="$SITE" \
  -DBUILD_EXAMPLES=OFF -DBUILD_GRAPHICAL_EXAMPLES=OFF -DBUILD_UNIT_TESTS=OFF -DBUILD_WITH_OPENMP=OFF
make -j3
sudo make install
sudo ldconfig

echo "--- 6/6 USB permission rules + Python module"
sudo cp "$SRC/config/99-realsense-libusb.rules" /etc/udev/rules.d/
sudo udevadm control --reload-rules && sudo udevadm trigger
if ! "$VENV/bin/python" -c "import pyrealsense2" 2>/dev/null; then
  for f in $(find "$SRC/build" -name 'pyrealsense2*.so*' -o -name 'pybackend2*.so*'); do cp -v "$f" "$SITE/"; done
fi
"$VENV/bin/python" -c "import pyrealsense2 as rs; print('pyrealsense2 OK, version', rs.__version__)"
echo "=== stage 1 setup finished - now plug the D405 into a BLUE (USB 3) port and run:"
echo "    $VENV/bin/python stage1_camera_check.py"
