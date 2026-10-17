#!/usr/bin/env bash
# Make the Henna Studio INSTALL STICK on a working Raspberry Pi (user 2026-10-16: "i want a master usb stick that
# can install in pi 5 and pi4 - just a option to select"). Run on the master Pi 5 (Studio set up and tested):
#
#     bash ~/henna-program/raspberry_pi/make_install_stick.sh /media/$USER/<stick name>
#
# Writes <stick>/henna_installer/ (about 1-1.5 GB; the stick may be FAT32 / exFAT):
#   henna-program.bundle  Studio, branch raspberry-pi with its history (git clone works from it; git pull later)
#   lib/                  the camera library built here (librealsense 2.58.4 in /usr/local + pyrealsense2 module)
#   wheels/ + requirements.lock   every Python package of ~/henna-venv, exact versions, for this Pi's OS
#   models/hand_landmarker.task   the approved hand model (SHA-256 checked)
#   target.txt            OS release + Python version the library was built for (the installer refuses others)
#   debs/                 the system packages Studio needs + all their dependencies, as a local package source
#                         (Packages.gz) - the new Pi installs the missing ones from here, WITHOUT internet
#                         (user 2026-10-17: "installation doesn't require wifi")
#   install.sh            the installer (raspberry_pi/install_from_stick.sh) - Pi 5 / Pi 4 menu
#   SHA256SUMS            checksums of every file (the installer checks them first)
# Nothing here touches the CNC or the camera. Needs internet once, here (Python wheels, system packages).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(dirname "$HERE")"
VENV="$HOME/henna-venv"
LRS_BUILD="$HOME/librealsense/build"
STICK="${1:-}"
[ -n "$STICK" ] && [ -d "$STICK" ] && [ -w "$STICK" ] || {
  echo "usage: bash $0 /media/$USER/<stick name>   (the stick must be plugged in and writable)"; exit 1; }
DEST="$STICK/henna_installer"
echo "=== Henna Studio install stick -> $DEST - $(date) ==="

[ "$(uname -m)" = "aarch64" ] || { echo "STOP: run this on a 64-bit Raspberry Pi OS"; exit 1; }
got=$("$VENV/bin/python" -c "import pyrealsense2 as rs; print(rs.__version__)" 2>/dev/null || echo none)
[ "$got" = "2.58.4" ] || { echo "STOP: this Pi's camera library is $got, expected 2.58.4 - set this Pi up first"; exit 1; }
branch=$(git -C "$REPO" rev-parse --abbrev-ref HEAD)
[ "$branch" = "raspberry-pi" ] || { echo "STOP: $REPO is on $branch, expected raspberry-pi"; exit 1; }
if [ -e "$DEST" ]; then
  echo "STOP: $DEST already exists - rename or delete it first"; exit 1
fi
mkdir -p "$DEST/lib/python" "$DEST/wheels" "$DEST/models" "$DEST/debs"
# system packages the installer needs on a fresh Raspberry Pi OS (most are already there; the rest come from debs/)
WANTED="git python3-venv libusb-1.0-0 libportaudio2 fonts-urw-base35 fonts-comic-neue curl python3-gpiozero python3-lgpio chromium"

echo "--- 1/7 what this stick is for"
. /etc/os-release
PYV=$("$VENV/bin/python" -c 'import sys; print("%d.%d" % sys.version_info[:2])')
cat > "$DEST/target.txt" <<EOF
os_codename=$VERSION_CODENAME
python=$PYV
arch=$(uname -m)
librealsense=2.58.4
studio_commit=$(git -C "$REPO" rev-parse --short HEAD)
made_on=$(tr -d '\0' < /proc/device-tree/model)
made=$(date -Iseconds)
EOF
cat "$DEST/target.txt"

echo "--- 2/7 Studio (git bundle of branch raspberry-pi)"
git -C "$REPO" bundle create "$DEST/henna-program.bundle" raspberry-pi
git -C "$REPO" status --porcelain | grep -v 'validation_log_' | sed 's/^/  not on the stick (uncommitted): /' || true

echo "--- 3/7 camera library (built on this Pi)"
if [ -f "$LRS_BUILD/install_manifest.txt" ]; then
  grep '^/usr/local/' "$LRS_BUILD/install_manifest.txt" > "$DEST/lib/files.txt"
