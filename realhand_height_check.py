"""Real hand - camera only: how tall is the hand compared with the machine's travel height?

Reads a finished Studio job (scan + hand detection, a REAL-HAND job by default) and measures, with the SAME
functions Studio's design check uses (15-frame median depth, depth-scale correction, Z calibration,
hand_top_from_raw), the height of every detected hand point and of the hand's highest point.
Studio needs:  travel Z (-20)  >=  hand top + standoff (4 mm or more).
Nothing here talks to the CNC or changes a job; it writes a picture + JSON to outputs/realhand_checks/<time>/.
Camera only - motion_ready stays false; a real hand never gets a program or a run.

  python realhand_height_check.py                 (the newest real-hand job)
  python realhand_height_check.py --job 20261012-131500-job [--standoff 4]
"""
import argparse
import datetime as dt
import json
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.abspath(__file__))


def _j(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ---------- pure helpers (unit-tested: tests/test_realhand_height_check.py) ----------
def contact_z(depth_mm, k_mm, head_z, depth_fn):
    """Studio's mapping (step19.hand_top_from_raw): camera depth -> machine Z of that surface."""
    return head_z - (depth_fn(depth_mm) - k_mm)


def point_depth_mm(raw, scale, uv, win=2):
    """Median valid depth (mm) in a (2*win+1)^2 window around pixel uv=(u, v), or None."""
    h, w = raw.shape
    u, v = int(round(uv[0])), int(round(uv[1]))
    if not (0 <= u < w and 0 <= v < h):
        return None
    patch = raw[max(0, v - win):v + win + 1, max(0, u - win):u + win + 1].astype(float)
    patch = patch[patch > 0]
    return float(np.median(patch)) * scale * 1000.0 if patch.size else None


def top_location(raw, scale, inside, pct=0.5):
    """Pixel (u, v) centre of the highest 0.5 % of usable depth inside the mask (where Studio's hand top is)."""
    d = raw.astype(float) * scale * 1000.0
    ok = inside & (raw > 0)
    thr = np.percentile(d[ok], pct)
    vs, us = np.nonzero(ok & (d <= thr))
    return float(us.mean()), float(vs.mean())


def nearest(names, landmarks, uv):
    dists = np.hypot(*(np.asarray(landmarks, float) - np.asarray(uv, float)).T)
    i = int(np.argmin(dists))
    return names[i], float(dists[i])


def verdict(top_z, travel_z, standoff):
    margin = round(travel_z - (top_z + standoff), 2)
    return {"clears": margin >= 0, "margin_mm": margin, "travel_z_needed": round(top_z + standoff, 2)}


# ---------- the check ----------
def newest_job(out_root, subject="person"):
    jobs = sorted((d for d in os.listdir(out_root) if d.endswith("-job")), reverse=True) if os.path.isdir(out_root) else []
    for d in jobs:
        p = os.path.join(out_root, d, "job.json")
        if os.path.isfile(p) and _j(p).get("subject") == subject:
            return d
    return None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--job")
    ap.add_argument("--standoff", type=float, default=None, help="mm (default: the job's design standoff, else 4)")
    ap.add_argument("--studio-dir", default=ROOT, help="Studio data folder (default: this project; the PC desktop "
                                                         "app keeps its own in HennaStudio-Windows-x64/app)")
    a = ap.parse_args(argv)
    sys.path.insert(0, ROOT)
    from henna_preview.depth_scale import correct_depth
    from henna_preview.hand_detect import LANDMARK_NAMES
    from henna_preview.recording_multi import read_frames_median
    from henna_preview.region import convex_hull, polygon_mask
    from step19_hand_path_avg import hand_top_from_raw
    from matplotlib.path import Path

    data = os.path.abspath(a.studio_dir)
    out_root = os.path.join(data, "outputs", "webui")
    job_id = a.job or newest_job(out_root)
    if not job_id:
        print("STOP: no real-hand job found - make one in Studio: New job -> Real hand, then Scan and Hand & region")
        return 1
    job = _j(os.path.join(out_root, job_id, "job.json"))
    steps = job.get("steps", {})
    if "scan" not in steps or "hand" not in steps:
        print(f"STOP: job {job_id} has no scan + hand detection yet")
        return 1
    if job.get("subject") != "person":
        print(f"NOTE: job {job_id} is marked '{job.get('subject_text')}', not a real hand")
    cal = _j(os.path.join(data, "current_calibration.json"))
    zc, ds = _j(os.path.join(data, cal["z_calibration"])), _j(os.path.join(data, cal["depth_scale"]))
    k, g, d_ref = float(zc["result"]["k_mm"]), float(ds["g"]), float(ds["d_ref_mm"])
    depth_fn = lambda d: correct_depth(d, g, d_ref)  # noqa: E731
    travel_z = float(cal["travel_z"])
    standoff = a.standoff if a.standoff is not None else float(steps.get("design", {}).get("standoff_mm", 4.0))
    head_z = float(steps["scan"]["mpos"][2])

    run_dir = os.path.join(out_root, steps["hand"]["run"].split(":", 1)[1])
    rep = _j(os.path.join(run_dir, "report.json"))
    rec = rep["input"]["path"]
    if not os.path.isfile(rec):                                   # moved to another computer: same name in Documents
        rec = os.path.join(os.path.expanduser("~"), "Documents", os.path.basename(rec))
    fi, nf = int(rep["input"]["frame_index"]), int(cal["frames"])
    print(f"Job {job_id} ({job.get('subject_text')}): reading {nf} frames around frame {fi} of {os.path.basename(rec)} ...")
    fr = read_frames_median(rec, fi - nf // 2, nf, cal["min_valid_frac"], cal["max_mad_mm"])
    with np.load(os.path.join(run_dir, "frame_bundle.npz")) as b:
        landmarks = b["landmarks_px"]
    raw, scale = fr["depth_raw_aligned"], fr["depth_scale_m_per_unit"]

    top = hand_top_from_raw(raw, scale, landmarks, k, head_z, depth_fn)        # exactly Studio's number
    hull = np.array(convex_hull(landmarks))
    c = hull.mean(0)
    grown = c + (hull - c) * (1 + 25 / max(1.0, np.linalg.norm(hull - c, axis=1).mean()))
    h, w = raw.shape
    v_, u_ = np.mgrid[0:h, 0:w]
    pix = np.column_stack([u_.ravel(), v_.ravel()])
    inside = Path(grown).contains_points(pix).reshape(h, w)
    in_hand = Path(hull).contains_points(pix).reshape(h, w)
    top_uv = top_location(raw, scale, inside)
    near_name, near_px = nearest(LANDMARK_NAMES, landmarks, top_uv)
    top_on_hand = bool(in_hand[int(top_uv[1]), int(top_uv[0])]) or near_px <= 20

    points = []
    for name, uv in zip(LANDMARK_NAMES, landmarks):
        d = point_depth_mm(raw, scale, uv)
        z = None if d is None else round(contact_z(d, k, head_z, depth_fn), 2)
        points.append({"name": name, "u": round(float(uv[0]), 1), "v": round(float(uv[1]), 1), "z": z,
                       "below_travel_mm": None if z is None else round(travel_z - z, 2)})
    region = None
    if "region" in steps:
        mask = polygon_mask(steps["region"]["polygon_px"], w, h)
        dd = raw[mask & (raw > 0)].astype(float) * scale * 1000.0
        if dd.size:
            region = {"highest_z": round(contact_z(float(np.percentile(dd, 0.5)), k, head_z, depth_fn), 2),
                      "lowest_z": round(contact_z(float(np.percentile(dd, 99.5)), k, head_z, depth_fn), 2)}
    v = verdict(top["hand_top_contact_z"], travel_z, standoff)
    result = {"job": job_id, "subject": job.get("subject"), "recording": os.path.basename(rec), "head_z": head_z,
              "travel_z": travel_z, "standoff_mm": standoff, "hand_top": top, "hand_top_px": [round(x, 1) for x in top_uv],
              "hand_top_nearest_point": near_name, "hand_top_distance_px": round(near_px, 1),
              "hand_top_on_the_hand": top_on_hand, "verdict": v, "region": region, "points": points,
              "motion_ready": False}

    out = os.path.join(data, "outputs", "realhand_checks", dt.datetime.now().strftime("%Y%m%d-%H%M%S"))
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "realhand_height.json"), "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(10, 6.4))
    ax.imshow(fr["colour_rgb"])
    ax.plot(*np.vstack([grown, grown[:1]]).T, color="#ffcc00", lw=1, ls="--", label="area Studio checks")
    for p in points:
        ax.plot(p["u"], p["v"], "o", ms=4, color="#00c0ff")
        if p["name"].endswith("_tip") or p["name"] in ("wrist", "thumb_ip", "middle_mcp"):
            ax.annotate(f"{p['name']}\n{p['z']}", (p["u"], p["v"]), fontsize=7, color="white",
                        xytext=(4, -4), textcoords="offset points")
    ax.plot(*top_uv, "x", ms=14, mew=3, color="red", label=f"highest point Z {top['hand_top_contact_z']}")
    ax.set_title(f"{'CLEARS' if v['clears'] else 'TOO TALL'}: hand top {top['hand_top_contact_z']} + standoff "
                 f"{standoff:g} vs travel Z {travel_z:g} (margin {v['margin_mm']} mm) - camera only", fontsize=10)
    ax.legend(loc="lower right", fontsize=8)
    ax.axis("off")
    fig.tight_layout()
    fig.savefig(os.path.join(out, "realhand_height.png"), dpi=110)

    print(f"\n=== REAL HAND HEIGHT ({'CLEARS' if v['clears'] else 'TOO TALL'}) ===")
    print(f"  hand top Z {top['hand_top_contact_z']} (Studio's number) near {near_name}"
          f"{'' if top_on_hand else ' - OUTSIDE the hand: something else is in the checked area'}")
    print(f"  needs: travel Z {travel_z:g} >= hand top + standoff {standoff:g} = {v['travel_z_needed']}"
          f"  -> margin {v['margin_mm']} mm")
    if region:
        print(f"  design region: highest Z {region['highest_z']}, lowest Z {region['lowest_z']}")
    tall = sorted((p for p in points if p["z"] is not None), key=lambda p: -p["z"])[:5]
    print("  highest hand points: " + ", ".join(f"{p['name']} {p['z']}" for p in tall))
    print(f"  picture + JSON: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
