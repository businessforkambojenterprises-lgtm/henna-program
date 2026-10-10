# Studio v14: Studio runs the hover program (no UGS)

User decisions on 10 October 2026: **Studio runs the program** and **the paper-feeler steps are removed;
prosthetic programs always use 4 mm or more above measured skin, never an offset.** This is a new revision;
v13, the calibration and all validated processing scripts are unchanged.

Start from source with `.venv\Scripts\python.exe webui_server_v14.py` and open http://127.0.0.1:8765.

## Workflow
1. **Scan** - Connect, Home (`$HZ`, then `$H`), Move to scan position, place the hand, Record (as v13).
2. **Hand & region** - unchanged.
3. **Design** - nozzle height 4.0 to 6.0 mm. Designs checked at 3 mm in older versions must be checked again.
4. **Program** - step 22 writes it, the independent checker re-reads it, Studio stores its SHA-256.
5. **Run** - Start (four confirmations), Pause / Resume, STOP. Progress, position and a run history per job.

**Jog** (top bar, approved 10 October 2026): X / Y / Z in 0.1, 1 or 10 mm steps after Home, with a "no person's
hand under the machine" tick. Every jog stays at travel height (Z -20) or higher, so the nozzle cannot reach a
hand: below Z -20 only Z up works, X/Y unlock at Z -20 or higher, Z down stops at -20, Z up stops at 0.
Sends only `$J=G21 G90 G53 <axis><target> F300|F1000` and waits for Idle at the target. Only a checked
program lowers the nozzle below Z -20.

## What the run does and refuses
- Sends only the job's own program file, unchanged since it was written (SHA-256), re-read again at Start,
  with the prosthetic header, standoff >= 4 mm and no offset. Older programs written for UGS must be rewritten.
- Sends only `G21`, `G90`, `G53 G0 Z-20`, `G53 G0 X Y` (at travel Z) and `G53 G1 X Y Z F<=300`, one line at a
  time (waits for each `ok`). Comments are not sent. No `$`, M, S or T. Requires Home on this connection and Idle.
- Pause = feed hold `!` and nothing new is sent until Resume (`~`). STOP = feed hold + soft reset; home again.
- Any `error:`, ALARM, controller restart, lost connection, missing `ok` (60 s, pauses excluded) or an end
  position other than the program's own stops the run (feed hold + reset) and requires homing.
- While a program runs, job changes, homing, scan-position moves, recording and Disconnect are refused.
- Real-hand jobs never get a program or a run. `motion_ready` stays false. No dispensing.
- Start and result of each run are logged in `validation_log_S2026-10-05-A.csv` and in the job (`runs`).

## Validation so far
- 17 fake-controller tests (planner queue, `ok` per line, hold/resume, reset, error, alarm, restart, missing
  `ok`, wrong end position, busy refusals, jog limits) and 20 server tests (4 mm rule, file fingerprint,
  re-read, confirmations, run records, jog inputs, HTTP guard).
- Page checked in the browser against a simulated controller (`.claude/studio_v14_sim.py`, port 8767):
  Connect, Home, Start dialog, progress, Pause (nothing sent while held), Resume, STOP, history, dark mode,
  and the jog pad from the scan position (only Z+ until Z -20, then X/Y unlock, Z- stops at -20).
- **Not yet run on the real machine.** First real run: an air run with NO hand under the machine (watch the
  path, try Pause / Resume / STOP), then the textured prosthetic, filmed from the side.

Desktop app: rebuilt as 0.14.0.1 from commit a0d7515 and installed in `Desktop\HennaStudio-Windows-x64` (offline smoke
test 8/8, self-test ok, jobs and app log kept; replaced v13 files in `backup-before-v14`).

## v15 (same day): two fixes from the first real run
The first real session (scan, 4 mm program over the prosthetic, STOP, full 345-line run with 4 Pause/Resume)
worked. Its log showed that positions reported during a run could be about 1 s old: v14 asked for the status
only while one acknowledgment was slow. `webui_server_v15.py` / `grbl_program_link_v15.py` (v14 unchanged):
- the status is polled on a fixed schedule for the whole run (about every 0.25 s);
- Pause is verified: if a fresh report still says Run/Jog, the hold is sent again; if the machine is still
  moving 1.5 s after Pause, the run is stopped (hold + reset).
