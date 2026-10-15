"""Henna Studio v29 = v28 + PASTE DISPENSING (user approval 2026-10-15: "go A").

Two dispensers, both on the controller's spindle/laser output through a relay: Pneumatic (a solenoid air valve) and
Servo dispenser (its driver started by an SSR). For the controller both are M3 S1000 = paste ON, M5 = paste OFF.

- Program step: Paste = None (hover only, as v28) / Pneumatic / Servo dispenser, with a start and a stop delay.
  The hover program is written and checked exactly as in v28; the paste lines are then added (dispense.add: ON after
  every drop to the skin, OFF before every lift) and the result is re-read by dispense_check, which shares no code
  with it: taking the paste lines out must give back the checked hover program, paste ON only after a drop below
  travel Z, never a G0 with paste ON, ends OFF, one ON and one OFF per stroke. Saved as its own file with its own
  SHA-256; the checked hover file stays beside it.
- Run: the checks of every run, then - before anything is sent - the same paste re-read again, and the controller's
  settings are read ($$): laser mode must be off ($32=0) and the full output $30 = 1000, else refused.
  Pause (screen, push button, hand watch) also stops the paste; Resume turns it on, waits the start delay, then
  moves on (grbl_program_link_v29). STOP, errors and closing Studio reset the controller, which turns it off.
- Dispenser test: 0.2 to 3 s of paste with the machine still (same settings check).
- On the Pi the SSR can be on a GPIO pin instead (user 2026-10-15: "B"): webui_server_pi sets machine.paste_output;
  the same program file, the link switches the pin after "G4 P0" (moves finished) instead of sending M3/M5, and the
  laser-mode check is not needed.
v1-v28 files are unchanged; the laptop runs this file, the Pi's webui_server_pi.py switches to it at the next merge.
"""
import datetime as dt
import hashlib
import os
import re
from urllib.parse import unquote, urlparse

import webui_server as base
import webui_server_v10 as v10
import webui_server_v11 as v11
import webui_server_v14 as v14
import webui_server_v17 as v17
import webui_server_v27 as v27
import webui_server_v28 as v28
from henna_preview import dispense, dispense_check
from henna_preview.grbl_program_link_v29 import GrblProgramLinkV29

STUDIO_HTML = os.path.join(base.WEB_DIR, "studio_v29.html")
TEST_S = 1.0
PASTE_NAME = re.compile(r"^DESIGN_PASTE_[0-9-]+\.gcode$")

machine = v28.machine
machine.__class__ = GrblProgramLinkV29          # adds $$ read, paste stop in pauses, the dispenser test
HOVER_CHECKED = v17.checked_program             # v17's checks of the hover file, unchanged


def _log(step, mpos, command, expected, measured, result, recording="", notes="Studio v29"):
    v10.log_row(v10.LOG_FILE, 0, step, mpos, command, expected, measured, result, recording, notes)


def _same(a, b):
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def _command_lines(text):
    return [ln for ln in text.splitlines() if ln and not ln.startswith("(")]


# ---------- the paste program ----------
def checked_program(job):
    """v17's checks of the hover file; for a paste program also its own file, fingerprint and the paste re-read.
    Installed as v17.checked_program, which every run start calls (v20.run_start)."""
    name, lines, end, rr, feed = HOVER_CHECKED(job)
    pr = job["steps"]["program"]
    d = pr.get("dispense")
    if not d:
        return name, lines, end, rr, feed
    pd = v10.out_dir("program", pr["program"])
    path = d["file"]
    if not _same(os.path.dirname(path), pd):
        raise ValueError("the paste program is not the one written for this job")
    with open(path, "rb") as f:
        raw = f.read()
    if hashlib.sha256(raw).hexdigest() != d["sha256"]:
        raise ValueError("the paste program changed after it was written - write it again in step 4")
    cfg = dispense.settings(d["dispenser"], d["start_s"], d["stop_s"])
    paste = _command_lines(raw.decode("ascii"))
    if paste != dispense.add(lines, cfg):
        raise ValueError("the paste program does not match its checked hover program - write it again in step 4")
    chk = dispense_check.check(paste, lines, cfg, v10.cal()["travel_z"], dispense.S_FULL)
    return os.path.basename(path), paste, end, dict(rr, paste=chk), feed


v17.checked_program = checked_program


