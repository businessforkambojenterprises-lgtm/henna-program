"""Henna Studio v28 = v27 + real-hand hover runs watched by the camera (user approval 2026-10-14: "go").

New job type "realhand" (Real hand - hover run). The camera-only "person" job stays as it was. A realhand job uses
the prosthetic pipeline (scan, region, design check, program, run - all the v10-v27 checks) plus:
  - standoff at least REAL_MIN_STANDOFF_MM (6 mm) and drawing speed REAL_FEED_MM_MIN (300 mm/min);
  - the v27 travel clearance over the hand, as for the prosthetic;
  - a camera check right before Start (every machine job): the hand must be where it was scanned
    (henna_preview/hand_watch.py), else Start is refused - this replaces the "hand has not moved" tick;
  - during a realhand run the camera keeps watching: hand moved more than 2 mm, skin within 1.5 mm of the tip, or the
    hand area out of sight for 1.5 s / no camera picture for 1.5 s -> feed hold (Pause) at once, with the reason.
    Resume first re-checks the hand. STOP is unchanged.
The job type replaces the "prosthetic hand only" tick; "nothing else in the travel area" and "at the emergency stop"
remain, as one tick in the run dialog (studio_v28.html). Writing a program moves nothing; its ticks are set from the
job type. v10-v27 files are unchanged.
"""
import collections
import datetime as dt
import json
import os
import threading
import time
from urllib.parse import urlparse

import numpy as np

import webui_server as base
import webui_server_v10 as v10
import webui_server_v11 as v11
import webui_server_v26 as v26
import webui_server_v27 as v27
from henna_preview import hand_watch as hw
from henna_preview import travel_clearance as tc
from henna_preview.depth_sample import WatchCameraService
from henna_preview.grbl_program_link import MachineError
from henna_preview.web_recorder import CameraService

STUDIO_HTML = os.path.join(base.WEB_DIR, "studio_v28.html")
REAL = "realhand"
MACHINE_JOBS = ("prosthetic", REAL)
REAL_MIN_STANDOFF_MM = 6.0
REAL_FEED_MM_MIN = 300
WATCH_PERIOD_S = 0.2
NO_PICTURE_S = 1.5
UNSEEN_S = 1.5
PRESTART_SAMPLES = 3
PRESTART_TIMEOUT_S = 8.0
REF_STRIDE = 2

machine = v26.machine
v11.SUBJECTS[REAL] = "real hand - hover run watched by the camera (no paste)"


def machine_job_only(job):
    if job.get("subject") not in MACHINE_JOBS:
        raise ValueError("programs and runs are for a prosthetic hand or a watched real-hand job - "
                         "this job is camera only")


v11.prosthetic_only = machine_job_only                      # every v11-v27 caller looks it up at call time

if type(v10.camera) is CameraService:                         # the laptop: the same camera service + depth samples
    v10.camera = WatchCameraService()


def _log(step, mpos, command, expected, measured, result, recording="", notes="Studio v28"):
    v10.log_row(v10.LOG_FILE, 0, step, mpos, command, expected, measured, result, recording, notes)


# ---------- calibration parts and the scanned skin ----------
def _parts():
    c = v10.cal()
    with open(os.path.join(base.ROOT, c["xy_calibration"]), encoding="utf-8") as f:
        xy = json.load(f)["result"]
    with open(os.path.join(base.ROOT, c["z_calibration"]), encoding="utf-8") as f:
        k = float(json.load(f)["result"]["k_mm"])
    return {"R": np.asarray(xy["R"], float), "t": np.asarray(xy["t_mm"], float), "k": k,
            "depth_fn": tc.depth_fn_from(c, base.ROOT)}


_rays = {}
_refs = {}


def rays_for(intr, shape, stride):
    key = (json.dumps(intr, sort_keys=True), tuple(shape), stride)
    if key not in _rays:
        _rays[key] = hw.ray_table(intr, shape, stride)
    return _rays[key]


def reference(job):
    """The design region's skin as scanned (machine cells), cached per job and region."""
    import cv2
    s = job["steps"]
    key = (job["id"], s["region"]["id"], s["scan"]["name"])
    if key not in _refs:
        fr = v26._median_frame(s["scan"]["recording"])
        raw = fr["depth_raw_aligned"]
        mask = np.zeros(raw.shape, np.uint8)
        cv2.fillPoly(mask, [np.round(np.asarray(s["region"]["polygon_px"], float)).astype(np.int32)], 1)
        sub = raw[::REF_STRIDE, ::REF_STRIDE]
        p = _parts()
        pts = hw.machine_points(sub, fr["depth_scale_m_per_unit"], rays_for(fr["colour_intrinsics"], sub.shape, REF_STRIDE),
                                s["scan"]["mpos"], p["R"], p["t"], p["k"], p["depth_fn"],
                                mask=mask[::REF_STRIDE, ::REF_STRIDE].astype(bool))
        _refs.clear()
        _refs[key] = hw.Reference(pts)
    return _refs[key]


