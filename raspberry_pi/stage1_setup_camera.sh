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
have=$(git describe --tags --exact-match 2>/dev/null || echo none)
if [ "$have" != "$LRS_TAG" ]; then
  # An older librealsense was already here (found on a test Pi: v2.55.1). Switch to the laptop's version.
  echo "Existing source is $have - switching to $LRS_TAG and building from clean"
  git fetch --depth 1 origin tag "$LRS_TAG"
  git checkout -f "$LRS_TAG"
  rm -rf "$SRC/build"
fi
echo "Source version: $(git describe --tags 2>/dev/null || echo unknown)"

echo "--- 5/6 build (about 45-75 min; RSUSB backend = no kernel patches)"
# Found 2026-10-17 on a fresh trixie Pi 5 (4 GB RAM, 2 GB swap - trixie has no dphys-swapfile, so step 2 adds none):
# with 3 jobs the compiler ran out of memory and was killed mid-write, leaving EMPTY object files (bag.cpp.o: 0 bytes)
# that make then treated as done -> librealsense2.so without the bag / .db3 code ("undefined symbol ...
# rosbag2_storage_plugins::SqliteStorage"). So: a build that did not finish (no .henna_build_ok) is redone from clean,
# and Pis under 6 GB build with 2 jobs. BUILD_TOOLS=OFF: Intel's command-line tools are not used by Studio.
if [ -d "$SRC/build" ] && [ ! -f "$SRC/build/.henna_build_ok" ]; then
  echo "The last build did not finish - building from clean"
  rm -rf "$SRC/build"
fi
MEM_KB=$(awk '/MemTotal/{print $2}' /proc/meminfo)
JOBS=3; [ "$MEM_KB" -lt 6000000 ] && JOBS=2
echo "Building with $JOBS jobs (memory $((MEM_KB / 1024)) MB)"
mkdir -p build && cd build
cmake .. -DCMAKE_BUILD_TYPE=Release -DFORCE_RSUSB_BACKEND=ON -DBUILD_PYTHON_BINDINGS=ON \
  -DPYTHON_EXECUTABLE="$VENV/bin/python" -DPYTHON_INSTALL_DIR="$SITE" \
  -DBUILD_EXAMPLES=OFF -DBUILD_GRAPHICAL_EXAMPLES=OFF -DBUILD_UNIT_TESTS=OFF -DBUILD_WITH_OPENMP=OFF \
  -DBUILD_TOOLS=OFF
make -j"$JOBS"
sudo make install
sudo ldconfig
touch "$SRC/build/.henna_build_ok"

echo "--- 6/6 USB permission rules + Python module"
sudo cp "$SRC/config/99-realsense-libusb.rules" /etc/udev/rules.d/
sudo udevadm control --reload-rules && sudo udevadm trigger
want="${LRS_TAG#v}"
got=$("$VENV/bin/python" -c "import pyrealsense2 as rs; print(rs.__version__)" 2>/dev/null || echo none)
if [ "$got" != "$want" ]; then
  # remove old copies of the module first, then copy the freshly built one
  rm -f "$SITE"/pyrealsense2*.so* "$SITE"/pybackend2*.so*
  for f in $(find "$SRC/build" -name 'pyrealsense2*.so*' -o -name 'pybackend2*.so*'); do cp -v "$f" "$SITE/"; done
  got=$("$VENV/bin/python" -c "import pyrealsense2 as rs; print(rs.__version__)" 2>/dev/null || echo none)
fi
if [ "$got" != "$want" ]; then
  echo "STOP: pyrealsense2 is $got, expected $want (the laptop's version). Send this log: $LOG"
  exit 1
fi
echo "pyrealsense2 OK, version $got (same as the laptop)"
echo "=== stage 1 setup finished - now plug the D405 into a BLUE (USB 3) port and run:"
echo "    $VENV/bin/python stage1_camera_check.py"
