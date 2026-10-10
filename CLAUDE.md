# Ai+d405 - henna dispensing prototype (project briefing for Claude)

Read this first. It replaces the long chat history (an export of it is in the transfer package).
Detailed history: `validation_log_S2026-10-05-A.csv` (every machine reading, decision and result) and `git log`.

## What this is
A RealSense **D405** camera is mounted on the head of a GRBL CNC (**MKS DLC32**, ESP32, CH340 USB serial,
run by hand in **UGS**). Goal: dispense henna designs on a hand. Today: the nozzle *hovers* a design over a
**prosthetic** hand; the dispenser is **not connected** yet (no valve control, no paste).

## Non-negotiable safety rules
- `motion_ready = false` everywhere. Nothing is validated for a person.
- Hover programs only for the **PROSTHETIC hand** (step 20/22 and the studio refuse other subjects). A real
  hand is **camera only** (scan, region, design preview) - never paper readings, programs or moves over it.
- Home with **`$HZ` then `$H`** at the start of every session (Z must go first - lost-position root cause).
- Capture pose 1 = **G53 Z-40, then X145.8 Y42.6**. Calibration is valid only there.
- Programs: G53 only; travel Z -20; nozzle >= measured skin + 1 mm (floor); feeds 300 draw / 1000 travel;
  standoff 3 mm + accepted offset, or 4 mm without. Every program is re-read by an independent checker.
- No dispensing commands (M/S/T, $ lines) in any program until a dispensing plan is approved.
- The web page may **read position and home** only (user decision 2026-10-09). Running programs and other
  moves from the page were **not approved** - they stay in UGS until the user explicitly approves a plan.
- New code goes into **new files/revisions**; validated scripts stay unchanged. Ask before installing packages.

## How the user likes to work
- When they ask "what to do?": short numbered steps, one point at a time, each UGS command in its own code
  block, expected result and a hard stop limit.
- Ask for a clear yes before commits, machine runs, and any safety-rule change. Log every machine reading.
- Real-world checks beat assumptions: verify with paper-feeler readings and side-view videos.

## Main workflow: Henna Studio (web UI v12)
Start in a Command Prompt (keep the window open; the Claude terminal tool tends to interrupt the slow start):
```
cd /d "%USERPROFILE%\Desktop\Ai+d405"
.venv\Scripts\python.exe webui_server_v12.py
```
Open http://127.0.0.1:8765 (older tabbed page: /classic). Close RealSense Viewer first (one camera user).
Disconnect UGS before pressing Connect in the page (one program per COM port).
Steps: 1 Scan (live view, record 5 s, file fixed + verified) - 2 Hand & region (MediaPipe, editable outline)
- 3 Design (gallery / SVG upload, size, rotate, drag; "Check on measured skin" = step 22 plan, all or
nothing) - 4 Surface check (3 paper-feeler readings -> offset) - 5 Program (step 22 + independent re-read)
- 6 Run in UGS + paper gap check. Jobs: `outputs/webui/<time>-job/job.json`.

## Key files
| File | Purpose |
|---|---|
| `current_calibration.json` | approved calibration paths + fixed path rules (single source for the web UI) |
| `webui_server_v12.py`, `webui/studio.html` | studio + machine link (v11 = same without link, v10 = tabbed) |
| `henna_preview/web_recorder.py` | live camera + recording; fixes late repeated option messages, verifies files |
| `henna_preview/grbl_link.py` | USB link: only `?`, `$HZ`, `$H`, feed hold + reset |
| `henna_preview/recording_multi.py` | 15-frame per-pixel median depth (unstable/noisy pixels rejected, never filled) |
| `henna_preview/svg_design.py` | line-art SVG reader and placement in machine X/Y |
| `henna_preview/surface_offset.py` | paper-feeler offset: smallest of >= 3, raise only, refused if spread > 0.5 mm |
| `step21_design_preview.py`, `step22_design_hover_test.py` | design preview / prosthetic hover program |
| `validation/S2026-*/` | settings and results of every validated session |

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
- "Move to set points" from the page (capture pose, paper-reading moves) was offered, not yet approved.
- Real hand: camera-only accuracy test (paper dots) and a 30 s hand-movement test were planned.
- Lower lift between strokes (Z -20 -> about -45) to shorten runs: user decision pending.
- Rigid textured hand cast for exact camera measurements.