def check_sample(ref, sample, head):
    p = _parts()
    pts = hw.machine_points(sample["depth"], sample["scale"], rays_for(sample["intr"], sample["depth"].shape, sample["stride"]),
                            head, p["R"], p["t"], p["k"], p["depth_fn"])
    return hw.evaluate(ref, pts, head)


def _fresh_sample(after_t, timeout):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        s = v10.camera.depth_sample()
        if s is not None and s["t"] > after_t:
            return s
        time.sleep(0.1)
    raise MachineError("no camera picture for the hand check - is the camera connected?")


def hand_check(job, samples=PRESTART_SAMPLES):
    """Right before Start / Resume: the hand must be where it was scanned. -> last result; raises when not."""
    ref = reference(job)
    st = machine.status()
    if not st.get("connected") or st.get("mpos") is None:
        raise MachineError("Connect the machine first")
    head = st["mpos"]
    t, res = 0.0, None
    for _ in range(samples):
        s = _fresh_sample(t, PRESTART_TIMEOUT_S)
        t = s["t"]
        res = check_sample(ref, s, head)
        if res["verdict"] != "ok":
            hint = {"unseen": " Move to scan position first, so the camera sees the hand.",
                    "moved": " Scan again.", "near": " Raise the nozzle."}[res["verdict"]]
            raise ValueError(f"Hand check: {hw.message(res)}.{hint}")
    return res


