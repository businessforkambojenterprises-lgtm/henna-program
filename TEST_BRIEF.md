# Test brief for an outside reviewer (ChatGPT / Codex)

Please **test and review** Henna Studio. Report findings; do not change code unless asked.

## Absolute limits
- **No hardware.** Do not connect to the CNC (COM7) and do not start the camera. Do not run
  `webui_server_v1x.py` or `HennaStudio.exe` on port 8765 with a machine attached. Use the simulator below.
- Do not edit `current_calibration.json`, the `validation/` folder, `validation_log_S2026-10-05-A.csv`,
  or any `step1*`/`step20`-`step22` script. Do not commit or push.
- `motion_ready = false`: nothing is validated for a person. No dispensing exists yet.

## What it is (read first)
`CLAUDE.md` (rules and findings), `STUDIO_V14.md` (v14-v17 changes), `README.md`.
Current version: **Studio v22** = scan -> hand & region -> design -> program -> run, all from the app:
v14 Studio streams the program itself (Start / Pause / Resume / STOP, jog >= Z -20), v15 fresh position and
verified Pause, v16 drawing speed 300-1200 mm/min (drops to the hand <= 300), v17 low hops between strokes
(5 mm above the highest measured skin under each crossing; `step23_design_hop_program.py`), v18 write a name
(single-stroke capitals, `henna_preview/stroke_font.py`), v19 names in any installed font
(`henna_preview/font_strokes.py`), v20 fixes from the first review
(`henna_preview/grbl_program_link_v20.py`), v21 fixes from the second review (`grbl_program_link_v21.py`,
`henna_preview/name_files_v21.py`, `webui_server_v21.py`), v22 fix from the third review (`grbl_program_link_v22.py`,
`webui_server_v22.py`; same page as v20; see STUDIO_V14.md).

## How to test (Windows, project folder, existing `.venv`)
1. Unit tests (no hardware):
   `.venv\Scripts\python.exe -m unittest discover -s tests` (expect 321 OK) and
   `cd desktop_build` then `..\.venv\Scripts\python.exe -m unittest discover -p "test_*.py"` (expect 25 OK).
2. Simulator (fake controller, no camera, temp jobs): `.venv\Scripts\python.exe .claude\studio_v17_sim.py`
   (demo job) or `.claude\studio_v19_sim.py` / `.claude\studio_v22_sim.py` (temporary copy of the desktop app's real jobs; Design step + names),
   open http://127.0.0.1:8767. Connect (SIM) -> Home -> open the demo job -> Run -> Start / Pause / Resume / STOP,
   Jog dialog. Ctrl+C to stop.
3. Packaged app smoke test (isolated app copy, never opens hardware), if a build exists:
   `.venv\Scripts\python.exe -B desktop_build\smoke_bundle.py --bundle outputs\HennaStudio-Windows-x64-v20 --work outputs\smoke-review --port 8877`
4. Offline hop check on real data is described in `STUDIO_V14.md` (step 23 on a copied app folder).

## Please look especially at
- **Safety of motion code:** `henna_preview/grbl_program_link.py`, `grbl_program_link_v15.py`,
  `grbl_scan_link.py` - can any path send a line that is not in the allowlist, keep moving after STOP/Pause,
  or start without homing? Thread/race issues (`_lock`, tokens, `_pending`).
- **Program checks:** `henna_preview/program_reread_v2.py`, `hop_path.py`, `webui_server_v16.py`
  (`slow_descents`, `check_feeds`), `webui_server_v17.py` (`checked_program`) - can a too-low hop, a fast drop,
  or a changed file get through?
- **Server guards:** confirmations, prosthetic-only, 4 mm minimum, busy-machine 409 guard, jog inputs.
- **Desktop launcher:** `desktop_build/henna_desktop.py` - window close shuts down safely, console hiding.
- **Names:** `henna_preview/stroke_font.py`, `font_strokes.py`, `webui_server_v18.py`/`v19.py` - odd input,
  file names, reuse, fonts that trace badly.
- **UI:** `webui/studio_v20.html` in the simulator - confusing states, wrong enable/disable, accessibility.

## Report format
For each finding: file:line, what can go wrong (concrete scenario), severity (safety / bug / UX / style),
and a suggested fix. Separate "confirmed by a test or simulator run" from "suspected from reading".
