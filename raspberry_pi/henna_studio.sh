#!/usr/bin/env bash
# Start Henna Studio v22 on the Raspberry Pi in its own browser window (no address bar).
# Closing the window stops Studio safely: Ctrl+C to the server = a moving machine is stopped (feed hold + reset),
# the CNC is disconnected and the camera released - the same shutdown as on the laptop.
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(dirname "$HERE")"
PORT=8765
URL="http://127.0.0.1:$PORT"
LOG="$HOME/henna_studio.log"
BROWSER="$(command -v chromium-browser || command -v chromium)"
cd "$REPO" || exit 1

if curl -s -o /dev/null "$URL/"; then          # already running: just show it
  exec "$BROWSER" --app="$URL" --user-data-dir="$HOME/.henna-studio-browser"
fi
echo "=== $(date) start" >> "$LOG"
"$HOME/henna-venv/bin/python" webui_server_v22.py >> "$LOG" 2>&1 &
SERVER=$!
for _ in $(seq 1 240); do
  curl -s -o /dev/null "$URL/" && break
  kill -0 "$SERVER" 2>/dev/null || { zenity --error --text="Henna Studio could not start - see $LOG" 2>/dev/null; exit 1; }
  sleep 0.5
done
"$BROWSER" --app="$URL" --user-data-dir="$HOME/.henna-studio-browser" --no-first-run --password-store=basic \
  --start-maximized >/dev/null 2>&1
kill -INT "$SERVER" 2>/dev/null          # window closed -> safe shutdown
wait "$SERVER"
echo "=== $(date) stopped" >> "$LOG"