# ---------- the watch during a real-hand run ----------
class Watch:
    def __init__(self):
        self.lock = threading.Lock()
        self.state = {"active": False}
        self._stop = threading.Event()
        self._thread = None

    def view(self, _=None):
        with self.lock:
            return dict(self.state)

    def start(self, job, ref):
        self.stop()
        self._stop.clear()
        with self.lock:
            self.state = {"active": True, "job": job["id"], "started": dt.datetime.now().isoformat(timespec="seconds"),
                          "verdict": "starting", "paused_by_watch": None, "last": None, "checks": 0}
        self._thread = threading.Thread(target=self._loop, args=(job, ref), daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        t = self._thread
        if t is not None and t is not threading.current_thread():
            t.join(timeout=3)

    def _pause(self, job, reason, mpos):
        with self.lock:
            if self.state.get("paused_by_watch"):
                return
            self.state["paused_by_watch"] = reason
        try:
            machine.pause()
        except MachineError as e:
            with self.lock:
                self.state["pause_error"] = str(e)
        _log("Hand watch: PAUSED the run", mpos, "feed hold (Pause)", "hand still under the nozzle", reason, "paused",
             job["steps"]["scan"]["name"], f"Studio v28 real-hand watch; job {job['id']}")

    def _loop(self, job, ref):
        hist = collections.deque(maxlen=60)
        last_t, last_pic, unseen_since = 0.0, time.time(), None
        try:
            while not self._stop.is_set():
                st = machine.status()
                if st.get("busy") != "program":
                    break                                  # the run ended (finished, stopped or failed)
                if st.get("mpos") is not None and st.get("age_s") is not None:
                    hist.append((time.time() - float(st["age_s"]), list(st["mpos"])))
                s = v10.camera.depth_sample()
                now = time.time()
                if s is None or s["t"] <= last_t:
                    if now - last_pic > NO_PICTURE_S:
                        self._pause(job, f"no camera picture for {NO_PICTURE_S:g} s", st.get("mpos"))
                    time.sleep(0.05)
                    continue
                last_t, last_pic = s["t"], now
                head = _mpos_at(hist, s["t"])
                if head is None:
                    continue
                res = check_sample(ref, s, head)
                with self.lock:
                    self.state.update(verdict=res["verdict"], last=res, checks=self.state.get("checks", 0) + 1)
                if res["verdict"] in ("moved", "near"):
                    self._pause(job, hw.message(res), head)
                elif res["verdict"] == "unseen":
                    unseen_since = unseen_since or now
                    if now - unseen_since > UNSEEN_S:
                        self._pause(job, hw.message(res) + f" for {UNSEEN_S:g} s", head)
                else:
                    unseen_since = None
                time.sleep(max(0.0, WATCH_PERIOD_S - (time.time() - now)))
        except Exception as e:                             # the watch failing must hold the machine, not vanish
            self._pause(job, f"hand watch error: {e}", None)
        finally:
            with self.lock:
                self.state["active"] = False
                self.state["ended"] = dt.datetime.now().isoformat(timespec="seconds")


def _mpos_at(hist, t):
    """Machine position at camera time t, interpolated from the status history (None if no status yet)."""
    if not hist:
        return None
    if t <= hist[0][0]:
        return hist[0][1]
    for (t0, p0), (t1, p1) in zip(list(hist)[:-1], list(hist)[1:]):
        if t0 <= t <= t1 and t1 > t0:
            a = (t - t0) / (t1 - t0)
            return [p0[i] + a * (p1[i] - p0[i]) for i in range(3)]
    return hist[-1][1]


WATCH = Watch()


# ---------- routes ----------
_INNER = {p: v27.Handler.POST_API[p][0] for p in ("/api/studio/check_design", "/api/studio/program", "/api/machine/run",
                                                  "/api/machine/resume")}


def _real_rules(job, standoff):
    if float(standoff) < REAL_MIN_STANDOFF_MM - 1e-9:
        raise ValueError(f"A real hand needs the nozzle {REAL_MIN_STANDOFF_MM:g} mm or more above the skin - "
                         f"choose {REAL_MIN_STANDOFF_MM:g} mm and check the design again")
    v27.clearance(job, float(standoff))                    # the v27 rule, as for the prosthetic


def check_design(params):
    job = v11.job_load(params.get("job"))
    if job.get("subject") == REAL:
        _real_rules(job, params.get("standoff_mm", 0))
    return _INNER["/api/studio/check_design"](params)


def program(params):
    job = v11.job_load(params.get("job"))
    machine_job_only(job)
    p = dict(params, confirm_prosthetic=True, confirm_unmoved=True)       # from the job type; writing moves nothing
    if job["subject"] == REAL:
        _real_rules(job, (job["steps"].get("design") or {}).get("standoff_mm", 0))
        p["draw_feed_mm_min"] = REAL_FEED_MM_MIN
    return _INNER["/api/studio/program"](p)


def run(params):
    job = v11.job_load(params.get("job"))
    machine_job_only(job)
    if params.get("confirm_clear") is not True or params.get("confirm_estop") is not True:
        raise ValueError("Confirm that nothing else is in the travel area and that you are at the emergency stop")
    if job["subject"] == REAL:
        _real_rules(job, (job["steps"].get("design") or {}).get("standoff_mm", 0))
        if float((job["steps"].get("program") or {}).get("draw_feed_mm_min", REAL_FEED_MM_MIN)) > REAL_FEED_MM_MIN:
            raise ValueError(f"A real hand runs at {REAL_FEED_MM_MIN} mm/min - write the program again")
    res = hand_check(job)                                  # replaces the "hand has not moved" tick
    out = _INNER["/api/machine/run"](dict(params, confirm_prosthetic=True, confirm_unmoved=True))
    _log("Hand check before Start", (machine.status() or {}).get("mpos"), "camera, 3 pictures", "hand where it was scanned",
         f"{job['subject']}: median {res['median_dz_mm']} mm, {100 * res['share_off']:.0f} % off, "
         f"{100 * res['seen_share']:.0f} % seen", "ok", job["steps"]["scan"]["name"], f"Studio v28; job {job['id']}")
    if job["subject"] == REAL:
        WATCH.start(job, reference(job))
    return out


def resume(params):
    w = WATCH.view()
    if w.get("active") and w.get("paused_by_watch"):
        job = v11.job_load(w["job"])
        hand_check(job, samples=2)                        # raises with the reason when the hand is still off
        with WATCH.lock:
            WATCH.state["paused_by_watch"] = None
        _log("Hand watch: resume after a fresh hand check", (machine.status() or {}).get("mpos"), "camera, 2 pictures",
             "hand where it was scanned", "ok", "resumed", job["steps"]["scan"]["name"], f"Studio v28; job {job['id']}")
    return _INNER["/api/machine/resume"](params)


class Handler(v27.Handler):
    server_version = "HennaStudio/0.28"
    GET_API = dict(v27.Handler.GET_API, **{"/api/studio/watch": WATCH.view})
    POST_API = dict(v27.Handler.POST_API, **{
        "/api/studio/check_design": (check_design, v27.Handler.POST_API["/api/studio/check_design"][1]),
        "/api/studio/program": (program, v27.Handler.POST_API["/api/studio/program"][1]),
        "/api/machine/run": (run, v27.Handler.POST_API["/api/machine/run"][1]),
        "/api/machine/resume": (resume, v27.Handler.POST_API["/api/machine/resume"][1]),
    })

    def do_GET(self):
        if urlparse(self.path).path in ("/", "/index.html", "/studio"):
            if self._host_ok():
                return self._file(STUDIO_HTML, "text/html; charset=utf-8")
            return
        return super().do_GET()


def main():
    v27.Handler = Handler                                   # v27.main serves v27.Handler (-> v26.main)
    print("Henna Studio v28 - real-hand hover runs watched by the camera", flush=True)
    try:
        v27.main()
    finally:
        WATCH.stop()


if __name__ == "__main__":
    main()
