"""Studio v26 - calibrating a machine in Studio (user approval 2026-10-13). v13-v25 files unchanged.

Only on a machine in CALIBRATION MODE (the ball screw now), in Calibration -> "Calibrate this machine":
- Calibration jog: Z only, 1 / 0.1 mm, F100, down to the machine's Z floor (ball screw -76 = 4 mm above the bed);
  X/Y stay refused below travel Z. Needs the tick "only paper or a block on the bed - no hand, no prosthetic,
  no person" (henna_preview/grbl_program_link_v26.py).
- A. X/Y: record the dot-sheet scan at the scan position (dots found automatically), save the nozzle position over
  each dot, fit (henna_preview.calibration.solve - the belt-drive machine's maths).
- B. Z: record a scan of a flat block, save paper-feeler touches on it, fit k (henna_preview.zcal.solve_k).
Every saved position, touch and fit is logged. The fits are DRAFTS: they become the machine's calibration only
after the user approves them (then its registry entry is set). Programs and runs stay refused until then.
"""
import json
import os
from urllib.parse import urlparse

import webui_server as base
import webui_server_v10 as v10
import webui_server_v14 as v14
import webui_server_v25 as v25
from henna_preview import calib_session
from henna_preview.calibration import CalibrationError
from henna_preview.grbl_program_link import MachineError
from henna_preview.grbl_program_link_v26 import GrblProgramLinkV26
from henna_preview.machines import MachineProfileError

v24 = v25.v24
STUDIO_HTML = os.path.join(base.WEB_DIR, "studio_v26.html")
profiles = v25.profiles
machine = v25.machine
machine.__class__ = GrblProgramLinkV26          # adds start_cal_jog; everything else is the v22 link unchanged


def apply_active():
    """The link's motion values from the active machine. A machine with only a work area yet (being set up) keeps
    the link's fixed route values but gets its own Jog box; scan routes stay refused for it (no scan position)."""
    from henna_preview import machine_motion
    mid = profiles.active_id()
    m = profiles.get(mid) if mid else None
    if m is not None:
        profiles.check_consistent(m)
    if m is not None and m.get("motion") and m.get("work_area_mm"):
        vals = machine_motion.values_for(m["motion"], m["work_area_mm"])
    else:
        vals = dict(machine_motion.BELT_DRIVE)
        if m is not None and m.get("work_area_mm"):
            vals["X_MAX"], vals["Y_MAX"] = (float(v) for v in m["work_area_mm"])
    machine_motion.apply(vals)
    machine.cal_floor = (float(m["motion"]["firmware_z_floor"])
                         if m is not None and profiles.calibration_mode(m) else None)
    return vals if m is not None and m.get("motion") else None


def link_cfg():
    """What the link checks before each move: the machine's motion values; while it is being set up (no scan
    position yet) the link's own fixed values, so only Home and Jog can run - scan routes stay refused."""
    from henna_preview import grbl_scan_link as gsl
    m = profiles.active()
    if m.get("motion"):
        return profiles.motion_cfg(m)
    return {"capture_pose_mpos": list(gsl.POSE), "travel_z": gsl.TRAVEL_Z, "draw_feed_mm_min": 300.0,
            "travel_feed_mm_min": 1000.0, "note": "jog-only values while the machine is being set up"}


machine._calibration = link_cfg

RULE_KEYS = ("frames", "min_valid_frac", "max_mad_mm", "floor_above_skin_mm", "draw_feed_mm_min", "travel_feed_mm_min",
             "standoff_mm_allowed", "fit_mm_allowed", "paper_mm_allowed")


def cal():
    """Studio's calibration (v10.cal). Calibrated machine: its approved file. Calibration mode: its motion values plus
    the Studio-wide rules (the same for every machine) - never the camera-to-nozzle parts (X/Y, Z, depth scale), so
    everything that needs those stays refused. Otherwise: refused."""
    m = profiles.active()
    if profiles.calibrated(m):
        return profiles.cal_of(m)
    if profiles.calibration_mode(m):
        with open(os.path.join(base.ROOT, "current_calibration.json"), encoding="utf-8") as f:
            rules = json.load(f)
        return dict({k: rules[k] for k in RULE_KEYS}, **profiles.motion_cfg(m))
    return profiles.cal_of(m)                                    # raises: not calibrated


