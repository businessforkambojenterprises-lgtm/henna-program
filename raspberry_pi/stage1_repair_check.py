"""Raspberry Pi port - stage 1b: Studio's own recording repair + check on the Pi (camera files only).

librealsense 2.58.4 writes a late repeated camera setting into recordings, so playback reports depth units 0.001
instead of 0.0001 (the same on the laptop). Studio's recorder removes only those late REPEATS and then verifies
the file. This runs exactly that code (henna_preview.web_recorder) on a COPY of the newest stage-1 recording.
Usage:  ~/henna-venv/bin/python ~/henna-program/raspberry_pi/stage1_repair_check.py [recording.db3]
"""
import glob
import json
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from henna_preview.web_recorder import fix_late_option_repeats, verify_recording  # noqa: E402

src = sys.argv[1] if len(sys.argv) > 1 else max(
    glob.glob(os.path.join(os.path.expanduser("~"), "henna_pi_check", "*.db3")), key=os.path.getmtime)
src = [p for p in [src] if not p.endswith("_repaired.db3")][0]
copy = src.replace(".db3", "_repaired.db3")
shutil.copyfile(src, copy)
print("Recording:", src)
removed = fix_late_option_repeats(copy)
print("Late repeated settings removed:", removed)
try:
    v = verify_recording(copy)
except Exception as e:  # CameraError explains what is wrong
    print("\n=== STAGE 1b RESULT: FAIL ===\n ", e)
    sys.exit(2)
print("Verified:", json.dumps(v))
ok = abs(v["depth_units_m"] - 0.0001) < 1e-7 and v["frame_pairs"] >= 60
print("\n=== STAGE 1b RESULT:", "PASS" if ok else "CHECK", "===")
print(f"  depth units {v['depth_units_m']:g} m | camera settings {v['camera_settings']} | frame pairs {v['frame_pairs']}"
      f" | median depth {v['median_depth_mm']} mm")
