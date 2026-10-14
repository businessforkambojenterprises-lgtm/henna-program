#!/usr/bin/env bash
# Start Henna Studio on the Raspberry Pi in its own browser window (no address bar): webui_server_pi.py =
# Studio v27 (machine profiles, calibration in Studio, travel clearance) with the camera in its own process and the
# faster preview (branch raspberry-pi).
# Closing the window stops Studio safely: Ctrl+C to the server = a moving machine is stopped (feed hold + reset),
# the CNC is disconnected and the camera released - the same shutdown as on the laptop.
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(dirname "$HERE")"
PORT=8765
URL="http://127.0.0.1:$PORT"
LOG="$HOME/henna_studio.log"
BROWSER="$(command -v chromium-browser || command -v chromium)"
# Touch Display 2 (5", 1280 x 720 on 110 x 62 mm): x2 zoom -> 640 x 360 CSS px for webui/pi_touch.css.
# Another display: start with HENNA_SCALE=1.5 (or 1 for a large monitor).
SCALE="${HENNA_SCALE:-2}"
# Touch: native Wayland where the desktop runs it (else X11) and touch events on, so a finger swipe scrolls the page
# instead of acting as a mouse drag (user 2026-10-14: "not able to scroll by touching").
TOUCH=(--ozone-platform-hint=auto --touch-events=enabled)
cd "$REPO" || exit 1

if curl -s -o /dev/null "$URL/"; then          # already running: just show it
  exec "$BROWSER" --app="$URL" --user-data-dir="$HOME/.henna-studio-browser" --force-device-scale-factor="$SCALE" \
    "${TOUCH[@]}"
fi
echo "=== $(date) start" >> "$LOG"
"$HOME/henna-venv/bin/python" webui_server_pi.py >> "$LOG" 2>&1 &
SERVER=$!
for _ in $(seq 1 240); do
  curl -s -o /dev/null "$URL/" && break
  kill -0 "$SERVER" 2>/dev/null || { zenity --error --text="Henna Studio could not start - see $LOG" 2>/dev/null; exit 1; }
  sleep 0.5
done
"$BROWSER" --app="$URL" --user-data-dir="$HOME/.henna-studio-browser" --no-first-run --password-store=basic \
  --force-device-scale-factor="$SCALE" "${TOUCH[@]}" --start-maximized >/dev/null 2>&1
kill -INT "$SERVER" 2>/dev/null          # window closed -> safe shutdown
wait "$SERVER"
echo "=== $(date) stopped" >> "$LOG"
