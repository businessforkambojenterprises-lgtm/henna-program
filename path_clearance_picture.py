"""Where is the tall point under the nozzle's path? A picture of Studio's path check for one job.

Runs the SAME measurement as Studio's design check (webui_server_v29.path_measure: scan, region, calibration) and
draws it on the scan's colour photo:
  green  = the design region            cyan  = the rest of the nozzle's path (corridor, 8 mm margin)
  white  = the scan position (left out) red X = the highest point Studio found on the path
Then prints where the tall parts of the path are, so you can see whether they are in the region itself or on the
way to it. Every point too tall for travel height is marked with a red dot.
It also compares the two frames Studio uses (2026-10-22: the region check said 165-186 mm while the path check
found points at about 144 mm inside the same outline): the region check reads the hand-detection frame, the path
check the 15-frame median of the scan recording - their recordings, frames and depth inside the outline are printed.
Camera data only - nothing here talks to the CNC or changes the job.

  python path_clearance_picture.py                       (the newest job with a region)
  python path_clearance_picture.py --job 20261018-084336-job
Writes outputs/path_checks/<time>/path_check.png + .json.
"""
import argparse
import datetime as dt
import glob
import json
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.abspath(__file__))


def newest_job_with_region(out_root):
    for p in sorted(glob.glob(os.path.join(out_root, "*-job", "job.json")), reverse=True):
        with open(p, encoding="utf-8") as f:
            s = json.load(f).get("steps", {})
        if s.get("region") and s.get("scan"):
            return os.path.basename(os.path.dirname(p))
    return None


