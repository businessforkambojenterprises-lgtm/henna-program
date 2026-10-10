"""Studio v15 on port 8767 with a SIMULATED controller and no camera - for checking the page only.

No serial port and no camera are opened. Jobs are written to a temporary folder with one demo job
(prosthetic, program written by v14) so the Run step can be exercised: Connect, Home, Start, Pause, STOP.
"""
import hashlib
import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import webui_server as base  # noqa: E402

base.PORT = 8767
base.ALLOWED_HOSTS = {"127.0.0.1:8767", "localhost:8767"}
base.OUT_ROOT = tempfile.mkdtemp(prefix="studio_v14_sim_")

import webui_server_v10 as v10  # noqa: E402
import webui_server_v15 as studio  # noqa: E402
from henna_preview.grbl_program_link_v15 import GrblProgramLinkV15  # noqa: E402
from tests.test_grbl_program_link import PROGRAM, FakeGrbl  # noqa: E402

OFF = {"state": "off", "has_frame": False, "error": "simulation - camera not used"}
v10.camera.start_preview = lambda: dict(OFF)
v10.camera.status = lambda: dict(OFF)
v10.camera.stop = lambda: dict(OFF)
studio.v13.v12.ports = lambda: [{"device": "SIM", "description": "simulated controller"}]
studio.v13.v12.default_port = lambda: "SIM"
v10.log_row = lambda *a, **k: None          # never write simulated rows to the validation log

fake = FakeGrbl(capacity=1000, move_s=1.0)   # big planner: every line acknowledged at once
fake.mpos = [145.8, 42.6, -40.0]
link = GrblProgramLinkV15(calibration_provider=v10.cal, opener=lambda port: fake, on_event=lambda e: None)
studio.machine = link
studio.v14.machine = link
studio.v13.machine = link

JOB, PLAN, PROG = "20261010-120000-job", "20261010-120001-plan", "20261010-120002-program"
GCODE = "DESIGN_HOVER_TEST_PROSTHETIC_20261010-120002.gcode"
for d in (JOB, PLAN, PROG):
    os.makedirs(os.path.join(base.OUT_ROOT, d))
gfile = os.path.join(base.OUT_ROOT, PROG, GCODE)
with open(gfile, "w", encoding="ascii", newline="\n") as f:
    f.write(PROGRAM)
shutil.copyfile(os.path.join(ROOT, "designs", "new_designs_preview.png"), os.path.join(base.OUT_ROOT, PROG, "preview.png"))
with open(gfile, "rb") as f:
    sha = hashlib.sha256(f.read()).hexdigest()


def save(rel, obj):
    with open(os.path.join(base.OUT_ROOT, rel), "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=1)


save(f"{PLAN}/plan.json", {"config": {"standoff_mm": 4.0}})
save(f"{PROG}/program.json", {"program_file": gfile, "standoff_mm": 4.0, "use_offset": False})
save(f"{PROG}/step22_report.json", {"global_floor_in_program": -64.7})
save(f"{JOB}/job.json", {
    "id": JOB, "created": "2026-10-10T12:00:00", "updated": "2026-10-10T12:00:00", "subject": "prosthetic",
    "subject_text": "prosthetic hand (no person)", "steps": {
        "scan": {"name": "20261010_115900_000001.db3", "status_line": "<Idle|MPos:145.800,42.600,-40.000|FS:0,0>",
                 "mpos": [145.8, 42.6, -40.0], "frame_pairs": 135},
        "hand": {"score": 0.98}, "region": {"id": "sim", "polygon_px": []},
        "design": {"svg": "designs/lotus.svg", "fit_mm": 30.0, "rotation_deg": 0.0, "centre_machine_xy": [84.7, 68.3],
                   "standoff_mm": 4.0, "verdict": "PASS"},
        "plan": {"plan": PLAN, "estimated_run_time_s": 60},
        "program": {"program": PROG, "program_file": gfile, "download": "#", "preview": f"/outputs/{PROG}/preview.png",
                    "reread": {"lines": 15, "strokes": 2, "draw_moves": 5, "z_min": -63.664, "z_max": -56.043, "problems": 0},
                    "use_offset": False, "offset_mm": 0.0, "standoff_mm": 4.0, "sha256": sha, "studio": "v14",
                    "nozzle_z_range": [-63.664, -56.043], "estimated_run_time_s": 60}}})
print("SIMULATION - no serial port, no camera. Jobs in", base.OUT_ROOT, flush=True)
studio.main()
