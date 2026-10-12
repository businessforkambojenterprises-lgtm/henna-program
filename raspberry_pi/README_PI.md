# Henna Studio on a Raspberry Pi 5 (port in progress)

The Windows `HennaStudio.exe` cannot run on a Pi (Linux on ARM). Henna Studio is Python, so a Pi version is
being made in stages; each must work before the next. The laptop version is not changed by this work.

| Stage | What | Status |
|---|---|---|
| 1 | Camera: build Intel RealSense v2.58.4 (same as the laptop) on the Pi, check the D405 streams and records | **PASS** with 2.58.4: USB 3.2, 27.2 fps, 0.0001 m; Studio's repair + check on a Pi recording PASS |
| 2 | Pi version of Studio: `/dev/ttyUSB0`, Linux fonts, own browser window, setup script, desktop icon | scripts ready |
| 3a | Speed check (camera only): why a Pi scan was refused ('Position status became stale during recording') - SD card speed, swap, power/temperature, preview cost, recordings to SD and to RAM | **done** 2026-10-12: SD 76 MB/s, zram only, 5 A supply, no throttling; recordings never stalled (worst 0.44 s); preview 48 ms/frame (lookup table 6.2 ms) - the cause is not the Pi alone |
| 3b | CNC link check (sends only '?', no motion): machine-status age while the camera records, from a stopped camera / after Studio's preview / after the faster preview | script ready |
| 3c | Faster live preview on the Pi (`henna_studio_fast.sh` = Studio v22 + `web_recorder_fast.py`) | ready to try |
| 3 | Same tests as the laptop: unit tests, simulator, Home, scan position, hover run on the prosthetic | after stage 3b |

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
3. Start from the icon (or `bash ~/henna-program/raspberry_pi/henna_studio.sh`): Studio v22 opens in its own browser
   window; closing the window stops Studio safely (a moving machine is stopped, the CNC disconnected).
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

## Stage 3c (faster live preview - try after stage 3b)
`bash ~/henna-program/raspberry_pi/henna_studio_fast.sh` starts Studio v22 with the faster preview (the recording,
machine link and checks are unchanged). `henna_studio.sh` and the desktop icon still start the normal v22.
