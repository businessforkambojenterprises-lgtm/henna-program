"""Hand watch for real-hand runs (Studio v28, user approval 2026-10-14: "go").

A real hand can move after the scan; the hover program assumes it does not. The camera rides on the head, so every
live depth pixel is turned into a MACHINE point through the approved calibration (the same model as the planner):
    tool_XY = head_XY + R @ camera_xy_mm + t        skin_Z = head_Z - (corrected_depth_mm - k)
and compared with the skin as scanned, cell by cell (1 mm grid) inside the design region:
  moved  - more than MOVED_SHARE of the seen cells are more than LIMIT_MM higher or lower than at the scan,
           or their median differs by more than LIMIT_MM
  near   - skin within NEAR_MM of the nozzle tip's own height, within NEAR_XY_MM of the tool (anywhere seen)
  unseen - less than MIN_SEEN of the region's cells visible (hand gone, covered, camera blind)
Pixels at or above the tip's own depth (d <= k) are the nozzle / dispenser itself and never count as skin.
Pure functions; the camera sample and the machine position come from the caller.
"""
import numpy as np

LIMIT_MM = 2.0
MOVED_SHARE = 0.20
NEAR_MM = 1.5
NEAR_XY_MM = 3.0            # skin right under the tip; the planner already lifts the tip over nearby higher skin
MIN_SEEN = 0.20
MIN_POINTS = 30
CELL_MM = 1.0


def ray_table(intr, shape, stride):
    """Unit-depth camera rays (x/z, y/z) for the sampled pixels, with the lens model (the SDK's deprojection)."""
    import pyrealsense2 as rs
    from henna_preview.region import to_rs_intrinsics
    ri = to_rs_intrinsics(intr)
    h, w = shape
    vs, us = np.mgrid[0:h, 0:w]
    rx, ry = np.empty((h, w)), np.empty((h, w))
    for v in range(h):
        for u in range(w):
            p = rs.rs2_deproject_pixel_to_point(ri, [float(u * stride), float(v * stride)], 1.0)
            rx[v, u], ry[v, u] = p[0], p[1]
    return rx, ry


def machine_points(depth_raw, scale, rays, head, R, t, k, depth_fn, mask=None):
    """(N, 3) machine X, Y, skin Z of the valid depth pixels below the tip's own depth (never the nozzle)."""
    from henna_preview.head_mask import head_pixels
    d_mm = depth_raw.astype(float) * float(scale) * 1000.0
    ok = (depth_raw > 0) & ~head_pixels(depth_raw, scale, k, depth_fn, grow_px=3)   # the tip itself reads ~k+1.4 mm
    if mask is not None:
        ok &= mask
    d = d_mm[ok]
    dc = np.asarray(depth_fn(d), float)
    keep = dc > k + 0.3                                   # at or above the tip plane = nozzle / dispenser
    d, dc = d[keep], dc[keep]
    rx, ry = rays[0][ok][keep], rays[1][ok][keep]
    cam = np.column_stack([rx * d, ry * d])               # camera mm (x, y) at the measured depth
    xy = np.asarray(head[:2], float) + cam @ np.asarray(R, float).T + np.asarray(t, float)
    z = float(head[2]) - (dc - k)
    return np.column_stack([xy, z])


class Reference:
    """The skin as scanned: median Z per 1 mm machine cell inside the design region."""

    def __init__(self, pts):
        if len(pts) < MIN_POINTS:
            raise ValueError("too little measured skin in the design region to watch the hand")
        self.x0, self.y0 = np.floor(pts[:, 0].min()), np.floor(pts[:, 1].min())
        ix, iy = self._cells(pts)
        self.nx, self.ny = int(ix.max()) + 1, int(iy.max()) + 1
        flat = ix * self.ny + iy
        order = np.argsort(flat)
        flat, z = flat[order], pts[order, 2]
        cells, start = np.unique(flat, return_index=True)
        self.z = np.full(self.nx * self.ny, np.nan)
        for c, s, e in zip(cells, start, list(start[1:]) + [len(flat)]):
            self.z[c] = float(np.median(z[s:e]))
        self.cells = int(np.isfinite(self.z).sum())

    def _cells(self, pts):
        return (np.floor((pts[:, 0] - self.x0) / CELL_MM).astype(int),
                np.floor((pts[:, 1] - self.y0) / CELL_MM).astype(int))

    def lookup(self, pts):
        ix, iy = self._cells(pts)
        inside = (ix >= 0) & (iy >= 0) & (ix < self.nx) & (iy < self.ny)
        z = np.full(len(pts), np.nan)
        z[inside] = self.z[ix[inside] * self.ny + iy[inside]]
        return z, np.where(inside, ix * self.ny + iy, -1)


def evaluate(ref, live_pts, tool_xyz):
    """-> {'verdict': ok|moved|near|unseen, numbers...} for one live picture."""
    out = {"seen_share": 0.0, "points": 0, "median_dz_mm": None, "share_off": None, "near_gap_mm": None}
    tool = np.asarray(tool_xyz, float)
    if len(live_pts):
        dxy = np.hypot(live_pts[:, 0] - tool[0], live_pts[:, 1] - tool[1])
        close = live_pts[dxy <= NEAR_XY_MM]
        if len(close) >= 5:
            gap = float(tool[2] - np.percentile(close[:, 2], 95))      # tip height above the highest skin there
            out["near_gap_mm"] = round(gap, 2)
            if gap < NEAR_MM:
                return dict(out, verdict="near")
    zr, cell = ref.lookup(live_pts) if len(live_pts) else (np.array([]), np.array([], int))
    m = np.isfinite(zr)
    out["points"] = int(m.sum())
    out["seen_share"] = round(len(np.unique(cell[m])) / max(1, ref.cells), 3)
    if out["points"] < MIN_POINTS or out["seen_share"] < MIN_SEEN:
        return dict(out, verdict="unseen")
    dz = live_pts[m, 2] - zr[m]
    out["median_dz_mm"] = round(float(np.median(dz)), 2)
    out["share_off"] = round(float(np.mean(np.abs(dz) > LIMIT_MM)), 3)
    if abs(out["median_dz_mm"]) > LIMIT_MM or out["share_off"] > MOVED_SHARE:
        return dict(out, verdict="moved")
    return dict(out, verdict="ok")


def message(res):
    v = res["verdict"]
    if v == "near":
        return f"the skin came within {res['near_gap_mm']:.1f} mm of the nozzle tip"
    if v == "moved":
        return (f"the hand moved since the scan (median {res['median_dz_mm']:+.1f} mm, "
                f"{100 * res['share_off']:.0f} % of the area more than {LIMIT_MM:g} mm off)")
    if v == "unseen":
        return f"the camera cannot see the hand area ({100 * res['seen_share']:.0f} % seen)"
    return "hand still"
