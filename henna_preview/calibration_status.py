"""Calibration status for Studio v23 (read-only): which approved calibration is in use and its key numbers.

Nothing here changes a calibration: it reads current_calibration.json and the three files it names
(X/Y camera -> nozzle, Z camera depth -> nozzle height, depth scale) and reports what they say.
"""
import datetime as dt
import hashlib
import json
import os


def _load(root, rel):
    path = os.path.join(root, rel)
    try:
        with open(path, "rb") as f:
            raw = f.read()
        return json.loads(raw.decode("utf-8")), hashlib.sha256(raw).hexdigest()[:12], None
    except (OSError, ValueError) as e:
        return None, None, f"{type(e).__name__}: {e}"


def _age_days(created, today):
    try:
        return (today - dt.date.fromisoformat(str(created)[:10])).days
    except ValueError:
        return None


def status(root, cal, today=None):
    """-> {'items': [...], 'capture_pose_mpos', 'travel_z', 'decided_by', 'ok'} - never raises for a bad file."""
    today = today or dt.date.today()
    items = []

    def item(key, name, what, values):
        doc, sha, err = _load(root, cal.get(key, ""))
        entry = {"key": key, "name": name, "what": what, "file": cal.get(key), "ok": doc is not None, "sha256": sha,
                 "error": err}
        if doc is not None:
            created = doc.get("created")
            entry.update(created=created, age_days=_age_days(created, today), tool=doc.get("tool"),
                         valid_only_for=doc.get("valid_only_for"), values=values(doc))
        items.append(entry)

    def r(doc, *path):
        for p in path:
            doc = doc.get(p, {}) if isinstance(doc, dict) else {}
        return doc if doc != {} else None

    item("xy_calibration", "X/Y", "camera picture position -> nozzle X/Y",
         lambda d: {"rms_mm": r(d, "result", "rms_mm"), "max_residual_mm": r(d, "result", "max_residual_mm"),
                    "rotation_deg": r(d, "result", "rotation_deg"),
                    "block_shift_rms_mm": r(d, "block_shift", "rms_after_used_points_mm"),
                    "held_out_point_error_mm": r(d, "block_shift", "held_out_point_error_mm")})
    item("z_calibration", "Z", "camera depth -> nozzle height",
         lambda d: {"k_mm": r(d, "result", "k_mm"), "k_spread_mm": r(d, "result", "k_spread_mm"),
                    "z_rms_mm": r(d, "result", "z_rms_mm")})
    item("depth_scale", "Depth scale", "correction of this camera's depth reading",
         lambda d: {"g": d.get("g"), "d_ref_mm": d.get("d_ref_mm"), "fit_rms_mm": d.get("fit_rms_mm")})
    return {"items": items, "ok": all(i["ok"] for i in items), "capture_pose_mpos": cal.get("capture_pose_mpos"),
            "travel_z": cal.get("travel_z"), "decided_by": cal.get("decided_by")}
