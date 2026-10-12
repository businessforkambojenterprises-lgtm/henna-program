# Ai+d405 - henna dispensing prototype (project briefing for Claude)

Read this first. It replaces the long chat history (an export of it is in the transfer package).
Detailed history: `validation_log_S2026-10-05-A.csv` (every machine reading, decision and result) and `git log`.

## What this is
A RealSense **D405** camera is mounted on the head of a GRBL CNC (**MKS DLC32**, ESP32, CH340 USB serial).
Since 2026-10-10 it is driven from **Henna Studio** (current **v21**; no UGS steps; UGS stays installed). Goal: dispense henna designs on a hand. Today: the nozzle *hovers* a design over a
**prosthetic** hand; the dispenser is **not connected** yet (no valve control, no paste).

## Non-negotiable safety rules
- `motion_ready = false` everywhere. Nothing is validated for a person.
- Hover programs only for the **PROSTHETIC hand** (step 20/22 and the studio refuse other subjects). A real
  hand is **camera only** (scan, region, design preview) - never paper readings, programs or moves over it.
- Home with **`$HZ` then `$H`** at the start of every session (Z must go first - lost-position root cause).
- Capture pose 1 = **G53 Z-40, then X145.8 Y42.6**. Calibration is valid only there.
- Programs: G53 only; travel Z -20; nozzle >= measured skin + 1 mm (floor); feeds 300 draw / 1000 travel;
  every program is re-read by an independent checker. Studio v14: nozzle **4 mm or more** above measured skin,
  never an offset - the paper-feeler steps were removed (user decision 2026-10-10). Studio v16: drawing speed
  chosen per program, 300-1200 mm/min, default 600 (user decision 2026-10-10); drops to the hand stay <= 300.
  Studio v17 (step 23): between strokes only a low hop = highest measured skin under the crossing (+-1.5 mm band)
  + 5 mm (user decision 2026-10-10); unmeasured crossings keep travel Z -20; start/end at travel Z.
- No dispensing commands (M/S/T, $ lines) in any program until a dispensing plan is approved.
- Page-driven motion (user decisions 2026-10-10): Studio may **home**, move to the **fixed scan position**
  (v13), **run the job's own checked prosthetic program** (v14: Start / Pause / Resume / STOP; the file must
  be unchanged since it was written, re-read again, four confirmations) and **jog** in 0.1/1/10 mm steps
  only at or above travel Z -20 (v14). Any other move from the page needs a new explicit approval. Real runs on
  the prosthetic since 2026-10-10 (v14-v19: designs, names, 300-600 mm/min, low hops; no touch).
- New code goes into **new files/revisions**; validated scripts stay unchanged. Ask before installing packages.

## How the user likes to work
- When they ask "what to do?": short numbered steps, one point at a time, each Studio action (or command)
  on its own, expected result and a hard stop limit. They do not want UGS steps any more.
- Ask for a clear yes before commits, machine runs, and any safety-rule change. Log every machine reading.
- Real-world checks beat assumptions: verify with paper-feeler readings and side-view videos.

