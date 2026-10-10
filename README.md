# Ai+d405 – henna preview prototype

`motion_ready = false` everywhere. The studio (v12) talks to the MKS DLC32 / GRBL controller only to
read its position and to home it; programs and all other moves are opened and run by the user in UGS.
Hover programs are written only for the PROSTHETIC hand (no person, no paste).

Moving to a new laptop: see `NEW_LAPTOP_SETUP.md`. Project briefing for Claude: `CLAUDE.md`.

## Henna Studio (web UI v12) - the main way to work

In a Command Prompt (keep the window open while you work):

```
cd /d "%USERPROFILE%\Desktop\Ai+d405"
.venv\Scripts\python.exe webui_server_v12.py
```

`webui_server_v12.py` adds a machine link on the CH340 USB port, found automatically (pyserial 3.5): the page reads the
position (the "Use machine position" buttons fill the ? boxes) and can home ($HZ then $H,
after two confirmations) with a STOP button. It cannot send any other G-code - programs
still run in UGS. Disconnect UGS before pressing Connect (one program per port).
`webui_server_v11.py` is the same studio without the machine link.

Open http://127.0.0.1:8765 (the older tabbed page is at /classic). Close RealSense
Viewer first: only one program can use the camera.

1. **Scan** - live view; park the head at pose 1 (Z-40, X145.8 Y42.6), paste the `?`
   line, record 5 s. Each file is fixed (late repeated camera-option messages) and
   checked: depth units 0.0001 m, camera settings stored, plausible depth.
2. **Hand & region** - automatic hand detection; edit and accept the outline.
3. **Design** - gallery or upload a line-art SVG; size, rotate and drag it on the hand;
   "Check on measured skin" plans every stroke (all or nothing).
4. **Surface check** - three paper-feeler readings; offset accepted only if even.
5. **Program** - written by step 22 and re-read by a separate checker.
6. **Run & verify** - run in UGS, then a paper gap check (PASS / TOO CLOSE).

Every job is saved in `outputs/webui/<time>-job/job.json` and every reading is logged
in `validation_log_S2026-10-05-A.csv`. A "Real hand" job is camera only: it stops at
the design preview. Calibration and path rules: `current_calibration.json`.

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
