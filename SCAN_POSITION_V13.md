# Studio v13: fixed scan positioning

Requested by the operator on 10 October 2026: replace the Capture panel's UGS
commands and pasted status with a scan-position button and automatic progression.
This additive revision leaves v12 and calibrated processing code unchanged.

Start the new source version with `.venv\Scripts\python.exe webui_server_v13.py`.
The v13 Windows package starts it automatically.

1. Disconnect UGS from the controller, then Connect in Studio.
2. Remove all hands and objects from the travel area. Use Home and confirm.
   Studio executes `$HZ`, waits for completion, then `$H` and waits again.
3. Use **Move to scan position** and confirm the area is clear and e-stop is near.
   The fixed machine-coordinate route is Z -20 (F300), X145.8 Y42.6 (F1000),
   then Z -40 (F300). Each stage requires ACK plus fresh Idle at its expected XYZ.
4. Wait for **Position ready**, then place the hand. Start the camera and Record.
5. A verified scan proceeds automatically to Hand & region and hand detection.

The status line is read from the controller. Capture excludes other app movements
and monitors status until camera verification ends. A position change, stale status,
reset, Stop or connection loss invalidates capture. Rejected recordings are retained
with `.rejected` suffix and are not published as an accepted scan or job step.

This does not enable general G-code streaming. Programs, paper-feeler moves and other
manual moves still use UGS. Real-hand jobs remain camera preview only; generated hover
programs remain prosthetic-only and `motion_ready` remains false. No dispensing.
The new move must occur with the travel area empty, before placing any subject.

Verification: fake serial and API tests exercise route, homing, completion reports,
wrong/stale positions, Stop, faults, reconnects, recording exclusion and rejection.
No live CNC movement or live D405 capture was used to validate this new revision.
Its first physical scan-position run remains an operator validation step.
