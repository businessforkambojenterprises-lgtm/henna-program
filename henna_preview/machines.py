"""Machine profiles (Studio v24): each physical machine has its own name and its own calibration.

machines/registry.json (in the project, approved) lists the machines. Which one this computer is driving is a
local choice, kept in outputs/active_machine.json - there is no default: Studio asks. A machine whose
"calibration" is null is NOT CALIBRATED: active_cal() refuses, so nothing that needs the calibration (scan
position, scan, design check, program, run, daily check) can run on it.
The belt-drive machine's calibration is current_calibration.json, read unchanged.
"""
import datetime as dt
import json
import os


class MachineProfileError(ValueError):
    """No machine chosen, an unknown machine, or a machine without a calibration."""


def _j(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


class Profiles:
    def __init__(self, root, state_dir=None):
        self.root = root
        self.registry_path = os.path.join(root, "machines", "registry.json")
        self.state_path = os.path.join(state_dir or os.path.join(root, "outputs"), "active_machine.json")

    def machines(self):
        return _j(self.registry_path)["machines"]

    def get(self, machine_id):
        for m in self.machines():
            if m["id"] == machine_id:
                return m
        raise MachineProfileError(f"unknown machine '{machine_id}'")

    def active_id(self):
        try:
            mid = _j(self.state_path).get("id")
        except (OSError, ValueError):
            return None
        try:
            self.get(mid)
        except MachineProfileError:
            return None
        return mid

    def active(self):
        mid = self.active_id()
        if mid is None:
            raise MachineProfileError("Choose the machine first: Calibration -> Machine")
        return self.get(mid)

    def select(self, machine_id):
        m = self.get(machine_id)
        os.makedirs(os.path.dirname(self.state_path), exist_ok=True)
        tmp = self.state_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"id": m["id"], "chosen": dt.datetime.now().isoformat(timespec="seconds")}, f, indent=2)
        os.replace(tmp, self.state_path)
        return m

    @staticmethod
    def calibrated(m):
        return bool(m.get("calibration"))

    def cal_of(self, m):
        if not self.calibrated(m):
            raise MachineProfileError(f"The {m['name']} is not calibrated yet - no scan position, scan, design check, "
                                      "program or run until its own calibration is made and approved")
        return _j(os.path.join(self.root, m["calibration"]))

    def active_cal(self):
        """The calibration dict Studio uses everywhere (replaces webui_server_v10.cal) - refuses if none."""
        return self.cal_of(self.active())

    # ---- v25: per-machine motion values and calibration mode ----
    @staticmethod
    def motion(m):
        mo = m.get("motion")
        if not mo or not m.get("work_area_mm"):
            raise MachineProfileError(f"The {m['name']} has no scan position / travel Z / work area yet")
        return mo

    def calibration_mode(self, m):
        return not self.calibrated(m) and bool(m.get("calibration_mode")) and bool(m.get("motion"))

    def motion_cfg(self, m):
        """What the machine link checks before every move: its scan pose, travel Z, feeds and Z floor."""
        mo = self.motion(m)
        return {"capture_pose_mpos": list(mo["scan_pose_mpos"]), "travel_z": mo["travel_z"],
                "firmware_z_floor": mo["firmware_z_floor"], "draw_feed_mm_min": 300.0, "travel_feed_mm_min": 1000.0,
                "machine": m["id"], "note": "motion values of this machine (registry), not a calibration"}

    def check_consistent(self, m):
        """A calibrated machine's calibration file must agree with its registry motion values."""
        if not self.calibrated(m):
            return
        cal, mo = self.cal_of(m), self.motion(m)
        for key, ck in (("scan_pose_mpos", "capture_pose_mpos"), ("travel_z", "travel_z"),
                        ("firmware_z_floor", "firmware_z_floor")):
            if [float(v) for v in (cal[ck] if isinstance(cal[ck], list) else [cal[ck]])] != \
                    [float(v) for v in (mo[key] if isinstance(mo[key], list) else [mo[key]])]:
                raise MachineProfileError(f"The {m['name']}'s calibration file and its registry disagree on {key}")

    # ---- v26: adding, setting up, approving and recalibrating machines from Studio (production flow) ----
    def _write(self, r):
        """Atomic write; the previous registry is kept under machines/history/."""
        hist = os.path.join(os.path.dirname(self.registry_path), "history")
        os.makedirs(hist, exist_ok=True)
        if os.path.isfile(self.registry_path):
            stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            path, n = os.path.join(hist, f"registry_{stamp}.json"), 1
            while os.path.exists(path):                    # Windows clock ticks ~1-15 ms: never overwrite a copy
                path, n = os.path.join(hist, f"registry_{stamp}_{n}.json"), n + 1
            with open(self.registry_path, "rb") as f, open(path, "xb") as g:
                g.write(f.read())
        tmp = self.registry_path + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            json.dump(r, f, indent=2)
            f.write("\n")
        os.replace(tmp, self.registry_path)

    def _edit(self, machine_id, change):
        r = _j(self.registry_path)
        m = next((x for x in r["machines"] if x["id"] == machine_id), None)
        if m is None:
            raise MachineProfileError(f"unknown machine '{machine_id}'")
        change(m)
        self._write(r)
        return m

    def add(self, name, drive):
        import re
        name = " ".join(str(name or "").split())
        if not 2 <= len(name) <= 60:
            raise MachineProfileError("give the machine a name (2 to 60 characters)")
        mid = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "machine"
        r = _j(self.registry_path)
        ids = {m["id"] for m in r["machines"]}
        base, n = mid, 2
        while mid in ids:
            mid, n = f"{base}-{n}", n + 1
        m = {"id": mid, "name": name, "drive": " ".join(str(drive or "").split())[:40] or "unknown",
             "calibration": None, "work_area_mm": None, "motion": None,
             "added": dt.datetime.now().isoformat(timespec="seconds"),
             "notes": "added in Studio; needs setup (work area, nozzle height, scan position), then calibration"}
        r["machines"].append(m)
        self._write(r)
        return m

    def setup(self, machine_id, work_area, scan_pose, travel_z, floor, source):
        def change(m):
            if self.calibrated(m):
                raise MachineProfileError("Recalibrate the machine before changing its setup")
            m["work_area_mm"] = [float(work_area[0]), float(work_area[1])]
            m["motion"] = {"scan_pose_mpos": [float(v) for v in scan_pose], "travel_z": float(travel_z),
                           "firmware_z_floor": float(floor), "source": source}
            m["calibration_mode"] = "setup done in Studio " + dt.datetime.now().isoformat(timespec="seconds")
        return self._edit(machine_id, change)

    def approve(self, machine_id, cal_rel, approver):
        def change(m):
            if self.calibrated(m):
                raise MachineProfileError("This machine already has a calibration")
            m["calibration"] = cal_rel
            m["approved"] = {"by": approver, "time": dt.datetime.now().isoformat(timespec="seconds"), "file": cal_rel}
        return self._edit(machine_id, change)

    def recalibrate(self, machine_id, by):
        def change(m):
            if not self.calibrated(m):
                raise MachineProfileError("This machine has no calibration to replace")
            m.setdefault("previous_calibrations", []).append(
                {"file": m["calibration"], "retired_by": by, "time": dt.datetime.now().isoformat(timespec="seconds")})
            m["calibration"] = None
            m["calibration_mode"] = "recalibration started in Studio " + dt.datetime.now().isoformat(timespec="seconds")
        return self._edit(machine_id, change)

    def summary(self):
        mid = self.active_id()
        return {"active": mid, "machines": [{"id": m["id"], "name": m["name"], "drive": m.get("drive"),
                                             "calibrated": self.calibrated(m), "work_area_mm": m.get("work_area_mm"),
                                             "calibration_mode": self.calibration_mode(m),
                                             "needs_setup": not self.calibrated(m) and not m.get("motion"),
                                             "approved": m.get("approved"),
                                             "scan_pose_mpos": (m.get("motion") or {}).get("scan_pose_mpos"),
                                             "notes": m.get("notes"), "active": m["id"] == mid}
                                            for m in self.machines()]}
