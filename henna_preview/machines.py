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

    def summary(self):
        mid = self.active_id()
        return {"active": mid, "machines": [{"id": m["id"], "name": m["name"], "drive": m.get("drive"),
                                             "calibrated": self.calibrated(m), "work_area_mm": m.get("work_area_mm"),
                                             "calibration_mode": self.calibration_mode(m),
                                             "scan_pose_mpos": (m.get("motion") or {}).get("scan_pose_mpos"),
                                             "notes": m.get("notes"), "active": m["id"] == mid}
                                            for m in self.machines()]}