else
  ls -d /usr/local/lib/librealsense2.so* /usr/local/include/librealsense2 /usr/local/lib/cmake/realsense2 \
    /usr/local/lib/pkgconfig/realsense2.pc 2>/dev/null > "$DEST/lib/files.txt"
fi
sed 's|^/||' "$DEST/lib/files.txt" > "$DEST/lib/files_rel.txt"
tar -czf "$DEST/lib/librealsense.tar.gz" -C / -T "$DEST/lib/files_rel.txt"
SITE=$("$VENV/bin/python" -c 'import site; print(site.getsitepackages()[0])')
cp -v "$SITE"/pyrealsense2.cpython-*.so "$SITE"/pyrealsense2*.so.2.58* "$DEST/lib/python/"   # not old 2.55 leftovers
cp "$SITE"/pybackend2*.so* "$DEST/lib/python/" 2>/dev/null || true
# the camera's USB rules: from the build folder, or - on a Pi that was itself installed from a stick - from udev
RULES="$HOME/librealsense/config/99-realsense-libusb.rules"
[ -f "$RULES" ] || RULES=/etc/udev/rules.d/99-realsense-libusb.rules
[ -f "$RULES" ] || { echo "STOP: no 99-realsense-libusb.rules found on this Pi"; exit 1; }
cp -v "$RULES" "$DEST/lib/"

echo "--- 4/7 Python packages (exact versions of $VENV)"
"$VENV/bin/pip" freeze --exclude-editable | grep -viE '^(pyrealsense2|pkg[-_]resources)' | grep -v ' @ ' \
  > "$DEST/requirements.lock"
"$VENV/bin/pip" download --only-binary=:all: -r "$DEST/requirements.lock" -d "$DEST/wheels"
echo "$(ls "$DEST/wheels" | wc -l) wheel files"

echo "--- 5/7 hand model"
WANT=$(grep -m1 'SHA-256:' "$REPO/models/MODEL_INFO.txt" | awk '{print $2}')
[ "$(sha256sum "$REPO/models/hand_landmarker.task" | cut -c1-64)" = "$WANT" ] || {
  echo "STOP: this Pi's hand model is not the approved one"; exit 1; }
cp "$REPO/models/hand_landmarker.task" "$DEST/models/"

echo "--- 6/7 system packages + dependencies (offline package source)"
dpkg -s dpkg-dev >/dev/null 2>&1 || sudo apt-get install -y dpkg-dev          # for dpkg-scanpackages
printf '%s\n' $WANTED > "$DEST/debs/wanted.txt"
DEPS=$(apt-cache depends --recurse --no-recommends --no-suggests --no-conflicts --no-breaks --no-replaces \
  --no-enhances $WANTED | grep -E '^[a-zA-Z0-9]' | sort -u)
TMPD=$(mktemp -d)
( cd "$TMPD" && for p in $DEPS; do apt-get download "$p" >/dev/null 2>&1 || echo "  (no package file for $p)"; done )
( cd "$TMPD" && dpkg-scanpackages --multiversion . /dev/null 2>/dev/null | gzip -9 > Packages.gz )
cp "$TMPD"/*.deb "$TMPD/Packages.gz" "$DEST/debs/"
rm -rf "$TMPD"
echo "$(ls "$DEST/debs"/*.deb | wc -l) package files, $(du -sh "$DEST/debs" | cut -f1)"

echo "--- 7/7 installer + checksums"
cp "$HERE/install_from_stick.sh" "$DEST/install.sh"
cat > "$DEST/README.txt" <<'EOF'
HENNA STUDIO INSTALL STICK (Raspberry Pi 5; a Pi 4 cannot run the hand detection)
The Pi needs: the SAME Raspberry Pi OS 64-bit release as in target.txt (os_codename) and the 5" Touch Display 2.
No internet needed - everything comes from this stick.
1. Plug this stick into the new Pi and open a terminal.
2.   bash /media/$USER/<stick name>/henna_installer/install.sh
3. Choose 1 (Pi 5), give the Pi's password when asked. About 10-15 minutes, no Wi-Fi needed.
4. Reboot when it says PASS. Then: start Henna Studio from the desktop icon, choose the machine, calibrate it
   (each machine needs its own calibration), set the daily-check reference, and - for the servo - save its angles.
EOF
( cd "$DEST" && find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS )
sync
echo "=== DONE: $(du -sh "$DEST" | cut -f1) on the stick. Eject it before unplugging."
