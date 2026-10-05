"""Step 15: build a DRY RUN program (in the air, no hand, no dispensing) from the checked nozzle path.

Rebuilds the corrected plan-view path through all step-13 checks, lifts it by the user's
lift, checks every limit, re-reads the text independently, and writes the program plus
pictures and a report. This tool never connects to the machine; the user runs the file.
"""
import argparse
import datetime as dt
import json
import os

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

import step13_plan_sim as s13
from henna_preview import MOTION_READY
from henna_preview.dry_run import independent_check, lift_strokes, program
from step12_nozzle_sim import ROOT, _j


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--params", required=True)
    ap.add_argument("--out")
    a = ap.parse_args()
    dp = _j(a.params)
    cfg = _j(dp["source_config"])
    d = dp["design"]
    r = s13.build(cfg, d["shape"], d["diameter_mm"], d["centre_machine_xy"], d["rotation_deg"], d["jump_mm"])
    p = {k: dp[k] for k in ("lift_mm", "travel_z", "floor_z", "draw_feed_mm_min", "travel_feed_mm_min")}
    lifted = lift_strokes(r["strokes"], p)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    header = ["DRY RUN - NO HAND - NO DISPENSING - EMPTY BED ONLY",
              f"made {stamp} by step15_dry_run.py from {dp['source_config']}",
              f"20 mm circle at X{d['centre_machine_xy'][0]} Y{d['centre_machine_xy'][1]}, lifted {p['lift_mm']} mm above the checked nozzle path",
              f"floor Z {p['floor_z']} - travel Z {p['travel_z']} - feeds {p['draw_feed_mm_min']:.0f} / {p['travel_feed_mm_min']:.0f}",
              "Home the machine first - stand at the emergency stop"]
    lines = program(lifted, p, header)
    moves = independent_check(lines, p)
    out = a.out or os.path.join(ROOT, "outputs", "step15", stamp)
    os.makedirs(out, exist_ok=True)
    name = f"DRY_RUN_NO_HAND_{stamp}.gcode"
    with open(os.path.join(out, name), "w", encoding="ascii", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    allz = np.concatenate([s[:, 2] for s in lifted])
    xy = np.vstack([s[:, :2] for s in lifted])
    fig, ax = plt.subplots(1, 2, figsize=(11, 4.6))
    for s in lifted:
        ax[0].plot(s[:, 0], s[:, 1], "-", color="#3B6D11", lw=2)
        ax[0].plot(s[0, 0], s[0, 1], "o", color="#185FA5")
        ax[0].annotate("start", (s[0, 0], s[0, 1]), textcoords="offset points", xytext=(6, -10), fontsize=8)
    ax[0].set_aspect("equal"); ax[0].grid(alpha=.3); ax[0].set_title("Top view (machine X/Y)")
    ax[0].set_xlabel("X (mm)"); ax[0].set_ylabel("Y (mm)")
    t = np.arange(len(allz))
    ax[1].plot(t, allz, color="#3B6D11", label="nozzle Z during drawing (lifted)")
    ax[1].axhline(p["travel_z"], color="#888", ls="--", label=f"travel Z {p['travel_z']}")
    ax[1].axhline(p["floor_z"], color="#E24B4A", ls="--", label=f"floor Z {p['floor_z']}")
    ax[1].set_ylim(p["floor_z"] - 5, 2); ax[1].grid(alpha=.3); ax[1].legend(fontsize=8)
    ax[1].set_xlabel("point along path"); ax[1].set_ylabel("machine Z (mm)"); ax[1].set_title("Heights")
    fig.suptitle("DRY RUN - NO HAND - NO DISPENSING - EMPTY BED ONLY", color="#A32D2D", fontsize=10)
    fig.tight_layout(); fig.savefig(os.path.join(out, "dry_run_preview.png"), dpi=110); plt.close(fig)
    draw_len = float(sum(np.linalg.norm(np.diff(s, axis=0), axis=1).sum() for s in lifted))
    est_s = draw_len / p["draw_feed_mm_min"] * 60
    rep = {"schema": "nozzle_dry_run/1", "created": stamp, "status": "DRY RUN - NO HAND - NO DISPENSING",
           "motion_ready_for_hand": MOTION_READY, "program_file": name, "params": dp,
           "lines": len(lines), "drawing_moves": sum(m["kind"] == "draw" for m in moves),
           "z_range_drawing": [round(float(allz.min()), 3), round(float(allz.max()), 3)],
           "xy_range": [[round(float(xy[:, 0].min()), 2), round(float(xy[:, 0].max()), 2)],
                        [round(float(xy[:, 1].min()), 2), round(float(xy[:, 1].max()), 2)]],
           "drawing_length_mm": round(draw_len, 1), "estimated_drawing_time_s": round(est_s, 1),
           "independent_check": "passed: only G21/G90/G53 G0/G53 G1; every Z >= floor; sideways rapids only at travel Z; ends at travel Z",
           "not_covered": ["holder / Z-rail block clearance not modelled (bed must be empty)",
                           "acceleration and feed actually reached are set by the controller",
                           "this does not validate drawing on skin"]}
    json.dump(rep, open(os.path.join(out, "report.json"), "w", encoding="utf-8"), indent=2)
    print(json.dumps({k: rep[k] for k in ("program_file", "lines", "drawing_moves", "z_range_drawing", "xy_range",
                                          "drawing_length_mm", "estimated_drawing_time_s")}, indent=2))
    print("\n".join(lines[:12]))
    print("...")
    print("\n".join(lines[-3:]))
    print("Wrote:", out)


if __name__ == "__main__":
    main()
