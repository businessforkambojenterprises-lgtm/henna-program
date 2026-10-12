"""Studio v20 - "Review fixes" (outside review by Codex, 2026-10-12).

Same as v19 with:
1. STOP always tries the controller reset even if the feed hold could not be sent; a failed write shows
   "STOP NOT CONFIRMED - use the emergency stop" until the next Connect / Home.
2. Pause must be confirmed by a fresh Hold/Idle report within 1.5 s; silence or motion -> the run is stopped.
3. Each finished run is recorded from a snapshot taken while it still owned the machine.
4. The Run panel shows current readiness first and the last result as context (no stale "Home again").
(henna_preview/grbl_program_link_v20.py; v13-v19 files unchanged.)
"""
import datetime as dt
import os
from urllib.parse import urlparse

import webui_server as base
import webui_server_v10 as v10
import webui_server_v11 as v11
import webui_server_v14 as v14
import webui_server_v16 as v16
import webui_server_v17 as v17
import webui_server_v19 as v19
from henna_preview.grbl_program_link import MachineError
from henna_preview.grbl_program_link_v20 import GrblProgramLinkV20

STUDIO_HTML = os.path.join(base.WEB_DIR, "studio_v20.html")

machine = GrblProgramLinkV20(calibration_provider=v10.cal, on_event=v14.v13.log_event)
# One serial link per process: every v13-v19 function and route looks the link up at call time.
v16.v15.machine = machine
v14.machine = machine
v14.v13.machine = machine


def run_start(params):
    """v17's run start; the result is recorded from the link's completion snapshot (review finding 3)."""
    job = v11.job_load(params.get("job"))
    v11.prosthetic_only(job)
    v11.need(job, "program")
    v14.confirmed_run(params)
    if v10.camera.status()["state"] == "recording":
        raise MachineError("Finish recording before running a program")
    name, lines, end, rr, feed = v17.checked_program(job)
    run_id = dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    with v14._runs_lock:
        status = machine.start_program(
            name, lines, end, on_done=lambda error, snap: v14._record_run(job["id"], run_id, error, snap))
        job.setdefault("runs", []).append({
            "id": run_id, "program": job["steps"]["program"]["program"], "file": name,
            "sha256": job["steps"]["program"]["sha256"], "lines": len(lines), "end_mpos": list(end),
            "draw_feed_mm_min": feed, "low_hops": rr.get("low_hops", 0), "recording": job["steps"]["scan"]["name"],
            "started": dt.datetime.now().isoformat(timespec="seconds"), "result": "running"})
        v11.job_save(job)
    v10.log_row(v10.LOG_FILE, 12, "Studio run start", status.get("mpos"), name,
                f"{len(lines)} lines; ends at X{end[0]:.3f} Y{end[1]:.3f} Z{end[2]:.3f}",
                f"re-read {rr['problems']} problems; {rr['strokes']} strokes; Z {rr['z_min']} to {rr['z_max']}; "
                f"drawing F{feed}; low hops {rr.get('low_hops', 0)}",
                "started", job["steps"]["scan"]["name"], "prosthetic only; 4 mm; no dispensing; four confirmations ticked")
    return dict(v14.machine_status(), job=v14.job_view(job))


class Handler(v19.Handler):
    server_version = "HennaStudio/0.20"
    POST_API = dict(v19.Handler.POST_API, **{"/api/machine/run": (run_start, True)})

    def do_GET(self):
        if urlparse(self.path).path in ("/", "/index.html", "/studio"):
            if self._host_ok():
                return self._file(STUDIO_HTML, "text/html; charset=utf-8")
            return
        return super().do_GET()


def main():
    srv = base.http.server.ThreadingHTTPServer((base.HOST, base.PORT), Handler)
    srv.daemon_threads = False          # let a scan that is being verified finish before shutdown
    print("Henna Studio v20 - review fixes. motion_ready=false", flush=True)
    print(f"Open http://{base.HOST}:{base.PORT}; press Ctrl+C to stop (a running program is stopped)", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        try:
            if machine.status().get("busy") in v14.MOVING:
                machine.stop()
        except MachineError:
            pass
        finally:
            try:
                srv.server_close()
            finally:
                try:
                    if machine.status().get("busy") in v14.MOVING:
                        machine.stop()
                except MachineError:
                    pass
                finally:
                    try:
                        machine.disconnect()
                    finally:
                        v10.camera.stop()


if __name__ == "__main__":
    main()