def program(params):
    cfg = dispense.settings(params.get("dispenser"), params.get("start_delay_s"), params.get("stop_delay_s"))
    out = v28.program(params)                                # the hover program, written and checked as in v28
    if cfg is None:
        return out
    job = v11.job_load(params.get("job"))
    try:
        name, lines, _, _, _ = HOVER_CHECKED(job)
        c = v10.cal()
        paste = dispense.add(lines, cfg)
        chk = dispense_check.check(paste, lines, cfg, c["travel_z"], dispense.S_FULL)
        pr = job["steps"]["program"]
        pd = v10.out_dir("program", pr["program"])
        gname = "DESIGN_PASTE_" + name.split("DESIGN_HOVER_TEST_PROSTHETIC_", 1)[-1]
        subject = {"prosthetic": "PROSTHETIC HAND", v28.REAL: "REAL HAND - WATCHED RUN"}.get(job["subject"], job["subject"])
        header = [f"(DESIGN WITH PASTE - {cfg['label'].upper()} - {subject})",
                  f"(made {dt.datetime.now():%Y%m%d-%H%M%S} by Studio v29 from {name}, sha256 {pr['sha256'][:16]})",
                  f"(paste ON M3 S{dispense.S_FULL} after each drop, start delay {cfg['start_s']:g} s; OFF M5 before "
                  f"each lift, stop delay {cfg['stop_s']:g} s - {chk['strokes']} strokes)",
                  "(paste output checked by Studio before the run: a Pi pin, or the controller with $32=0, $30=1000)",
                  "(Run from Studio only - home first - stand at the emergency stop)"]
        gfile = os.path.join(pd, gname)
        with open(gfile, "w", encoding="ascii", newline="\n") as f:
            f.write("\n".join(header + paste) + "\n")
        with open(gfile, "rb") as f:
            sha = hashlib.sha256(f.read()).hexdigest()
        added = dispense.added_time_s(cfg, chk["strokes"])
        pr["dispense"] = dict(cfg, file=gfile, sha256=sha, strokes=chk["strokes"], added_s=added, check=chk,
                              download=f"/download_program/{os.path.basename(pd)}/{gname}")
        pr["estimated_run_time_s"] = round(float(pr["estimated_run_time_s"]) + added, 1)
        v11.job_save(job)
        checked_program(job)                                 # exactly what a run will check, once now
    except Exception:
        job = v11.job_load(params.get("job"))
        job["steps"].pop("program", None)                    # never leave a hover program where paste was chosen
        v11.job_save(job)
        raise
    _log("Studio v29 paste program", None, "Studio Write program", "paste lines added and re-read",
         f"{gname}: {cfg['label']}, {chk['strokes']} strokes, start {cfg['start_s']:g} s, stop {cfg['stop_s']:g} s, "
         f"+{added:g} s; re-read {chk['problems']} problems", "generated - not yet run", job["steps"]["scan"]["name"],
         f"Studio v29; job {job['id']}; {job['subject']}")
    return {"job": v14.job_view(job)}


# ---------- the controller ----------
def controller_ready():
    """The paste output is ready: a Pi pin (webui_server_pi sets machine.paste_output), or the controller's output
    with laser mode off and full output 1000 (read with $$ right now)."""
    pin = machine.paste_output
    if pin is not None:
        if not getattr(pin, "ready", True):
            raise ValueError(f"Paste refused: the Pi paste pin is not working ({getattr(pin, 'error', '')})")
        return {"output": pin.name}
    found = machine.read_settings()
    bad = dispense.controller_ok(found)
    if bad:
        raise ValueError("Paste refused: " + bad)
    return found


def _where(found):
    return f"Pi pin {found['output']}" if "output" in found else f"controller $32={found.get(32)} $30={found.get(30)}"


def run(params):
    job = v11.job_load(params.get("job"))
    d = (job["steps"].get("program") or {}).get("dispense")
    if d:
        found = controller_ready()
        machine.arm_dispense(dispense.settings(d["dispenser"], d["start_s"], d["stop_s"]))
    try:
        out = v28.run(params)
    finally:
        machine.arm_dispense(None)                           # start_program took it, or the run was refused
    if d:
        _log("Paste run start", (machine.status() or {}).get("mpos"), "$$ then the paste program",
             "paste output ready", f"{_where(found)}; {d['label']}, "
             f"{d['strokes']} strokes", "started", job["steps"]["scan"]["name"], f"Studio v29; job {job['id']}")
    return out


def dispense_test(params):
    seconds = float((params or {}).get("seconds", TEST_S))
    found = controller_ready()
    st = machine.status()

    def done(error):
        _log("Dispenser test", st.get("mpos"), f"M3 S{dispense.S_FULL}; G4 P{seconds:.3f}; M5",
             f"paste {seconds:g} s, machine still", "ok" if error is None else error,
             "done" if error is None else "stopped", "", f"Studio v29; {_where(found)}")
    machine.start_dispense_test(seconds, on_done=done)
    return v14.machine_status()


def dispensers(_=None):
    return {"dispensers": dispense.DISPENSERS, "delay_range_s": [dispense.DELAY_MIN_S, dispense.DELAY_MAX_S],
            "test_s": TEST_S, "s_full": dispense.S_FULL}


class Handler(v28.Handler):
    server_version = "HennaStudio/0.29"
    GET_API = dict(v28.Handler.GET_API, **{"/api/studio/dispensers": dispensers})
    POST_API = dict(v28.Handler.POST_API, **{
        "/api/studio/program": (program, v28.Handler.POST_API["/api/studio/program"][1]),
        "/api/machine/run": (run, v28.Handler.POST_API["/api/machine/run"][1]),
        "/api/machine/dispense_test": (dispense_test, False),
    })

    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/", "/index.html", "/studio"):
            if self._host_ok():
                return self._file(STUDIO_HTML, "text/html; charset=utf-8")
            return
        if path.startswith("/download_program/") and self._paste_download(unquote(path[len("/download_program/"):])):
            return
        return super().do_GET()

    def _paste_download(self, rest):
        """A copy of a paste program (the v10 route only knows hover program names)."""
        parts = rest.split("/")
        if not (len(parts) == 2 and v10.NAME.match(parts[0]) and PASTE_NAME.match(parts[1])):
            return False
        if not self._host_ok():
            return True                                      # _host_ok answered already
        p = os.path.join(base.OUT_ROOT, *parts)
        if not (base.is_inside(p, base.OUT_ROOT) and os.path.isfile(p)):
            return False
        with open(p, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=ascii")
        self.send_header("Content-Disposition", f'attachment; filename="{parts[1]}"')
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
        return True


def main():
    v27.Handler = Handler                                   # v27.main serves v27.Handler (-> v26.main)
    print("Henna Studio v29 - paste dispensing (pneumatic / servo SSR on the controller output)", flush=True)
    try:
        v27.main()
    finally:
        v28.WATCH.stop()


if __name__ == "__main__":
    main()
