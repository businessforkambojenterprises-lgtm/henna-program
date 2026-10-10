# Ai+d405 – henna preview prototype

`motion_ready = false` everywhere. Studio v13 can read machine position, home Z
then all axes, and move to one fixed scan position after the travel area is cleared.
Hover programs remain **prosthetic-only** (no person, no paste); programs and other
manual moves are run in UGS. Real-hand jobs support camera preview only.

Moving to a new laptop: see `NEW_LAPTOP_SETUP.md`. The v13 changes and validation
limits are documented in [SCAN_POSITION_V13.md](SCAN_POSITION_V13.md).

## Run the Windows app

1. Open the extracted **HennaStudio-Windows-x64** folder on your Desktop.
2. Double-click **HennaStudio.exe**. Keep its console window open.
3. Your browser opens [Henna Studio](http://127.0.0.1:8765).
   If it does not open, enter that address yourself.
4. Close RealSense Viewer before using Scan. Disconnect UGS before connecting
   Studio to the controller; only one app can use each device at a time.
5. In Studio, **Connect**, clear all hands and objects from the travel area,
   then **Home** and confirm. In Scan, select **Move to scan position** and confirm.
6. Wait for **Position ready**, place the hand, then choose **Record scan**.
   A verified scan automatically opens Hand & region and starts detection.

Keep `HennaStudio.exe`, `_internal`, and `app` together. Python is not required
for the packaged app. Press Ctrl+C in the console to close it after operations
finish. If port 8765 is already in use, close the previous Studio instance first.

The new scan-position movement has been tested with a simulated controller;
its first physical machine validation is still pending. Never move over a person.

## Run Studio v13 from source

For a fresh clone, follow `NEW_LAPTOP_SETUP.md` first. The ignored hand model must
be obtained and its SHA-256 verified using `models/MODEL_INFO.txt` before use.

In a Command Prompt, using the existing project Python environment:

```bat
cd /d "%USERPROFILE%\Desktop\Ai+d405"
.venv\Scripts\python.exe webui_server_v13.py
```

Open http://127.0.0.1:8765 and follow the same workflow above. Keep the terminal
open while working. This source checkout and a packaged app have separate `app`
data folders; changing source does not automatically update an extracted package.
The repeatable Windows build recipe is in [desktop_build/BUILDING.txt](desktop_build/BUILDING.txt).

## Workflow

1. **Scan** - confirmed fixed machine positioning, automatic position reading,
   live camera and 5-second recording. Files are fixed and verified before use:
   depth units 0.0001 m, stored camera settings and plausible depth.
2. **Hand & region** - automatic detection; edit and accept the outline.
3. **Design** - gallery or uploaded line-art SVG; size, rotate and drag it onto
   the hand. "Check on measured skin" plans every stroke, all or nothing.
4. **Surface check** - prosthetic only; three paper-feeler readings and an offset
   accepted only if readings agree.
5. **Program** - prosthetic only; written by step 22 and independently re-read.
6. **Run & verify** - disconnect Studio, run in UGS, then check the paper gap.

Jobs are saved in `outputs/webui/<time>-job/job.json`; readings and movement
stages are logged in `validation_log_S2026-10-05-A.csv`. Real-hand jobs stop at
design preview. Fixed calibration and path rules are in `current_calibration.json`.
Older server revisions and the Classic page remain available for reference.

## Early tools (offline previews)

## Setup

Uses Python 3.11 with packages already on this PC: numpy, matplotlib.
No installs were needed for the steps built so far.

## Run the web UI

```
cd Desktop\Ai+d405
python webui_server.py
```

Open http://127.0.0.1:8765 in a browser. Stop with Ctrl+C in the terminal.

1. Pick a depth file. Only `.npz` files in approved folders are listed
   (currently `%USERPROFILE%\palm_3d_scans`). Files without depth + intrinsics are greyed out.
2. Choose the depth units. Tick "confirmed" only if you know them.
3. Press Run. You get the depth/status picture, a rotatable 3D view of measured
   points, warnings and the report. Each run is saved in `outputs/webui/<time>/`.

The server listens on 127.0.0.1 only. To approve another input folder, add it to
`APPROVED_DIRS` in `webui_server.py`.

## Command line (same result)

```
python step02_inspect_depth.py --depth-units mm
```

## Tests

```
python -m unittest discover -s tests -v
```

## What exists so far

| Stage | Status |
|---|---|
| Measured surface from depth (camera-frame mm, holes kept) | built |
| Web UI for the above | built |
| Colour + depth recording (.bag) loading | waiting for a recording |
| Hand detection (MediaPipe) | needs colour data and an approved install |
| Reviewed region, design placement, projection, full report | not built |

## Known limitations

- `ptg_scan_raw.npz` has no colour image, no distortion coefficients and no stated depth units.
- The 3D view shows at most 30,000 points (evenly subsampled) for speed.
- Processing times are from this PC for single files; no real-time claim.
- Coordinates are camera-frame only. There is no machine mapping.
