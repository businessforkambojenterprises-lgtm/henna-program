"""Studio v18 on port 8767 with a SIMULATED controller, no camera, and a TEMPORARY COPY of the desktop app's jobs.

For checking the page with real scans (Design step, names on the hand). No serial port and no camera are opened;
the real app folder is only read (copied), never written. Uploaded/name designs go to a temporary folder.
"""
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import webui_server as base  # noqa: E402

APP_JOBS = os.path.join(os.path.expanduser("~"), "Desktop", "HennaStudio-Windows-x64", "app", "outputs", "webui")
base.PORT = 8767
base.ALLOWED_HOSTS = {"127.0.0.1:8767", "localhost:8767"}
work = tempfile.mkdtemp(prefix="studio_v18_sim_")
base.OUT_ROOT = os.path.join(work, "webui")
shutil.copytree(APP_JOBS, base.OUT_ROOT)

import webui_server_v10 as v10  # noqa: E402
import webui_server_v18 as studio  # noqa: E402
from henna_preview.grbl_program_link_v15 import GrblProgramLinkV15  # noqa: E402
from tests.test_grbl_program_link import FakeGrbl  # noqa: E402

OFF = {"state": "off", "has_frame": False, "error": "simulation - camera not used"}
v10.camera.start_preview = lambda: dict(OFF)
v10.camera.status = lambda: dict(OFF)
v10.camera.stop = lambda: dict(OFF)
v10.log_row = lambda *a, **k: None
studio.v11.UPLOAD_DIR = os.path.join(work, "uploads")
v14 = studio.v14
v14.v13.v12.ports = lambda: [{"device": "SIM", "description": "simulated controller"}]
v14.v13.v12.default_port = lambda: "SIM"
fake = FakeGrbl(capacity=4, move_s=0.5)
link = GrblProgramLinkV15(calibration_provider=v10.cal, opener=lambda port: fake, on_event=lambda e: None)
studio.v16.v15.machine = link
v14.machine = link
v14.v13.machine = link
print("SIMULATION - no serial port, no camera. Jobs copied to", base.OUT_ROOT, flush=True)
studio.main()
