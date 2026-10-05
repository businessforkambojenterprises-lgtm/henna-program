"""Check a saved camera-to-tool X/Y calibration against a different head pose (no re-fitting).

For each clicked dot in the verification recording:
    predicted_tool_XY = head_XY_at_capture + R @ camera_xy_mm + t     (R, t from the saved calibration)
and the error is predicted minus the tool X/Y actually hovered over that dot.
"""
import numpy as np

from henna_preview.calibration import CalibrationError, dot_camera_mm, predict


def check_compatible(calib, targets):
    if calib.get("schema") != "camera_tool_xy_calibration/1":
        raise CalibrationError("not a camera-to-tool calibration file")
    if calib.get("tool") != targets.get("tool"):
        raise CalibrationError(f"tool mismatch: calibration is '{calib.get('tool')}', targets are '{targets.get('tool')}'")
    rec_cal = calib["inputs"]["head_pose_at_capture"].get("recording")
    rec_ver = targets["head_pose_at_capture"].get("recording")
    if rec_cal and rec_cal == rec_ver:
        raise CalibrationError("verification must use a different recording / head pose than the calibration")


def verify(frame, targets, clicks, calib):
    check_compatible(calib, targets)
    R = np.asarray(calib["result"]["R"], float)
    t = np.asarray(calib["result"]["t_mm"], float)
    head = targets["head_pose_at_capture"]
    raw, scale, intr = frame["depth_raw_aligned"], frame["depth_scale_m_per_unit"], frame["colour_intrinsics"]
    dots, errs = [], []
    for label, uv in sorted(clicks.items()):
        if label not in targets["targets"]:
            raise CalibrationError(f"'{label}' is not in the targets file")
        p, n = dot_camera_mm(raw, scale, intr, uv)
        d = {"label": label, "pixel_uv": [round(float(uv[0]), 1), round(float(uv[1]), 1)],
             "machine_xy": targets["targets"][label], "depth_pixels_used": n}
        if p is None:
            d["status"] = "rejected: no measured depth near the click"
        else:
            pred = predict(p[:2], [head["x"], head["y"]], R, t)
            e = pred - np.asarray(targets["targets"][label], float)
            d.update({"status": "used", "camera_mm": [round(float(c), 2) for c in p],
                      "predicted_xy": [round(float(v), 2) for v in pred],
                      "error_xy_mm": [round(float(v), 2) for v in e], "error_mm": round(float(np.hypot(*e)), 2)})
            errs.append(e)
        dots.append(d)
    if len(errs) < 3:
        raise CalibrationError("fewer than 3 clicked dots have measured depth")
    E = np.array(errs)
    lens = np.hypot(E[:, 0], E[:, 1])
    return {"dots": dots, "dots_used": len(errs), "rms_mm": round(float(np.sqrt((lens ** 2).mean())), 2),
            "max_error_mm": round(float(lens.max()), 2),
            "mean_error_xy_mm": [round(float(v), 2) for v in E.mean(0)],
            "calibration_fit_rms_mm": calib["result"]["rms_mm"]}
