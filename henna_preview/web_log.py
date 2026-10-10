"""Append rows to the validation log CSV from the web page (same columns as the hand-written rows)."""
import csv
import datetime as dt
import os
import threading

COLUMNS = ["session_id", "date_time", "phase", "step", "tool_fitted", "head_mpos_x", "head_mpos_y", "head_mpos_z",
           "command_sent", "expected", "measured", "pass_fail", "recording_file", "notes"]
_lock = threading.Lock()


def session_id(today=None):
    today = today or dt.date.today()
    return f"S{today.isoformat()}-W"


def append(path, phase, step, mpos=None, command="", expected="", measured="", result="", recording="", notes=""):
    with open(path, encoding="utf-8") as f:
        header = f.readline().strip().split(",")
    if header != COLUMNS:
        raise ValueError("validation log has unexpected columns - not written")
    x, y, z = (f"{v:.3f}" for v in mpos) if mpos else ("", "", "")
    row = [session_id(), dt.date.today().isoformat(), phase, step, "nozzle", x, y, z, command, expected, measured,
           result, recording, "web page" + (f"; {notes}" if notes else "")]
    with _lock:
        with open(path, "rb+") as f:          # make sure the last row ends with a newline
            f.seek(0, os.SEEK_END)
            if f.tell() > 0:
                f.seek(-1, os.SEEK_END)
                if f.read(1) != b"\n":
                    f.write(b"\n")
        with open(path, "a", encoding="utf-8", newline="") as f:
            csv.writer(f, lineterminator="\n").writerow(row)
    return row
