# Ai+d405 – henna preview prototype (handoff, revision 2)

Offline preview of: recorded colour + depth → detect hand → user-reviewed region →
simple design → projection onto measured depth → 3D preview → report.

**motion_ready = false everywhere.** Nothing here talks to the D405 live, the
MKS DLC32 / GRBL controller, or any serial port, and nothing produces G-code.
Original recordings and scans are only read; every report records their checksum.
`README.md` (revision 1) is kept unchanged.

## Setup (Windows, this PC)

Everything runs in the project environment `.venv` (Python 3.11.9). Your main
Python is untouched. To rebuild the environment on another PC:

```
py -3.11 -m venv .venv
.venv\Scripts\python -m pip install -r requirements-step04.txt
```

Approved packages: mediapipe 1.0.1, opencv-contrib-python 5.0.0.93, numpy 2.4.6,
matplotlib 3.11.2, pyrealsense2 2.58.4.10922 (file playback only) and their
dependencies. Do not add `opencv-python` (it conflicts with the contrib build).

Hand model: `models/hand_landmarker.task` (see `models/MODEL_INFO.txt` for source
and SHA-256). The code refuses any other file.

## Usage

```
cd Desktop\Ai+d405
.venv\Scripts\python webui_server_v5.py
```

Open http://127.0.0.1:8765 (this PC only). Stop with Ctrl+C.

1. **1–2 Recording and hand detection** – pick a `.db3`/`.bag` from `Documents`,
   pick a frame, press *Detect hand*. Check the landmarks and any red marks
   (landmarks on missing depth).
2. **3 Reviewed region** – adjust the suggested palm outline (drag corners,
   double-click to add, right-click to remove), *Check region*, look at the 3D
   view, then *Accept region*. Nothing later works without an accepted region.
3. **5–6 Design and projection** – choose a circle or short curve, size,
   rotation, depth-jump limit; click the image to move it. Green = path on
   measured depth; red = rejected or broken. *Save preview*.
4. **7 Report** – pick the saved design, *Build report*, download
   `final_report.json`.
5. **4 Measured surface** – inspects older depth-only `.npz` scans.

Recording tip (RealSense Viewer): Stereo Module, 640×480, 30 fps, Depth + Color
on, record 3–5 s with the head still and the controller off. Spread the
fingers slightly if you want to test finger-gap breaks.

Command-line equivalents: `step02_inspect_depth.py`, `step04_hand_detect.py`,
`step05_region.py`, `step06_design.py`, `step07_report.py` (each has `--help`).

Tests (62): `.venv\Scripts\python -m unittest discover -s tests`

## Example preview (from your approved inputs)

`outputs/example_preview/` – recording `20261001_083523.db3`, frame 215, your
accepted (edited) region, 60 mm circle: complete and unbroken, 363/363 samples on
measured depth, 344 measured path points, 0 interpolated, recording checksum
re-verified.

## Measured on this PC (single frames, not a real-time claim)

| Stage | Time |
|---|---|
| Read + align one frame from the recording | 0.7–1.5 s (later frames take longer) |
| MediaPipe hand detection | ~0.6 s (+ ~5 s first import) |
| Region check, ~20k points to 3D | ~0.1 s |
| Design projection | 0.01–0.07 s |

## Known limitations

- Single frame only. No averaging over frames, no motion check while recording.
- Landmarks only *suggest* the region; the boundary is your decision.
  No segmentation model (SAM 2.1 etc.) has been added or benchmarked.
- Design size uses the depth at its centre, so it is approximate on tilted or
  curved skin. Path length is summed between whole pixels and reads 5–15 % long.
- Depth-jump breaks are proven on test data only; the real recording had the
  fingers together.
- Viewer post-processing state is not provable from the file; frames are
  treated as raw.
- Coloured PLY files are not supported for the full workflow (no image,
  pixel correspondence, timing or calibration).
- The 3D views show at most 30,000 points.

## Not validated – required before any machine work

These are requirements, not features. None are implemented here.

- Camera-to-nozzle offset (pencil-tip coordinates are not nozzle-tip coordinates).
- Z / tool reference; cause of the earlier descent into the bed.
- Soft limits failing to stop excessive Z travel; Pn.
- Head pose at capture (the camera moves with the head) and its recording.
- Fresh calibration sessions only; do not mix older ones.
- A separate motion-control component, kept apart from this preview code.