v10.cal = cal


def _rules():
    with open(os.path.join(base.ROOT, "current_calibration.json"), encoding="utf-8") as f:
        c = json.load(f)
    return {k: c[k] for k in ("frames", "min_valid_frac", "max_mad_mm")}       # processing rules, not machine values


def _cal_mode():
    m = profiles.active()
    if not profiles.calibration_mode(m):
        raise MachineProfileError("The calibration tools are only for a machine in calibration mode")
    return m


def _targets_only(params):
    if params.get("confirm_targets_only") is not True:
        raise ValueError("Confirm that only paper or a block is on the bed - no hand, no prosthetic, no person")


def _session(m):
    return calib_session.Session(os.path.join(base.ROOT, "outputs", "calibration", m["id"]))


def _fresh_idle_status():
    st = machine.status()
    if not st.get("connected") or not st.get("homed"):
        raise MachineError("Connect and Home first")
    if st.get("busy") or st.get("state") != "Idle" or st.get("age_s") is None or st["age_s"] > 1.5:
        raise MachineError("Wait for a fresh Idle machine position")
    return st


def _median_frame(path):
    from henna_preview.recording import count_frames_db3
    from henna_preview.recording_multi import read_frames_median
    r = _rules()
    pairs = min(count_frames_db3(path).values())
    n = int(r["frames"])
    return read_frames_median(path, max(0, pairs // 2 - n // 2), n, r["min_valid_frac"], r["max_mad_mm"])


def _record(m, kind):
    s = _session(m)
    os.makedirs(s.dir, exist_ok=True)
    stamp = v10.dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    path = os.path.join(s.dir, f"{kind}_{stamp}.db3")
    if v10.shutil.disk_usage(base.ROOT).free < v10.MIN_FREE_BYTES:
        raise ValueError("less than 2 GB free")
    with machine.capture_guard() as line:              # fresh, homed, idle at the verified scan pose - whole recording
        st = v10.parse_status(line)
        v10.check_pose(st["mpos"], tuple(v10.cal()["capture_pose_mpos"]))
        v10.camera.record(path, 3.0)
    return path, st, stamp


def _out(m):
    s = _session(m).load()
    mo = profiles.motion(m)
    return dict(s, machine={"id": m["id"], "name": m["name"]}, floor=mo["firmware_z_floor"], travel_z=mo["travel_z"],
                scan_pose=mo["scan_pose_mpos"])


def _log(step, mpos, command, expected, measured, result="recorded", recording=""):
    v10.log_row(v10.LOG_FILE, 0, step, mpos, command, expected, measured, result, recording, "Studio v26 calibration")


# ---------- routes ----------
def session_get(_=None):
    return _out(_cal_mode())


def jog_z(params):
    m = _cal_mode()
    _targets_only(params)
    if v10.camera.status()["state"] == "recording":
        raise MachineError("Finish recording before jogging")
    d, step = params.get("direction"), params.get("step_mm")
    if d not in (1, -1) or type(d) is not int or not isinstance(step, (int, float)) or isinstance(step, bool) \
            or float(step) not in (0.1, 1.0, 10.0):
        raise ValueError("calibration jog: direction 1 or -1, step 10, 1 or 0.1 mm")
    machine.cal_floor = float(profiles.motion(m)["firmware_z_floor"])
    return machine.start_cal_jog(d, float(step))


def jog_xy(params):
    """Calibration X/Y nudge: 1 / 0.1 mm at any height down to the floor (calibration mode, targets-only tick)."""
    m = _cal_mode()
    _targets_only(params)
    if v10.camera.status()["state"] == "recording":
        raise MachineError("Finish recording before jogging")
    axis, d, step = params.get("axis"), params.get("direction"), params.get("step_mm")
    if axis not in ("X", "Y") or d not in (1, -1) or type(d) is not int or not isinstance(step, (int, float))             or isinstance(step, bool) or float(step) not in (0.1, 1.0):
        raise ValueError("X/Y nudge: axis X or Y, direction 1 or -1, step 1 or 0.1 mm")
    machine.cal_floor = float(profiles.motion(m)["firmware_z_floor"])
    return machine.start_cal_nudge(axis, d, float(step))


def xy_scan(params):
    m = _cal_mode()
    _targets_only(params)
    path, st, stamp = _record(m, "xy")
    frame = _median_frame(path)
    dots = calib_session.find_dots(frame)
    rel = f"{stamp}-calxy"
    calib_session.overlay(frame, dots, os.path.join(base.OUT_ROOT, rel, "overlay.png"),
                          f"dot sheet - {len(dots)} dots found - {m['name']}")
    sess = _session(m)
    s = sess.load()
    s.update(xy_scan={"recording": path, "time": calib_session.now(), "overlay": f"/outputs/{rel}/overlay.png",
                      "head": dict(zip("xyz", st["mpos"])), "dots": dots}, xy_points={}, xy_fit=None,
             z_scan=None, z_touches=[], z_fit=None)
    sess.save(s)
    _log("Calibration X/Y: dot-sheet scan", st["mpos"], "Studio Record 3 s", "7 dots with measured depth",
         f"{len(dots)} dots found: " + "; ".join(f"{d['label']} px {d['pixel_uv']} cam {d['camera_mm']}" for d in dots),
         recording=os.path.basename(path))
    return _out(m)


def xy_point(params):
    m = _cal_mode()
    sess = _session(m)
    s = sess.load()
    if not s.get("xy_scan"):
        raise ValueError("Record the dot-sheet scan first")
    label = params.get("label")
    if label not in [d["label"] for d in s["xy_scan"]["dots"]]:
        raise ValueError("choose one of the dots found in the scan")
    st = _fresh_idle_status()
    x, y, z = st["mpos"]
    s["xy_points"][label] = {"x": x, "y": y, "z": z, "time": calib_session.now()}
    s["xy_fit"] = None
    sess.save(s)
    _log(f"Calibration X/Y: nozzle over {label}", st["mpos"], "Studio Save nozzle position", "nozzle tip over the dot",
         st.get("line", ""))
    return _out(m)


def xy_fit(_params):
    m = _cal_mode()
    sess = _session(m)
    s = sess.load()
    if not s.get("xy_scan"):
        raise ValueError("Record the dot-sheet scan first")
    r = calib_session.fit_xy(s["xy_scan"], s["xy_points"])
    r["time"] = calib_session.now()
    s["xy_fit"] = r
    sess.save(s)
    _log("Calibration X/Y: fit (draft)", None, "Studio Fit X/Y", "rms <= 1 mm, scale 1 +- 0.02",
         f"{r['dots_used']} dots; rms {r['rms_mm']} mm; max {r['max_residual_mm']} mm; scale {r['scale_check']}; "
         f"rotation {r['rotation_deg']} deg; mirrored {r['mirrored']}; t {r['t_mm']}", r["verdict"])
    return _out(m)


def z_scan(params):
    m = _cal_mode()
    _targets_only(params)
    sess = _session(m)
    if not sess.load().get("xy_fit"):
        raise ValueError("Fit X/Y first - the touch spots are found through it")
    path, st, stamp = _record(m, "z")
    frame = _median_frame(path)
    rel = f"{stamp}-calz"
    calib_session.overlay(frame, [], os.path.join(base.OUT_ROOT, rel, "overlay.png"), f"block scan - {m['name']}")
    s = sess.load()
    s.update(z_scan={"recording": path, "time": calib_session.now(), "overlay": f"/outputs/{rel}/overlay.png",
                     "head": dict(zip("xyz", st["mpos"]))}, z_touches=[], z_fit=None)
    sess.save(s)
    _log("Calibration Z: block scan", st["mpos"], "Studio Record 3 s", "flat block in view", "recorded",
         recording=os.path.basename(path))
    return _out(m)


def z_touch(params):
    m = _cal_mode()
    if params.get("confirm_feeler") is not True:
        raise ValueError("Confirm that the paper strip is just pinched between the nozzle and the block")
    sess = _session(m)
    s = sess.load()
    if not s.get("z_scan"):
        raise ValueError("Record the block scan first")
    st = _fresh_idle_status()
    if st["mpos"][2] >= float(profiles.motion(m)["travel_z"]):
        raise ValueError("Lower the nozzle onto the block first (below travel Z)")
    label = f"T{len(s['z_touches']) + 1}"
    x, y, z = st["mpos"]
    s["z_touches"].append({"label": label, "x": x, "y": y, "z_contact": z, "time": calib_session.now()})
    s["z_fit"] = None
    sess.save(s)
    _log(f"Calibration Z: touch {label}", st["mpos"], "Studio Save touch (paper feeler)", "nozzle on the block",
         st.get("line", ""))
    return _out(m)


def z_fit(_params):
    m = _cal_mode()
    sess = _session(m)
    s = sess.load()
    if not s.get("z_scan") or not s.get("xy_fit"):
        raise ValueError("Fit X/Y and record the block scan first")
    head = s["z_scan"]["head"]
    r = calib_session.fit_z(_median_frame(s["z_scan"]["recording"]), head, s["z_touches"], s["xy_fit"])
    r["time"] = calib_session.now()
    s["z_fit"] = r
    sess.save(s)
    _log("Calibration Z: fit k (draft)", None, "Studio Fit Z", "k spread <= 1 mm",
         f"{len(s['z_touches'])} touches; k {r['k_mm']} mm; spread {r['k_spread_mm']} mm; z rms {r['z_rms_mm']} mm",
         r["verdict"])
    return _out(m)


def session_reset(params):
    m = _cal_mode()
    if params.get("confirm_reset") is not True:
        raise ValueError("Confirm starting the calibration over")
    _session(m).archive()
    _log("Calibration session restarted", None, "Studio Start over", "", "old session kept under old_sessions")
    return _out(m)


def machines_select(params):
    v24.machines_select(params)
    apply_active()
    return v25.machines_list()


# ---------- production flow: add, set up, approve, recalibrate ----------
MIN_TIP_AT_TRAVEL_MM = 40.0       # nozzle tip at travel Z at least this far above the bed (hands, prosthetic)
FLOOR_ABOVE_BED_MM = 4.0


def machines_add(params):
    if params.get("confirm_new") is not True:
        raise ValueError("Confirm adding a new machine")
    m = profiles.add(params.get("name"), params.get("drive"))
    _log("Machine added", None, "Studio Add machine", "a new machine profile",
         f"{m['name']} ({m['id']}), drive {m['drive']}; locked until set up and calibrated")
    return v25.machines_list()


def setup_area(params):
    m = profiles.active()
    if params.get("confirm_measured") is not True:
        raise ValueError("Confirm that the work area was read from the controller and checked with a ruler")
    try:
        x, y = float(params.get("work_area_x")), float(params.get("work_area_y"))
    except (TypeError, ValueError):
        raise ValueError("work area: two numbers in mm")
    if not (50 <= x <= 1000 and 50 <= y <= 1000):
        raise ValueError("work area must be 50 to 1000 mm on each axis")
    if machine.status().get("busy"):
        raise MachineError("Wait for the machine to finish")

    def change(e):
        if profiles.calibrated(e):
            raise MachineProfileError("Recalibrate the machine before changing its setup")
        e["work_area_mm"] = [x, y]
    profiles._edit(m["id"], change)
    apply_active()
    _log("Machine setup: work area", None, "Studio Setup", "measured travel", f"{m['name']}: X {x:g} x Y {y:g} mm")
    return v25.machines_list()


def setup_scan(params):
    """Scan position = where the head is now (camera over the hand area, chosen with the live picture); Z floor from
    the measured nozzle-tip height at Z 0."""
    m = profiles.active()
    if params.get("confirm_measured") is not True:
        raise ValueError("Confirm the nozzle-tip height was measured with a ruler at Z 0 and the hand area is centred")
    if not m.get("work_area_mm"):
        raise ValueError("Save the work area first")
    try:
        tip = float(params.get("tip_height_mm"))
        scan_z = float(params.get("scan_z", -20.0))
    except (TypeError, ValueError):
        raise ValueError("nozzle-tip height and scan Z: numbers in mm")
    travel = -20.0
    floor = round(-(tip - FLOOR_ABOVE_BED_MM), 1)
    if tip - abs(travel) < MIN_TIP_AT_TRAVEL_MM:
        raise ValueError(f"at travel Z {travel:g} the nozzle tip would be only {tip - abs(travel):g} mm above the bed "
                         f"(needs {MIN_TIP_AT_TRAVEL_MM:g}) - this machine needs another travel height first")
    if not (floor + MIN_TIP_AT_TRAVEL_MM <= scan_z <= travel):
        raise ValueError(f"scan Z must be between {floor + MIN_TIP_AT_TRAVEL_MM:g} and {travel:g}")
    st = _fresh_idle_status()
    x, y = round(st["mpos"][0], 1), round(st["mpos"][1], 1)
    if not (0 <= x <= m["work_area_mm"][0] and 0 <= y <= m["work_area_mm"][1]):
        raise ValueError("the head is outside the saved work area")
    src = (f"set up in Studio {calib_session.now()}: scan X{x:g} Y{y:g} (head position, camera centred on the hand "
           f"area), scan Z {scan_z:g}, nozzle tip {tip:g} mm above the bed at Z 0 -> floor {floor:g} "
           f"({FLOOR_ABOVE_BED_MM:g} mm above the bed), travel Z {travel:g}")
    profiles.setup(m["id"], m["work_area_mm"], [x, y, scan_z], travel, floor, src)
    apply_active()
    _log("Machine setup: scan position + Z floor", st["mpos"], "Studio Setup", "calibration mode", src)
    return v25.machines_list()


def approve(params):
    """Both fits GOOD + the approver's name + confirmations -> this machine's own calibration files; unlocks it."""
    m = _cal_mode()
    approver = " ".join(str(params.get("approver") or "").split())
    if len(approver) < 2:
        raise ValueError("Type the name of the person approving this calibration")
    # (no "first program will be an air run" tick - air runs were removed, user 2026-10-27)
    for k, text in (("confirm_dots", "every dot in the scan picture is the right dot"),
                    ("confirm_touches", "every touch was made with the paper strip just pinched")):
        if params.get(k) is not True:
            raise ValueError(f"Confirm: {text}")
    s = _session(m).load()
    if not (s.get("xy_fit") and s["xy_fit"].get("verdict") == "GOOD"):
        raise ValueError("The X/Y fit is missing or not GOOD - fix it before approving")
    if not (s.get("z_fit") and s["z_fit"].get("verdict") == "GOOD"):
        raise ValueError("The Z fit is missing or not GOOD - fix it before approving")
    from henna_preview.daily_check import recording_serial
    serial = recording_serial(s["xy_scan"]["recording"])
    stamp = v10.dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    mdir_rel = f"machines/{m['id']}"
    os.makedirs(os.path.join(base.ROOT, mdir_rel), exist_ok=True)
    mo = profiles.motion(m)
    common = {"created": calib_session.now(), "motion_ready": False, "tool": f"nozzle (machine {m['id']})",
              "machine": m["id"], "camera_serial": serial, "approved_by": approver}
    xy_doc = dict(common, schema="camera_tool_xy_calibration/1", axes="X/Y only (Z excluded)",
                  model="tool_XY = head_XY_at_capture + R @ camera_xy_mm + t",
                  valid_only_for="this machine, this nozzle, head at its scan position, this camera mount and bed; "
                                 "recalibrate after any change",
                  inputs={"recording": s["xy_scan"]["recording"], "head_pose_at_capture": s["xy_scan"]["head"],
                          "dots": s["xy_scan"]["dots"], "nozzle_points": s["xy_points"]}, result=s["xy_fit"])
    z_doc = dict(common, schema="camera_tool_z_calibration/1", model="Z_contact = Z_capture - (camera_depth_mm - k_mm)",
                 valid_only_for="this machine, this nozzle, this camera mount",
                 inputs={"recording": s["z_scan"]["recording"], "head": s["z_scan"]["head"],
                         "touches": s["z_touches"]}, result=s["z_fit"])
    with open(os.path.join(base.ROOT, "current_calibration.json"), encoding="utf-8") as f:
        rules = json.load(f)
    xy_rel, z_rel, cal_rel = (f"{mdir_rel}/xy_calibration_{stamp}.json", f"{mdir_rel}/z_calibration_{stamp}.json",
                              f"{mdir_rel}/calibration_{stamp}.json")
    cal_doc = {"schema": "current_calibration/1", "machine": m["id"], "camera_serial": serial,
               "note": f"The approved calibration of {m['name']} (made and approved in Studio v26).",
               "decided_by": f"{approver}, approved in Studio {calib_session.now()}; X/Y {s['xy_fit']['dots_used']} dots "
                             f"rms {s['xy_fit']['rms_mm']} mm; Z k {s['z_fit']['k_mm']} spread {s['z_fit']['k_spread_mm']} mm; "
                             "processing and path rules as current_calibration.json",
               "capture_pose_mpos": mo["scan_pose_mpos"], "xy_calibration": xy_rel, "z_calibration": z_rel,
               "depth_scale": rules["depth_scale"], "travel_z": mo["travel_z"], "firmware_z_floor": mo["firmware_z_floor"]}
    for k in ("frames", "min_valid_frac", "max_mad_mm", "floor_above_skin_mm", "draw_feed_mm_min", "travel_feed_mm_min",
              "standoff_mm_allowed", "fit_mm_allowed", "paper_mm_allowed"):
        cal_doc[k] = rules[k]
    for rel, doc in ((xy_rel, xy_doc), (z_rel, z_doc), (cal_rel, cal_doc)):
        with open(os.path.join(base.ROOT, rel), "w", encoding="utf-8", newline="\n") as f:
            json.dump(doc, f, indent=2)
    profiles.approve(m["id"], cal_rel, approver)
    profiles.check_consistent(profiles.get(m["id"]))
    apply_active()
    _log("Calibration APPROVED", None, "Studio Approve", "both fits GOOD + confirmations",
         f"{m['name']}: {cal_rel}; camera {serial}; approved by {approver}", "APPROVED")
    return v25.machines_list()


def recalibrate(params):
    m = profiles.active()
    who = " ".join(str(params.get("by") or "").split())
    if params.get("confirm_recalibrate") is not True or len(who) < 2:
        raise ValueError("Type your name and confirm: this machine is locked until its new calibration is approved")
    old = m.get("calibration")
    profiles.recalibrate(m["id"], who)
    apply_active()
    _log("Recalibration started", None, "Studio Recalibrate", "old calibration retired, machine in calibration mode",
         f"{m['name']}: retired {old} (by {who})")
    return v25.machines_list()


class Handler(v25.Handler):
    server_version = "HennaStudio/0.26"
    GET_API = dict(v25.Handler.GET_API, **{"/api/calibration/session": session_get})
    POST_API = dict(v25.Handler.POST_API, **{
        "/api/machines/select": (machines_select, False),
        "/api/calibration/jog_z": (jog_z, False),
        "/api/calibration/jog_xy": (jog_xy, False),
        "/api/calibration/xy_scan": (xy_scan, True),
        "/api/calibration/xy_point": (xy_point, True),
        "/api/calibration/xy_fit": (xy_fit, True),
        "/api/calibration/z_scan": (z_scan, True),
        "/api/calibration/z_touch": (z_touch, True),
        "/api/calibration/z_fit": (z_fit, True),
        "/api/calibration/session_reset": (session_reset, True),
        "/api/calibration/approve": (approve, True),
        "/api/machines/add": (machines_add, False),
        "/api/machines/setup_area": (setup_area, False),
        "/api/machines/setup_scan": (setup_scan, False),
        "/api/machines/recalibrate": (recalibrate, False),
    })
    ERRORS = v25.Handler.ERRORS + (CalibrationError,)

    def do_GET(self):
        if urlparse(self.path).path in ("/", "/index.html", "/studio"):
            if self._host_ok():
                return self._file(STUDIO_HTML, "text/html; charset=utf-8")
            return
        return super().do_GET()


def main():
    vals = apply_active()
    srv = base.http.server.ThreadingHTTPServer((base.HOST, base.PORT), Handler)
    srv.daemon_threads = False
    mid = profiles.active_id()
    print(f"Henna Studio v26 - calibrating in Studio (active: {mid or 'none chosen'}"
          f"{'; scan position ' + str(list(vals['POSE'])) if vals else ''}"
          f"{'; calibration jog floor ' + str(machine.cal_floor) if machine.cal_floor is not None else ''}). "
          "motion_ready=false", flush=True)
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
