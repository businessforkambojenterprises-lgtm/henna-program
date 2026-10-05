"""Combine one saved design preview with its accepted region, hand detection and recording.

Re-checks every link in the chain before writing anything:
recording file still matches its checksum, the region is accepted and was
drawn on that recording + frame, and the model checksum is the approved one.
"""
import datetime as dt
import hashlib
import json
import os

from henna_preview import MOTION_READY
from henna_preview.hand_detect import MODEL_SHA256
from henna_preview.region import RegionError, require_accepted

NOT_VALIDATED = [
    "Camera-to-nozzle offset is not measured or validated (pencil-tip coordinates are not nozzle-tip coordinates).",
    "Z / tool reference is not validated; an earlier descent hit the bed and the cause is unresolved.",
    "Soft limits have failed to prevent excessive Z travel; Pn is unresolved.",
    "Head pose at capture is not recorded in this recording; camera-frame points cannot be mapped to the machine.",
    "Older calibration sessions must not be mixed with these measurements.",
]


class ReportError(ValueError):
    """Raised when the chain of inputs cannot be verified."""


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


def _json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def build_final_report(design_dir, region_dir, check_recording=True):
    design = _json(os.path.join(design_dir, "design_report.json"))
    region = _json(os.path.join(region_dir, "region.json"))
    src = region["source"]
    hand = _json(os.path.join(src["hand_run"], "report.json"))

    if design.get("motion_ready") is not False:
        raise ReportError("design report is not marked motion_ready=false")
    try:
        require_accepted(region, design["inputs"]["recording_sha256"], design["inputs"]["frame_index"])
    except RegionError as e:
        raise ReportError(str(e))
    if hand["input"]["sha256"] != src["recording_sha256"] or hand["input"]["frame_index"] != src["frame_index"]:
        raise ReportError("hand-detection run does not match the region's recording/frame")
    if hand["model"]["sha256"] != MODEL_SHA256:
        raise ReportError("hand-detection run used an unapproved model file")
    rec_check = "skipped"
    if check_recording:
        if not os.path.isfile(src["recording"]):
            raise ReportError("original recording file is missing")
        if _sha256(src["recording"]) != src["recording_sha256"]:
            raise ReportError("original recording has changed since it was processed")
        rec_check = "matches"

    h0 = hand["hands"][0] if hand.get("hands") else {}
    return {
        "schema": "henna_preview_final/1",
        "created": dt.datetime.now().isoformat(timespec="seconds"),
        "motion_ready": MOTION_READY,
        "gcode": None,
        "machine_coordinates": None,
        "purpose": "Offline preview only. Not a toolpath. Must not be sent to the machine.",
        "inputs": {
            "recording": src["recording"], "recording_sha256": src["recording_sha256"],
            "recording_checksum_now": rec_check, "frame_index": src["frame_index"],
            "device": hand["input"].get("device"), "streams": hand["input"].get("streams"),
            "depth_frame_number": hand["input"].get("depth_frame_number"),
            "colour_frame_number": hand["input"].get("colour_frame_number"),
            "hand_run": src["hand_run"], "region": os.path.abspath(region_dir), "design": os.path.abspath(design_dir),
        },
        "units": {"path_points": "mm", "image": "px", "depth_scale_m_per_unit": hand["units"]["depth_scale_m_per_unit"],
                  "depth_units_source": hand["units"]["units_source"]},
        "coordinate_frames": {
            "image": "colour image pixels, u right, v down, origin top-left",
            "points": "colour camera frame, SDK deprojection with the colour lens model, millimetres",
            "machine": None,
        },
        "camera_intrinsics_colour": hand["colour_intrinsics"],
        "hand_detection": {"model": "MediaPipe Hand Landmarker (pretrained)", "model_sha256": hand["model"]["sha256"],
                           "detected": hand["hand_detected"], "score": h0.get("score"),
                           "role": "suggested the region outline only"},
        "region": {"accepted": region["accepted"], "edited_by_user": region["suggestion"]["edited_by_user"],
                   "created": region.get("created"), "polygon_px": region["polygon_px"], **region["stats"],
                   "missing_depth_policy": region["missing_depth_policy"]},
        "design": {"params": design["params"], "planned": design["planned"]},
        "validity": design["validity"],
        "points": {"measured": design["validity"]["path_points_measured"],
                   "interpolated": design["validity"]["path_points_interpolated"],
                   "planned_samples": design["validity"]["planned_samples"],
                   "note": "Measured = depth read at a pixel. Planned = the design curve before projection. Nothing interpolated."},
        "timings_s": {"hand_detection": hand["timings_s"], "region": region.get("timings_s"), "design": design["timings_s"]},
        "timing_note": "measured on this PC for single frames; not a real-time claim",
        "warnings": design["warnings"],
        "not_validated_before_any_machine_use": NOT_VALIDATED,
    }
