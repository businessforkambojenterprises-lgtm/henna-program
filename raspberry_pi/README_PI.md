# Henna Studio on a Raspberry Pi 5 (port in progress)

The Windows `HennaStudio.exe` cannot run on a Pi (Linux on ARM). Henna Studio is Python, so a Pi version is
being made in stages; each must work before the next. The laptop version is not changed by this work.

| Stage | What | Status |
|---|---|---|
| 1 | Camera: build Intel RealSense v2.58.4 (same as the laptop) on the Pi, check the D405 streams and records | **PASS** with 2.58.4: USB 3.2, 27.2 fps, 0.0001 m; Studio's repair + check on a Pi recording PASS |
| 2 | Pi version of Studio: `/dev/ttyUSB0`, Linux fonts, own browser window, setup script, desktop icon | scripts ready |
| 3 | Same tests as the laptop: unit tests, simulator, Home, scan position, hover run on the prosthetic | after stage 2 |

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
