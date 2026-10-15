# Henna Studio on a Raspberry Pi 5 (port in progress)

**Branch `raspberry-pi`** (user decision 2026-10-12): all Pi work lives here, never on `main` (= the PC's Henna
Studio). PC Studio updates are brought into this branch only when the user decides (`git merge main` here - safe since
2026-10-13: main's "move the Pi work off main" revert is recorded as merged without being applied);
nothing from this branch is merged into `main`. On the Pi: `git checkout raspberry-pi`, then `git pull` as usual.

The Windows `HennaStudio.exe` cannot run on a Pi (Linux on ARM). Henna Studio is Python, so a Pi version is
being made in stages; each must work before the next. The laptop version is not changed by this work.

| Stage | What | Status |
|---|---|---|
| 1 | Camera: build Intel RealSense v2.58.4 (same as the laptop) on the Pi, check the D405 streams and records | **PASS** with 2.58.4: USB 3.2, 27.2 fps, 0.0001 m; Studio's repair + check on a Pi recording PASS |
| 2 | Pi version of Studio: `/dev/ttyUSB0`, Linux fonts, own browser window, setup script, desktop icon | scripts ready |
| 3a | Speed check (camera only): why a Pi scan was refused ('Position status became stale during recording') - SD card speed, swap, power/temperature, preview cost, recordings to SD and to RAM | **done** 2026-10-12: SD 76 MB/s, zram only, 5 A supply, no throttling; recordings never stalled (worst 0.44 s); preview 48 ms/frame (lookup table 6.2 ms) - the cause is not the Pi alone |
| 3b | CNC link check (sends only '?', no motion): machine-status age while the camera records | 2026-10-12: refusal reproduced in 5 of 6 runs - Python paused 2.0-2.5 s when the camera starts/stops (camera and CNC on different USB controllers); faster preview CPU 98 -> 13 % |
| 3c | Pi Studio (`webui_server_pi.py` = Studio v27 (since 2026-10-14; v23 from 2026-10-13, v22 before) + camera in its own process `camera_process.py` + faster preview `web_recorder_fast.py`) | **PASS** 2026-10-12: 3b phase E 3/3 OK (worst status age 0.29 s, no Python pause, preview CPU 17 %); 3 real Studio scans on the prosthetic, none refused (136 pairs, 0.0001 m, median 176.4-176.7 mm). Now started by `henna_studio.sh` / the desktop icon |
| 3 | Same tests as the laptop: unit tests, simulator, Home, scan position, hover run on the prosthetic | Home + scan position + scan PASS (3c); hand & region, design, program, air run next |

## Stage 1 (camera only - the CNC is not used)
Needs: Raspberry Pi 5 (4 GB is enough), 64-bit Raspberry Pi OS (Bookworm), internet, official power supply,
the D405 on a **blue USB 3 port**.
1. Copy the folder `raspberry_pi` to the Pi (for example download the repository ZIP from GitHub while signed in).
2. In a terminal on the Pi, in that folder:  `bash stage1_setup_camera.sh`  (about 1 hour; asks for your password)
3. Plug in the D405, then:  `~/henna-venv/bin/python stage1_camera_check.py`
4. Send back the lines after `=== STAGE 1 RESULT`.

Note: the Raspberry Pi Connect remote shell adds `^[[200~` / `~` when pasting - type commands, or delete the extra characters.

## Stage 2 (Studio on the Pi - setup does not touch the CNC or camera)
1. `cd ~/henna-program && git pull`
2. `bash ~/henna-program/raspberry_pi/stage2_setup_studio.sh`  (10-15 min): the laptop's exact package versions
   (`requirements-pi.txt`), fonts (URW Z003 script, Comic Neue), the approved hand model (SHA-256 checked),
   USB permission (group dialout - log out and in once), tests, and a **Henna Studio** desktop icon.
3. Start from the icon (or `bash ~/henna-program/raspberry_pi/henna_studio.sh`): the Pi Studio (`webui_server_pi.py`,
   see 3c) opens in its own browser window; closing the window stops Studio safely (a moving machine is stopped, the
   CNC disconnected). When `main` gets a new Studio version, change the one import in `webui_server_pi.py`.
The CNC appears as `/dev/ttyUSB0` (found automatically). Recordings go to `~/Documents`, logs to `~/henna_studio.log`.

## Stage 3a (speed check - camera only, the CNC is never used)
A Studio scan writes about 56 MB/s; the check measures whether the Pi keeps up.
1. `cd ~/henna-program && git pull`
2. Close Henna Studio. D405 on a blue USB 3 port, pointed at something still (a hand is fine - camera only).
3. `~/henna-venv/bin/python ~/henna-program/raspberry_pi/stage3a_pi_check.py`  (about 3 minutes, needs 2 GB free)
4. Send back the lines after `=== STAGE 3A RESULT`. Its test files are removed at the end; the full result stays
   in `~/henna_stage3a_check_<time>.json`.

## Stage 3b (CNC link check - sends only '?', the machine does not move)
Uses Studio's own link class, wrapped so that only '?' (GRBL status query) can reach the controller. No Home.
1. Close Henna Studio. CNC USB cable on the Pi, CNC power on; D405 on a blue USB 3 port.
2. `~/henna-venv/bin/python ~/henna-program/raspberry_pi/stage3b_cnc_link_check.py`  (about 2 minutes)
3. Send back the lines after `=== STAGE 3B RESULT` (full result: `~/henna_stage3b_check_<time>.json`).

## Stage 3c (Pi Studio - PASS 2026-10-12)
The desktop icon / `henna_studio.sh` start `webui_server_pi.py`: Studio v27 with the camera in its own process (a
camera freeze can no longer pause the CNC link) and the faster preview. The recording, machine link and all checks
are v27 unchanged. Since v23: Calibration (top bar) - status + daily check, see below.

## Studio v24-v27 on the Pi (from main, 2026-10-14)
- **Machine profiles (v24/v25):** the top bar shows the machine. On the Pi, first time: Calibration -> Machine ->
  choose **Ball screw (new machine)** or **Belt drive**, tick "This computer is connected to the machine I choose" ->
  **Use this machine** (only while disconnected). Each machine has its own scan position, travel Z, floor and
  calibration (`machines/registry.json`, `machines/<id>/`).
- **Calibration in Studio (v26):** Recalibrate / dot sheet / X/Y nudge / block touches / Approve - as on the laptop.
  The ball-screw calibration approved 2026-10-14 12:57 is in git and works on the Pi (same camera 260322278767).
- **Travel clearance (v27):** checks, programs and runs refuse a hand whose top is less than
  max(standoff, 5 mm hop) + 3 mm below the travel height, and say so (or "something between the camera and the
  hand" for a hanging cable).
- **Real-hand hover runs (v28):** New job -> **Real hand - hover run** (Real hand - camera only stays as before).
  6 mm above the skin, 300 mm/min, the v27 clearance; before Start the camera checks the hand is where it was scanned;
  during the run it watches (5 checks/s) and PAUSES the machine if the hand moves > 2 mm, comes within 1.5 mm of the
  tip, or is out of sight / no picture for 1.5 s. Resume re-checks first. The camera process makes the depth samples.
  **Prove it first with the prosthetic in a real-hand job: move it on purpose during the run - the machine must pause.**
- **Touch layout (Pi only):** portrait, x2 zoom, machine bar at the bottom, drag-to-scroll, Studio's own confirm box,
  Home and Move to scan position in one tap.
- **Daily-check reference is per device** (`outputs/` is not in git): set it once on the Pi - Calibration ->
  Set the reference - with the bed exactly as for every check (the taped dot sheet), then Run daily check -> OK.

## Real hand R1 - hand height (camera only: the machine never moves over the hand)
2026-10-12: a real-hand design check was refused - "travel Z does not clear the hand top + standoff". This measures
why. In Studio: New job -> **Real hand**; Move to scan position FIRST, then place the hand; Record; Hand & region.
Then: `~/henna-venv/bin/python ~/henna-program/realhand_height_check.py` (newest real-hand job; `--job <id>`).
It prints the hand top (the same number as Studio's check), the margin to travel Z -20 with the standoff, the
highest hand points, and saves a picture under `outputs/realhand_checks/`. Same result as Studio on the prosthetic
job 20261010-140217-job: hand top -31.79 (margin 7.79 mm).

## Calibration - status and daily check (Studio v23, camera only)
Top bar **Calibration**. Status = the approved calibration in use (read-only). Daily check: bed EMPTY, Connect + Home,
tick the boxes, **Move to scan position**, tick "bed empty", **Run daily check** (about 30 s) -> OK / CHECK /
RECALIBRATE (height, tilt, sideways shift, same camera) or UNCLEAR (something on the bed). First time only:
**Set the reference** right after a known-good calibration. Results: `outputs/calibration/`, one log row per check.

## Push buttons - PAUSE and STOP (2026-10-15)
- **PAUSE** (yellow, momentary, normally-open): Pi header **pin 11 (GPIO17)** and **pin 9 (GND)**, no resistor.
  A press **pauses a running program** (feed hold, position kept; Resume on the screen, which re-checks a real hand)
  and **stops any other motion** (jog, homing, move to scan: feed hold + reset, home again). Idle: nothing. Every
  press is a log row. Works only while Studio runs on the Pi. The machine bar shows **Pause btn** (green, watched) or
  **Pause btn OFF** (red: not working - use the screen). Code: `henna_preview/pause_button.py`.
- **STOP** (red, latching mushroom, normally-closed): in the **+ wire from the power supply to the DLC32**. It cuts
  the motors whatever the software does; home again after releasing it. Not connected to the Pi.

## Paste SSR on the Pi (Studio v29, 2026-10-15)
- SSR input **+ (terminal 3)** to **pin 13 (GPIO27)**, input **- (terminal 4)** to **pin 14 (GND)** - never to the
  5 V pins 2 / 4 (always on). GPIO27 is OFF from power-on until Studio switches it.
- Program step -> **Paste: Servo dispenser (SSR)** or Pneumatic -> **Test dispenser (1 s)**: the SSR's input LED
  lights for about 1 s, the machine does not move. In a run: ON after each drop, OFF before each lift, OFF at once
  on Pause / STOP / errors / closing Studio. Code: `henna_preview/paste_gpio.py`.
- The SSR-25DA switches **AC loads only** (24-480 V AC); a DC valve or a low-voltage trigger needs a DC SSR or a
  relay module.
