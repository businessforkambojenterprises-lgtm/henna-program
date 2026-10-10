#!/usr/bin/env bash
# Raspberry Pi port - stage 2: set up Henna Studio (v19) on the Pi. Needs stage 1 (~/henna-venv with
# pyrealsense2 2.58.4). Installs the laptop's exact Python package versions, two handwriting-style fonts, the
# approved hand model (checked by its SHA-256), USB permission for the CNC, runs the tests, adds a desktop icon.
# Does NOT connect to the CNC or start the camera.
#
# Run on the Pi:   bash ~/henna-program/raspberry_pi/stage2_setup_studio.sh      (about 10-15 min)
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(dirname "$HERE")"
VENV="$HOME/henna-venv"
LOG="$HOME/henna_stage2_setup.log"
exec > >(tee -a "$LOG") 2>&1
echo "=== Henna Studio - Raspberry Pi stage 2 setup - $(date) ==="

[ -x "$VENV/bin/python" ] || { echo "STOP: run stage 1 first (no $VENV)"; exit 1; }
got=$("$VENV/bin/python" -c "import pyrealsense2 as rs; print(rs.__version__)" 2>/dev/null || echo none)
[ "$got" = "2.58.4" ] || { echo "STOP: pyrealsense2 is $got, stage 1 must give 2.58.4"; exit 1; }

echo "--- 1/6 system packages (audio library for mediapipe, fonts, browser)"
sudo apt-get update
sudo apt-get install -y libportaudio2 fonts-urw-base35 fonts-comic-neue curl
if ! command -v chromium-browser >/dev/null && ! command -v chromium >/dev/null; then
  sudo apt-get install -y chromium-browser || sudo apt-get install -y chromium
fi

echo "--- 2/6 Python packages (the laptop's exact versions)"
"$VENV/bin/pip" install -r "$HERE/requirements-pi.txt"

echo "--- 3/6 hand model (approved file, checked by SHA-256)"
MODEL="$REPO/models/hand_landmarker.task"
URL=$(grep -m1 'Source:' "$REPO/models/MODEL_INFO.txt" | awk '{print $2}')
WANT=$(grep -m1 'SHA-256:' "$REPO/models/MODEL_INFO.txt" | awk '{print $2}')
if [ ! -f "$MODEL" ] || [ "$(sha256sum "$MODEL" | cut -c1-64)" != "$WANT" ]; then
  curl -fL -o "$MODEL.download" "$URL"
  have=$(sha256sum "$MODEL.download" | cut -c1-64)
  if [ "$have" != "$WANT" ]; then
    rm -f "$MODEL.download"
    echo "STOP: the downloaded hand model is not the approved one ($have) - ask before using another model"; exit 1
  fi
  mv "$MODEL.download" "$MODEL"
fi
echo "Hand model OK ($WANT)"

echo "--- 4/6 USB permission for the CNC (group dialout) and recordings folder"
if ! id -nG "$USER" | grep -qw dialout; then
  sudo usermod -aG dialout "$USER"
  echo "Added $USER to dialout - log out and in (or reboot) once before connecting the CNC"
fi
mkdir -p "$HOME/Documents"

echo "--- 5/6 tests (no hardware)"
cd "$REPO"
if "$VENV/bin/python" -m unittest discover -s tests > "$HOME/henna_stage2_tests.log" 2>&1; then
  echo "Tests: $(grep -E '^Ran ' "$HOME/henna_stage2_tests.log")  OK"
else
  echo "Tests: $(grep -E '^Ran |^FAILED' "$HOME/henna_stage2_tests.log" | tr '\n' ' ')  - details in ~/henna_stage2_tests.log"
fi
"$VENV/bin/python" -c "from henna_preview import font_strokes as f; L=f.list_fonts(); print('Fonts:', len(L), '| handwriting:', [x['name'] for x in L if x['recommended']])"

echo "--- 6/6 desktop icon 'Henna Studio'"
chmod +x "$HERE/henna_studio.sh"
mkdir -p "$HOME/Desktop" "$HOME/.local/share/applications"
for f in "$HOME/Desktop/henna-studio.desktop" "$HOME/.local/share/applications/henna-studio.desktop"; do
  cat > "$f" <<EOF
[Desktop Entry]
Type=Application
Name=Henna Studio
Comment=Scan, design and hover run (prosthetic hand only)
Exec=bash $HERE/henna_studio.sh
Icon=$REPO/desktop_build/henna_icon_preview.png
Terminal=false
Categories=Graphics;
EOF
  chmod +x "$f"
done
gio set "$HOME/Desktop/henna-studio.desktop" metadata::trusted true 2>/dev/null || true
echo "=== stage 2 setup finished. Start Henna Studio from the desktop icon, or:  bash $HERE/henna_studio.sh"