## Main workflow: Henna Studio (web UI v21 = v20 + second review fixes)
Start in a Command Prompt (keep the window open; the Claude terminal tool tends to interrupt the slow start):
```
cd /d "%USERPROFILE%\Desktop\Ai+d405"
.venv\Scripts\python.exe webui_server_v21.py
```
Open http://127.0.0.1:8765 (older tabbed page: /classic). Close RealSense Viewer and UGS first (one user per device).
Steps: 1 Scan (Connect, Home, Move to scan position, record 5 s, file fixed + verified) - 2 Hand & region
(MediaPipe, editable outline) - 3 Design (gallery / SVG upload, size, rotate, drag; nozzle 4-6 mm; "Check on
measured skin" = step 22 plan, all or nothing) - 4 Program (step 22 + independent re-read + SHA-256) - 5 Run
(Studio streams it; Pause = feed hold, STOP = hold + reset, home again). Jobs: `outputs/webui/<time>-job/job.json`.
The packaged desktop app (`Desktop\HennaStudio-Windows-x64`) runs **v21 (0.21.0.1)** (earlier files in
`backup-before-v14` ... `backup-before-v21`); 0.20.1 removes the Windows download mark from its own window DLLs
(other PCs) and falls back to the browser if the window cannot open; it has its own `app\` data folder (jobs, log). Since 0.16.2 it opens in its own native window (pywebview/WebView2,
Desktop shortcut 'Henna Studio'); closing the window stops Studio safely. Simulators: `.claude/studio_v17_sim.py` (demo job), `.claude/studio_v19_sim.py` (copy of the desktop app's real jobs), `.claude/studio_v21_sim.py` (v21).
Raspberry Pi 5 port: `raspberry_pi/` (README_PI.md) - stage 1 camera PASS, stage 2 Studio runs on the Pi;
open issue: a Pi scan was refused ('Position status became stale during recording', likely slow SD writes).

## Key files
| File | Purpose |
|---|---|
| `current_calibration.json` | approved calibration paths + fixed path rules (single source for the web UI) |
| `webui_server_v21.py`, `henna_preview/grbl_program_link_v21.py`, `henna_preview/name_files_v21.py` | v21: STOP warning until Home, each run recorded from its own data, name file fixes (same page as v20) |
| `webui_server_v20.py`, `webui/studio_v20.html`, `henna_preview/grbl_program_link_v20.py` | v20: review fixes (STOP reset, confirmed Pause, run snapshot) |
| `webui_server_v19.py`, `webui/studio_v19.html`, `henna_preview/font_strokes.py` | v19: names in any installed font (centre lines) |
| `webui_server_v18.py`, `webui/studio_v18.html`, `henna_preview/stroke_font.py` | v18: write a name (single-stroke A-Z) |
| `webui_server_v17.py`, `webui/studio_v17.html`, `step23_design_hop_program.py` | v17: low hops between strokes |
| `henna_preview/hop_path.py`, `henna_preview/program_reread_v2.py` | hop planning + check; re-read allowing hops |
| `webui_server_v16.py`, `webui/studio_v16.html` | v16: v15 + drawing speed per program (drops capped at 300) |
| `webui_server_v15.py`, `henna_preview/grbl_program_link_v15.py` | v15: v14 + fresh position during runs + verified Pause |
| `webui_server_v14.py`, `webui/studio_v14.html` | studio v14: Studio runs the checked prosthetic program (page used by v15) |
| `henna_preview/grbl_program_link.py` | v14 link: v13 homing + scan position, plus line-by-line program run |
| `webui_server_v13.py`, `henna_preview/grbl_scan_link.py` | v13: homing + fixed scan position (Codex) |
| `webui_server_v12.py`, `webui/studio.html` | studio + machine link (v11 = same without link, v10 = tabbed) |
| `henna_preview/web_recorder.py` | live camera + recording; fixes late repeated option messages, verifies files |
| `henna_preview/grbl_link.py` | USB link: only `?`, `$HZ`, `$H`, feed hold + reset |
| `henna_preview/recording_multi.py` | 15-frame per-pixel median depth (unstable/noisy pixels rejected, never filled) |
| `henna_preview/svg_design.py` | line-art SVG reader and placement in machine X/Y |
| `henna_preview/surface_offset.py` | paper-feeler offset: smallest of >= 3, raise only, refused if spread > 0.5 mm |
| `step21_design_preview.py`, `step22_design_hover_test.py` | design preview / prosthetic hover program |
| `validation/S2026-*/` | settings and results of every validated session |
| `make_move_package.py`, `restore_on_new_pc.bat`, `setup_new_laptop.bat` | move to a new PC (`NEW_LAPTOP_SETUP.md`) |

## Hard-won findings (do not re-derive)
- pyrealsense2 2.58 recordings: a late repeated 'Error Polling Enabled' option makes playback report depth
  units 0.001 instead of 0.0001 (every height 10x wrong). The web recorder removes only late *repeats* and
  verifies every file. Web scans match RealSense Viewer scans to 0.1 mm.
- X/Y calibration was 4.8 mm off at block height when built from hover-over-dots; corrected by nozzle touches
  ("blockshift"). Z k = 141.47 mm. Camera depth drifts about 0.5 mm over a day.
- D405 on plain silicone reads 2-3 mm too deep; dense pen texture fixes the gaps and makes the error even
  (about 1.3-1.7 mm). Real skin is also translucent: its error must be measured (camera-only), never assumed.
- Eye touches on soft silicone repeat to about 1 mm; a 0.10 mm paper feeler to about 0.4 mm.
- The prosthetic surface drifts 0.25-1 mm over about 30 min (rocks on its rounded back): real margin is
  about 2 mm with either 3 mm + offset or 4 mm standoff.

## Open items / next steps
- Dispenser: not connected. A dispensing plan needs its own approval (start on paper, not a hand).
- Desktop rebuild recipe: `desktop_build/BUILDING.txt` (PyInstaller is in the ignored `desktop_build/build_tools`).
- Raspberry Pi: fix the stale-status refusal during recording (stage 3), then the same real tests as the laptop.
- Real hand: camera-only accuracy test (paper dots) and a 30 s hand-movement test were planned.
- Low hops between strokes: done in v17 (5 mm above the skin); air run OK and prosthetic run OK at 600 mm/min
  (2026-10-10, no touch). Next big step: dispensing (needs the dispenser details and its own approved plan).
- Rigid textured hand cast for exact camera measurements.
