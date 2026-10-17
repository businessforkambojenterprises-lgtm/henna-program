#!/usr/bin/env bash
# Henna Studio installer on the INSTALL STICK (made by raspberry_pi/make_install_stick.sh; copied there as
# install.sh). User 2026-10-16: "i want a master usb stick that can install in pi 5 and pi4 - just a option to select".
#
#     bash /media/$USER/<stick name>/henna_installer/install.sh
#
# 1 Pi 5 / 2 Pi 4 menu (the detected model is the default). About 10-15 minutes, needs internet for the system
# packages, asks for this Pi's password (sudo). Nothing here touches the CNC or the camera. Log: ~/henna_install.log
# Installs: system packages, ~/henna-venv (the stick's exact Python packages), the prebuilt camera library
# (librealsense 2.58.4), Studio in ~/henna-program (branch raspberry-pi, git pull works later), the hand model,
# USB permissions (camera rules, CNC group dialout), the servo's hardware signal for the chosen model in
# /boot/firmware/config.txt (backup first), the desktop icon; then runs the tests and says PASS or FAIL.
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
VENV="$HOME/henna-venv"
REPO="$HOME/henna-program"
GITHUB="https://github.com/businessforkambojenterprises-lgtm/henna-program.git"
CONFIG=/boot/firmware/config.txt
LOG="$HOME/henna_install.log"
exec > >(tee -a "$LOG") 2>&1
echo "=== Henna Studio - install from stick - $(date) ==="
stop() { echo "STOP: $*"; echo "Nothing more was changed. Log: $LOG"; exit 1; }
val() { grep -m1 "^$1=" "$SRC/target.txt" | cut -d= -f2-; }

echo "--- 1/9 check the stick"
[ -f "$SRC/target.txt" ] && [ -f "$SRC/SHA256SUMS" ] || stop "this is not a complete henna_installer folder"
( cd "$SRC" && sha256sum -c --quiet SHA256SUMS ) || stop "a file on the stick is damaged - make the stick again"
echo "Stick OK: Studio $(val studio_commit), made $(val made) on $(val made_on)"

echo "--- 2/9 which Raspberry Pi"
MODEL_TEXT=$(tr -d '\0' < /proc/device-tree/model 2>/dev/null || echo unknown)
case "$MODEL_TEXT" in *"Raspberry Pi 5"*) DETECTED=1 ;; *"Raspberry Pi 4"*) DETECTED=2 ;; *) DETECTED="" ;; esac
echo "This Pi says: $MODEL_TEXT"
echo "  1) Raspberry Pi 5"
echo "  2) Raspberry Pi 4"
read -r -p "Choose 1 or 2 [${DETECTED:-no default}]: " CHOICE < /dev/tty
CHOICE="${CHOICE:-$DETECTED}"
case "$CHOICE" in 1) PI=5 ;; 2) PI=4 ;; *) stop "choose 1 or 2" ;; esac
if [ -n "$DETECTED" ] && [ "$CHOICE" != "$DETECTED" ]; then
  read -r -p "You chose Pi $PI but this Pi says '$MODEL_TEXT'. Continue anyway? (y/N): " yn < /dev/tty
  [ "$yn" = "y" ] || stop "model not confirmed"
fi
echo "Installing for Raspberry Pi $PI"

echo "--- 3/9 check this Pi"
[ "$(uname -m)" = "$(val arch)" ] || stop "this Pi runs $(uname -m), the stick is for $(val arch) - use 64-bit Raspberry Pi OS"
. /etc/os-release
PYV=$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')
[ "$VERSION_CODENAME" = "$(val os_codename)" ] && [ "$PYV" = "$(val python)" ] || stop "this Pi runs
  Raspberry Pi OS '$VERSION_CODENAME' with Python $PYV; the stick was made on '$(val os_codename)' with Python $(val python).
  Write the same 64-bit Raspberry Pi OS release to this Pi's SD card (Raspberry Pi Imager) and run this again."
[ -e "$REPO" ] && stop "$REPO already exists - Studio is installed here; update it with: cd $REPO && git pull"
# MediaPipe (hand detection) needs the processor's AES instructions: the Pi 5 has them, the Pi 4's chip does not
# (found 2026-10-16: "This binary was compiled with aes enabled, but this feature is not available on this processor").
grep -qw aes /proc/cpuinfo || stop "this processor has no AES instructions, which Studio's hand detection (MediaPipe)
  needs - a Raspberry Pi 4 cannot run it. Use a Raspberry Pi 5."
echo "OS $VERSION_CODENAME, Python $PYV, $(uname -m), AES: OK"

echo "--- 4/9 system packages (from the stick - no internet needed)"
[ -f "$SRC/debs/wanted.txt" ] && [ -f "$SRC/debs/Packages.gz" ] || stop "this stick has no system packages - make it again"
NEED=""
for p in $(cat "$SRC/debs/wanted.txt"); do dpkg -s "$p" >/dev/null 2>&1 || NEED="$NEED $p"; done
if [ -z "$NEED" ]; then
  echo "All system packages are already on this Pi."