def tall_spots(xy, z, mask, n=5, apart_mm=10.0):
    """The n highest points under mask, at least apart_mm from each other: [(x, y, z)]."""
    idx = np.nonzero(mask)[0]
    out = []
    for i in idx[np.argsort(-z[idx])]:
        if all(np.hypot(xy[i, 0] - a, xy[i, 1] - b) >= apart_mm for a, b, _ in out):
            out.append((round(float(xy[i, 0]), 1), round(float(xy[i, 1]), 1), round(float(z[i]), 1)))
            if len(out) == n:
                break
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--job")
    a = ap.parse_args(argv)
    sys.path.insert(0, ROOT)
    import webui_server_v29 as v29
    from henna_preview import path_clearance as pc

    out_root = v29.base.OUT_ROOT
    job_id = a.job or newest_job_with_region(out_root)
    if not job_id:
        print("STOP: no job with a scan and a region - do Scan and Hand & region in Studio first")
        return 1
    job = v29.v11.job_load(job_id)
    print(f"Job {job_id} ({job.get('subject')}): measuring the nozzle's path ...")
    try:
        m = v29.path_measure(job)
    except Exception as e:                                     # the same refusal Studio shows
        print(f"STOP: {e}")
        return 1
    r, xy, z, ok, stride = m["r"], m["xy"], m["z"], m["ok"], m["stride"]
    head = m["head"]
    c = v29.v10.cal()
    bed = v29.v27._paper_z(c, m["parts"]["k"])
    travel = float(c["travel_z"])

    far = np.hypot(*(xy - np.asarray(head[:2])).T) > pc.HEAD_FOOTPRINT_MM
    corridor = (pc.near_segments(xy, r["segments"], r["margin_mm"]) if r.get("segments")
                else pc.in_corridor(xy, np.asarray(r["corridor_xy"]), r["margin_mm"])) & far
    region = m["region_ok"]
    way = corridor & ~region
    above = (lambda zz: round(zz - bed, 1)) if bed is not None else (lambda zz: None)
    reg_xy = xy[region]
    too_tall = corridor & (z > travel - 9.0)
    sub = m["frame"]["depth_raw_aligned"][::stride, ::stride]
    depth_mm = sub[ok].astype(float) * float(m["frame"]["depth_scale_m_per_unit"]) * 1000.0
    hand = job["steps"].get("hand") or {}
    hand_input = {}
    try:
        with open(os.path.join(out_root, str(hand.get("run", "")).split(":", 1)[-1], "report.json"), encoding="utf-8") as f:
            hand_input = json.load(f).get("input", {})
    except (OSError, ValueError):
        pass
    frames = {
        "scan_recording": os.path.basename(m["recording"]),
        "scan_median_frames_around": m["frame"].get("frame_index"),
        "hand_recording": os.path.basename(str(hand_input.get("path", ""))) or None,
        "hand_frame": hand_input.get("frame_index"),
        "region_check_depth_mm": (job["steps"].get("region") or {}).get("depth_mm_range"),
        "path_check_depth_mm_in_region": [round(float(depth_mm[region].min()), 1), round(float(depth_mm[region].max()), 1)]
        if region.any() else None,
        "region_points": int(region.sum()), "region_points_too_tall": int((too_tall & region).sum()),
        "path_points_too_tall": int(too_tall.sum()),
    }
    vs0, us0 = np.nonzero(ok)
    order = np.nonzero(too_tall)[0][np.argsort(-z[too_tall])][:10]
    tallest = [{"u": int(us0[i] * stride), "v": int(vs0[i] * stride), "depth_mm": round(float(depth_mm[i]), 1),
                "z": round(float(z[i]), 2), "x": round(float(xy[i, 0]), 1), "y": round(float(xy[i, 1]), 1),
                "in_region": bool(region[i])} for i in order]
    result = {
        "job": job_id, "subject": job.get("subject"), "recording": os.path.basename(m["recording"]),
        "scan_position": head, "travel_z": travel, "bed_z": bed,
        "path_top_z": r["top_z"], "path_top_xy": r["top_xy"], "path_top_above_bed_mm": above(r["top_z"]),
        "region_xy_box": [[round(float(v), 1) for v in reg_xy.min(0)], [round(float(v), 1) for v in reg_xy.max(0)]],
        "region_top_z": round(float(np.percentile(z[region], pc.TOP_PERCENTILE)), 2),
        "tall_in_region": tall_spots(xy, z, region),
        "tall_on_the_way": tall_spots(xy, z, way),
        "path": {k: v for k, v in r.items() if k not in ("corridor_xy", "segments")},
        "travel_lines": r.get("segments"),
        "frames": frames, "tallest_points": tallest,
    }

    out = os.path.join(v29.base.ROOT, "outputs", "path_checks", dt.datetime.now().strftime("%Y%m%d-%H%M%S"))
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "path_check.json"), "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    vs, us = np.nonzero(ok)                                    # same order as xy / z
    us, vs = us * stride, vs * stride
    fig, ax = plt.subplots(figsize=(10, 6.4))
    ax.imshow(m["frame"]["colour_rgb"])
    ax.scatter(us[way], vs[way], s=5, c="#00b8ff", alpha=0.5, lw=0)
    ax.scatter(us[region], vs[region], s=5, c="#00ff66", alpha=0.4, lw=0)
    ax.plot([], [], "s", ms=8, color="#00b8ff", label="nozzle path (on the way)")
    ax.plot([], [], "s", ms=8, color="#00ff66", label="design region")
    if too_tall.any():
        ax.scatter(us[too_tall], vs[too_tall], s=10, c="red", lw=0)
        ax.plot([], [], "o", ms=5, color="red", label=f"too tall for travel Z ({int(too_tall.sum())} points)")
    i_head = int(np.argmin(np.hypot(*(xy - np.asarray(head[:2])).T)))
    ax.plot(us[i_head], vs[i_head], "o", ms=10, mfc="none", mec="white", mew=2, label="scan position (left out)")
    if r["top_xy"]:
        i_top = int(np.argmin(np.where(corridor, np.hypot(*(xy - np.asarray(r["top_xy"])).T), np.inf)))
        ax.plot(us[i_top], vs[i_top], "x", ms=12, mew=3, color="red",
                label=f"highest on path Z {r['top_z']:.1f} (X{r['top_xy'][0]:.0f} Y{r['top_xy'][1]:.0f})")
    for x_, y_, z_ in result["tall_on_the_way"][:3]:
        j = int(np.argmin(np.hypot(xy[:, 0] - x_, xy[:, 1] - y_)))
        ax.annotate(f"Z {z_}", (us[j], vs[j]), color="yellow", fontsize=8, xytext=(5, -5), textcoords="offset points")
    need = r["top_z"] + 9.0
    ax.set_title(f"Nozzle path top Z {r['top_z']:.1f}"
                 + (f" ({above(r['top_z'])} mm above bed)" if bed is not None else "")
                 + f" - travel Z {travel:g} needs it at Z {travel - 9:g} or lower"
                 + ("  OK" if need <= travel else "  TOO TALL"), fontsize=10)
    ax.legend(loc="lower right", fontsize=8)
    ax.axis("off")
    fig.tight_layout()
    fig.savefig(os.path.join(out, "path_check.png"), dpi=110)

    print(f"\n=== NOZZLE PATH ({'OK' if need <= travel else 'TOO TALL'}) ===")
    print(f"  scan position X{head[0]:g} Y{head[1]:g}; travel Z {travel:g}")
    print(f"  checked: {r['basis']}")
    if r.get("approach_candidates"):
        way = r.get("approach") or []
        print("  route to the first stroke: " + ("straight" if not way else
              "round tall skin via " + ", ".join(f"X{x:.0f} Y{y:.0f}" for x, y in way)) + "  (candidates: " +
              ", ".join(("straight" if not c["waypoints"] else "via X{:.0f} Y{:.0f}".format(*c["waypoints"][0]))
                        + f" top Z {c['top_z']}" for c in r["approach_candidates"]) + ")")
    print(f"  highest on the path: Z {r['top_z']:.1f}" + (f" at X{r['top_xy'][0]:.0f} Y{r['top_xy'][1]:.0f}" if r["top_xy"] else
                                                       " (edge of a patch without depth)"))
    b = result["region_xy_box"]
    print(f"  design region: X{b[0][0]:.0f}-{b[1][0]:.0f} Y{b[0][1]:.0f}-{b[1][1]:.0f}, its top Z {result['region_top_z']}")
    print("  tallest in the region:   " + ", ".join(f"X{x:.0f} Y{y:.0f} Z{zz}" for x, y, zz in result["tall_in_region"][:3]))
    print("  tallest on the way to it: " + ", ".join(f"X{x:.0f} Y{y:.0f} Z{zz}" for x, y, zz in result["tall_on_the_way"][:3]))
    f_ = frames
    print(f"  frames: scan {f_['scan_recording']} (median around {f_['scan_median_frames_around']}), "
          f"hand detection {f_['hand_recording']} frame {f_['hand_frame']}")
    print(f"  depth inside the outline: region check {f_['region_check_depth_mm']} mm, "
          f"path check {f_['path_check_depth_mm_in_region']} mm")
    print(f"  too tall: {f_['path_points_too_tall']} path points, {f_['region_points_too_tall']} of "
          f"{f_['region_points']} in the outline")
    for t in tallest[:5]:
        print(f"    pixel u{t['u']} v{t['v']}: depth {t['depth_mm']} mm -> Z {t['z']} at X{t['x']:.0f} Y{t['y']:.0f}"
              + (" (in the outline)" if t["in_region"] else ""))
    print(f"  picture + JSON: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
