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

    def summary(self):
        mid = self.active_id()
        return {"active": mid, "machines": [{"id": m["id"], "name": m["name"], "drive": m.get("drive"),
                                             "calibrated": self.calibrated(m), "work_area_mm": m.get("work_area_mm"),
                                             "notes": m.get("notes"), "active": m["id"] == mid}
                                            for m in self.machines()]}