Tests: 6 more fake-controller tests (252 in total); simulator with a large planner: position age <= 0.3 s.
Desktop app: v15 (0.15.0.1, commit d9b0328) installed the same way; smoke test 8/8, self-test ok; v14 files in
`backup-before-v15`.

## v16: drawing speed per program
User decision on 10 October 2026: choose the drawing speed in step 4 - 100, 150, 200, 250, **300 (default,
approved)**, 400, 500 or 600 mm/min. `webui_server_v16.py` + `webui/studio_v16.html` (earlier files unchanged):
- step 22 writes the program with the chosen speed; the drop from Z -20 to the first point of each stroke is
  set to 300 mm/min or slower (the nozzle approaches the hand there); a comment line records the speeds;
- the independent re-read uses the chosen speed as its limit, and a second check refuses any drop above 300
  or any drawing line that is not at the program speed - when written and again at every Start;
- v14 programs (300) still run; changing the speed after writing needs Write program again.
Tests: 12 server tests (264 in total); page checked in the simulator. First run at a new speed: air run.
Desktop app: v16 (0.16.0.1, commit 750d3a2) installed the same way; smoke test 8/8, self-test ok.

### v16.1: speed range changed by the user
"minimum 300, default 600, maximum 1200": 300, 400 ... 1200 mm/min, default 600. Drops to the hand stay at 300.
1200 mm/min = 20 mm/s: first run at each new speed as an air run. Desktop package 0.16.1.1.

## Own app window (package 0.16.2)
User request: open like any other program, not in a browser. The desktop launcher now opens a native
"Henna Studio" window (pywebview 5.3.2 + pythonnet 3.0.5, drawn by Windows WebView2) with the Henna icon,
hides its own console (messages in app/outputs/studio_console.log) and shuts Studio down safely when the
window is closed (asks first while the machine is busy; a running program is stopped). Started from a Command
Prompt the console stays visible. If no window can be made it falls back to the browser. Desktop shortcut added.

## v17: low hops between strokes ("Z like a laser")
User decision on 10 October 2026: between strokes the nozzle hops only **5 mm above the highest measured skin
under the crossing** instead of rising to Z -20. `step23_design_hop_program.py` (step 22 + hops; step 22 unchanged):
- each crossing is sampled every 0.5 mm on the straight line and on parallel lines up to 1.5 mm to each side and in
  a square around both ends; hop = highest skin + 5 mm, never below either end's nozzle + 0.5 mm, rounded up;
- any unmeasured or out-of-region sample, or a hop within 1 mm of travel Z, keeps travel Z -20 for that crossing;
- the hop moves are read back from the program text and the skin under them is sampled again (hop_path.verify_hops);
- program_reread_v2 allows only: rise (G0 Z up to a hop or travel Z), straight crossing right after it, drop/draw;
  the first crossing and the end are at travel Z. Used when writing and at every Start.
Real data (lotus 33 mm, scan 20261010_140248, offline): 11 of 11 hops low at Z -34.6..-28.0 (5.00 mm above the skin,
independent check agrees); estimated run 69.5 s instead of 96 s at 300 mm/min. First real run: air run.
Tests: 13 more (278 in total). Desktop package 0.17.0.1 (step 23 added to the allowed worker scripts).

### v17.1: clearer end of a run
After every line is sent, the Run step shows "Finishing last moves" (pulsing bar, top bar "Finishing...") until the
machine stops at the program end; only then "Finished". Found in the first real hop air run. Package 0.17.1.1.

## v18: write a name on the hand
User request: type a name (e.g. HARSH, SANDEEP) instead of choosing a picture. `henna_preview/stroke_font.py` draws
capital A-Z, spaces and hyphens as single pen lines (normal fonts are filled outlines, not usable for henna); the SVG
goes through the normal upload checks into designs/uploads (`Name_<TEXT>.svg`, reused when typed again) and is then
placed, checked on skin, programmed (speed, low hops) and run like any design. HARSH = 11 strokes, SANDEEP = 10.
Tests: 7 (in total 285). Simulator with a copy of the real jobs: HARSH placed at 30 mm inside the outline.
