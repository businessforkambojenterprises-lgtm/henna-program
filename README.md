# Ai+d405 – henna preview prototype

Offline preview only. `motion_ready = false` everywhere. Nothing here talks to
the camera, the MKS DLC32 / GRBL controller or any serial port, and nothing
produces G-code. Original data files are only read.

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