else
  echo "Installing from the stick:$NEED"
  LIST=$(mktemp)
  echo "deb [trusted=yes] file:$SRC/debs ./" > "$LIST"
  APTOPT=(-o Dir::Etc::SourceList="$LIST" -o Dir::Etc::SourceParts=/dev/null -o APT::Get::List-Cleanup=0)
  sudo apt-get "${APTOPT[@]}" update
  sudo apt-get "${APTOPT[@]}" install -y --no-install-recommends $NEED
  rm -f "$LIST"
fi

echo "--- 5/9 Studio -> $REPO"
git clone --branch raspberry-pi "$SRC/henna-program.bundle" "$REPO"
git -C "$REPO" remote set-url origin "$GITHUB"
if git -C "$REPO" fetch -q origin raspberry-pi 2>/dev/null; then
  git -C "$REPO" branch -q --set-upstream-to=origin/raspberry-pi raspberry-pi
  echo "Linked to GitHub: later updates with  cd $REPO && git pull"
else
  echo "GitHub not reachable now - later:  cd $REPO && git fetch origin && git branch -u origin/raspberry-pi"
fi
cp "$SRC/models/hand_landmarker.task" "$REPO/models/"
WANT=$(grep -m1 'SHA-256:' "$REPO/models/MODEL_INFO.txt" | awk '{print $2}')
[ "$(sha256sum "$REPO/models/hand_landmarker.task" | cut -c1-64)" = "$WANT" ] || stop "hand model check failed"

echo "--- 6/9 Python packages + camera library"
python3 -m venv "$VENV"
"$VENV/bin/pip" install -q --no-index --find-links "$SRC/wheels" -r "$SRC/requirements.lock"
sudo tar -xzf "$SRC/lib/librealsense.tar.gz" -C /
sudo ldconfig
SITE=$("$VENV/bin/python" -c 'import site; print(site.getsitepackages()[0])')
cp "$SRC"/lib/python/* "$SITE/"
sudo cp "$SRC/lib/99-realsense-libusb.rules" /etc/udev/rules.d/
sudo udevadm control --reload-rules && sudo udevadm trigger
got=$("$VENV/bin/python" -c "import pyrealsense2 as rs; print(rs.__version__)" 2>/dev/null || echo none)
[ "$got" = "$(val librealsense)" ] || stop "camera library is $got, expected $(val librealsense)"
echo "Camera library OK ($got)"

echo "--- 7/9 USB permission for the CNC, recordings folder"
if ! id -nG "$USER" | grep -qw dialout; then sudo usermod -aG dialout "$USER"; fi
mkdir -p "$HOME/Documents"

echo "--- 8/9 servo hardware signal for the Pi $PI (GPIO18, pin 12) in $CONFIG"
if [ "$PI" = 5 ]; then LINES=("dtoverlay=pwm-pio,gpio=18"); else LINES=("dtoverlay=pwm,pin=18,func=2" "dtparam=audio=off"); fi
sudo cp "$CONFIG" "$CONFIG.before-henna"
for line in "${LINES[@]}"; do
  grep -qxF "$line" "$CONFIG" || echo "$line" | sudo tee -a "$CONFIG" > /dev/null
done
echo "Added: ${LINES[*]}  (backup: $CONFIG.before-henna)"
[ "$PI" = 4 ] && echo "Note: on a Pi 4 the servo signal uses the PWM that analog audio would use - audio jack off."

echo "--- 9/9 desktop icon + tests"
chmod +x "$REPO/raspberry_pi/henna_studio.sh"
mkdir -p "$HOME/Desktop" "$HOME/.local/share/applications"
for f in "$HOME/Desktop/henna-studio.desktop" "$HOME/.local/share/applications/henna-studio.desktop"; do
  cat > "$f" <<EOF
[Desktop Entry]
Type=Application
Name=Henna Studio
Comment=Henna Studio v29 (Raspberry Pi $PI)
Exec=bash $REPO/raspberry_pi/henna_studio.sh
Icon=$REPO/desktop_build/henna_icon_preview.png
Terminal=false
Categories=Graphics;
EOF
  chmod +x "$f"
done
gio set "$HOME/Desktop/henna-studio.desktop" metadata::trusted true 2>/dev/null || true
cd "$REPO"
if "$VENV/bin/python" -m unittest discover -s tests > "$HOME/henna_install_tests.log" 2>&1; then
  RESULT="PASS"
else
  RESULT="FAIL"
fi
echo "Tests: $(grep -E '^Ran |^OK|^FAILED' "$HOME/henna_install_tests.log" | tr '\n' ' ') (details: ~/henna_install_tests.log)"
echo "=== $RESULT - Henna Studio installed for Raspberry Pi $PI. Now:  sudo reboot"
echo "    After the reboot: Henna Studio icon -> choose the machine -> calibrate it -> daily-check reference;"
echo "    for the servo: Program -> Servo angles -> Save."
[ "$RESULT" = PASS ]
