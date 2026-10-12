"""Raspberry Pi port - stage 3a speed check (camera only - never the CNC; the serial library is not even imported).

Finds out why a Studio scan on the Pi was refused with 'Position status became stale during recording'
(Studio needs a fresh machine status every 1.5 s while it records). Suspected cause: a scan writes about
56 MB/s (848x480 depth + colour, 30 fps, uncompressed) and an SD card cannot keep up, so the Pi stalls.

1. System: Pi model, memory, swap (on the SD card?), where ~/Documents lives (SD / NVMe / USB), power and
   temperature warnings (vcgencmd get_throttled).
2. Disk: sustained write speed of ~/Documents (512 MB, fsync) and of RAM (/dev/shm).
3. Live preview cost: Studio's current preview encoding vs a lookup-table version (same colours, half size).
4. Recordings with Studio's own recorder (record + repair + verify), while two dummy loops run every 0.25 s
   like Studio's status poller (one pure timer, one appending to a log file like the validation log):
   5 s to ~/Documents, 3 s to ~/Documents, 5 s to RAM. Longest loop gaps > 1.5 s = the refusal reproduced.
All test files are written to ~/Documents/henna_pi_check_<time> and /dev/shm and removed at the end.
Close Henna Studio first (one user per camera). Run:   ~/henna-venv/bin/python stage3a_pi_check.py
Send back the lines after '=== STAGE 3A RESULT'.  Options: --no-camera (steps 1-2 only), --keep-files.
"""
import datetime as dt
import json
import os
import platform
import shutil
import subprocess
import sys
import threading
import time
import urllib.request

import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

SCAN_MB_PER_S = 56.0          # measured: 280 MB per 5 s Studio scan
FRESH_S = 1.5                 # Studio's status freshness limit during recording (grbl_scan_link.py)
POLL_S = 0.25                 # Studio's status poll interval
STUDIO_URL = "http://127.0.0.1:8765/"
THROTTLE_BITS = {0: "under-voltage now", 1: "CPU speed capped now", 2: "throttled now", 3: "soft temperature limit now",
                 16: "under-voltage since boot", 17: "CPU speed capped since boot", 18: "throttled since boot",
                 19: "soft temperature limit since boot"}


# ---------- pure helpers (unit-tested in tests/test_pi_check_stage3a.py) ----------
def decode_throttled(value):
    """'throttled=0x50000' or int -> list of plain-language warnings (empty = all fine)."""
    if isinstance(value, str):
        value = int(value.strip().split("=")[-1], 16)
    return [text for bit, text in sorted(THROTTLE_BITS.items()) if value & (1 << bit)]


def classify_swaps(proc_swaps_text):
    """/proc/swaps text -> list of {"name", "kind", "size_mb", "used_mb"}; kind = zram | file | partition."""
    out = []
    for line in proc_swaps_text.strip().splitlines()[1:]:
        parts = line.split()
        if len(parts) < 4:
            continue
        name, typ, size_kb, used_kb = parts[0], parts[1], int(parts[2]), int(parts[3])
        kind = "zram" if "zram" in name else ("file" if typ == "file" else "partition")
        out.append({"name": name, "kind": kind, "size_mb": size_kb // 1024, "used_mb": used_kb // 1024})
    return out


def storage_kind(source):
    """Mount source like /dev/mmcblk0p2 -> 'SD card', /dev/nvme0n1p2 -> 'NVMe SSD', /dev/sda1 -> 'USB drive'."""
    s = source or ""
    if "mmcblk" in s:
        return "SD card"
    if "nvme" in s:
        return "NVMe SSD"
    if s.startswith("/dev/sd"):
        return "USB drive"
    if s in ("tmpfs", "shm") or "tmpfs" in s:
        return "RAM"
    return s or "unknown"


def gap_stats(times):
    """Tick timestamps (monotonic s) -> {"ticks", "max_gap_s", "gaps_over_fresh"}."""
    gaps = np.diff(np.asarray(times, float)) if len(times) > 1 else np.zeros(0)
    return {"ticks": len(times), "max_gap_s": round(float(gaps.max()), 3) if gaps.size else None,
            "gaps_over_fresh": int((gaps > FRESH_S).sum())}


def preview_lut():
    """65536 x 3 colour table identical to Studio's preview colouring (web_recorder._depth_rgb)."""
    from henna_preview.web_recorder import _depth_rgb
    return _depth_rgb(np.arange(65536, dtype=np.uint16).reshape(1, -1))[0]


def verdict(r):
    """Result dict -> list of (OK/NO, check, plain-language finding)."""
    out = []
    w = r.get("disk", {}).get("documents_write_mb_s")
    if w is not None:
        out.append(("OK " if w >= SCAN_MB_PER_S * 1.2 else "NO ", "documents_disk_speed",
                    f"{w} MB/s sustained; a 30 fps scan needs about {SCAN_MB_PER_S:g} MB/s"))
    swaps = r.get("system", {}).get("swaps", [])
    sd_swap = [s for s in swaps if s["kind"] != "zram"] if r.get("system", {}).get("root_storage") == "SD card" else []
    out.append(("NO " if sd_swap else "OK ", "no_swap_on_sd",
                f"swap on the SD card: {[s['name'] + ' ' + str(s['size_mb']) + ' MB' for s in sd_swap]}" if sd_swap
                else "no swap on the SD card"))
    warn = r.get("system", {}).get("throttled_warnings")
    if warn is not None:
        out.append(("NO " if warn else "OK ", "power_and_temperature", ", ".join(warn) if warn else "no warnings"))
    p = r.get("preview")
    if p:
        out.append(("OK " if p["current_ms"] < 33 else "NO ", "preview_encoding",
                    f"current {p['current_ms']} ms/frame, lookup table {p['lut_ms']} ms/frame (30 fps = 33 ms)"))
    for name, rec in r.get("recordings", {}).items():
        if "error" in rec:
            out.append(("NO ", f"recording_{name}", rec["error"]))
            continue
        st = rec["status_loop"]
        ok = st["gaps_over_fresh"] == 0 and abs((rec.get("depth_units_m") or 0) - 0.0001) < 1e-7
        out.append(("OK " if ok else "NO ", f"recording_{name}",
                    f"longest status gap {st['max_gap_s']} s ({st['gaps_over_fresh']} over {FRESH_S} s), "
                    f"log-write gap {rec['log_loop']['max_gap_s']} s, {rec['frame_pairs']} frame pairs, "
                    f"{rec['total_s']} s incl. repair+verify"))
    return out


# ---------- measurements ----------
def _run(cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _meminfo():
    vals = {}
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                k, v = line.split(":")
                vals[k] = int(v.split()[0]) // 1024
    except OSError:
        pass
    return vals


def _temp_c():
    t = _run(["vcgencmd", "measure_temp"])
    return t.split("=")[-1] if t else None


def system_info(docs):
    info = {"machine": platform.machine(), "python": platform.python_version()}
    try:
        with open("/proc/device-tree/model") as f:
            info["model"] = f.read().strip("\x00\n")
    except OSError:
        info["model"] = platform.platform()
    mem = _meminfo()
    info["ram_mb"], info["ram_available_mb"] = mem.get("MemTotal"), mem.get("MemAvailable")
    try:
        with open("/proc/swaps") as f:
            info["swaps"] = classify_swaps(f.read())
    except OSError:
        info["swaps"] = []
    info["root_storage"] = storage_kind(_run(["findmnt", "-n", "-o", "SOURCE", "/"]))
    info["documents_storage"] = storage_kind(_run(["findmnt", "-n", "-o", "SOURCE", "--target", docs]))
    info["documents_free_gb"] = round(shutil.disk_usage(docs).free / 1e9, 1)
    thr = _run(["vcgencmd", "get_throttled"])
    info["throttled"] = thr or None
    info["throttled_warnings"] = decode_throttled(thr) if thr else None
    info["temperature"] = _temp_c()
    try:   # Pi 5: 5000 = the 5 A supply was detected (otherwise USB devices are limited to 600 mA in total)
        with open("/proc/device-tree/chosen/power/max_current", "rb") as f:
            info["psu_max_current_ma"] = int.from_bytes(f.read()[:4], "big")
    except OSError:
        info["psu_max_current_ma"] = None
    info["usb_max_current_enable"] = _run(["vcgencmd", "get_config", "usb_max_current_enable"]) or None
    return info


def write_speed(folder, total_mb, chunk_mb=8):
    """Sustained write incl. fsync -> (MB/s, slowest single chunk in s). The file is removed again."""
    path = os.path.join(folder, "write_test.bin")
    chunk = np.random.default_rng(0).bytes(chunk_mb * 1024 * 1024)
    worst = 0.0
    t0 = time.perf_counter()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0))
    try:
        for _ in range(total_mb // chunk_mb):
            c0 = time.perf_counter()
            os.write(fd, chunk)
            worst = max(worst, time.perf_counter() - c0)
        os.fsync(fd)
    finally:
        os.close(fd)
    secs = time.perf_counter() - t0
    os.remove(path)
    return round(total_mb / secs, 1), round(worst, 3)


def preview_cost(seconds=4.0):
    """Mean ms per frame: Studio's preview encoding vs the lookup-table version, on live frames."""
    import cv2
    from henna_preview.web_recorder import CameraService
    lut = preview_lut()
    svc = CameraService()
    pipe = svc._pipeline()
    cur, new = [], []
    try:
        t0 = time.time()
        while time.time() - t0 < seconds:
            ok, fs = pipe.try_wait_for_frames(2000)
            if not ok:
                continue
            c0 = time.perf_counter()
            svc._encode(fs)
            cur.append(time.perf_counter() - c0)
            c0 = time.perf_counter()
            d, c = fs.get_depth_frame(), fs.get_color_frame()
            rgb = np.asanyarray(c.get_data())[::2, ::2]
            dep = lut[np.asanyarray(d.get_data())[::2, ::2]]
            cv2.imencode(".jpg", np.hstack([rgb, dep])[:, :, ::-1], [cv2.IMWRITE_JPEG_QUALITY, 75])
            new.append(time.perf_counter() - c0)
    finally:
        pipe.stop()
    return {"frames": len(cur), "current_ms": round(1000 * float(np.mean(cur)), 1),
            "lut_ms": round(1000 * float(np.mean(new)), 1)}


def recording_test(folder, seconds, log_folder):
    """Studio's own recorder while two 0.25 s loops (status-poller stand-ins) and a memory sampler run."""
    from henna_preview.web_recorder import CameraService
    path = os.path.join(folder, f"check_{seconds:g}s_{dt.datetime.now():%H%M%S}.db3")
    log_path = os.path.join(log_folder, "loop_log.csv")
    stop = threading.Event()
    ticks, log_ticks, mem = [], [], []

    def status_loop():
        while not stop.is_set():
            ticks.append(time.monotonic())
            stop.wait(POLL_S)

    def log_loop():
        while not stop.is_set():
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"{time.time():.3f},tick\n")
            log_ticks.append(time.monotonic())
            stop.wait(POLL_S)

    def mem_loop():
        while not stop.is_set():
            m = _meminfo()
            if m:
                mem.append((m.get("MemAvailable", 0), m.get("SwapTotal", 0) - m.get("SwapFree", 0)))
            stop.wait(0.2)

    threads = [threading.Thread(target=f, daemon=True) for f in (status_loop, log_loop, mem_loop)]
    for t in threads:
        t.start()
    swap0 = _meminfo()
    t0 = time.monotonic()
    try:
        info = CameraService().record(path, seconds)
        err = None
    except Exception as e:      # reported, not raised: the other tests still run
        info, err = {}, f"{type(e).__name__}: {e}"
    total = time.monotonic() - t0
    stop.set()
    for t in threads:
        t.join(timeout=2)
    res = {"seconds": seconds, "total_s": round(total, 1), "status_loop": gap_stats(ticks),
           "log_loop": gap_stats(log_ticks)}
    if mem:
        res["min_ram_available_mb"] = min(a for a, _ in mem)
        res["max_swap_used_mb"] = max(s for _, s in mem)
        res["swap_used_before_mb"] = swap0.get("SwapTotal", 0) - swap0.get("SwapFree", 0)
    if err:
        res["error"] = err
    else:
        res.update(size_mb=round(os.path.getsize(path) / 1e6, 1), frame_pairs=info.get("frame_pairs"),
                   frames_seen=info.get("frames_seen_while_recording"), depth_units_m=info.get("depth_units_m"),
                   median_depth_mm=info.get("median_depth_mm"))
    return res


def studio_running():
    try:
        urllib.request.urlopen(STUDIO_URL, timeout=1)
        return True
    except Exception:
        return False


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    camera, keep = "--no-camera" not in argv, "--keep-files" in argv
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    docs = os.path.join(os.path.expanduser("~"), "Documents")
    work = os.path.join(docs, f"henna_pi_check_{stamp}")
    ram = f"/dev/shm/henna_pi_check_{stamp}" if os.path.isdir("/dev/shm") else None
    if camera and studio_running():
        print("STOP: Henna Studio is running - close its window first (one user per camera).")
        return 1
    os.makedirs(work, exist_ok=True)
    if ram:
        os.makedirs(ram, exist_ok=True)
    r = {"time": dt.datetime.now().isoformat(timespec="seconds")}
    try:
        print("--- 1/4 system")
        r["system"] = system_info(docs)
        print(json.dumps(r["system"]))
        if r["system"]["documents_free_gb"] < 2:
            print("STOP: less than 2 GB free in ~/Documents")
            return 1

        print("--- 2/4 disk write speed (about 1 minute on an SD card)")
        w, worst = write_speed(work, 512)
        r["disk"] = {"documents_write_mb_s": w, "documents_slowest_8mb_chunk_s": worst}
        if ram and (_meminfo().get("MemAvailable", 0) > 1500):
            r["disk"]["ram_write_mb_s"] = write_speed(ram, 256)[0]
        print(json.dumps(r["disk"]))

        if camera:
            import pyrealsense2 as rs
            devs = rs.context().query_devices()
            if not devs:
                print("STOP: no RealSense camera found - plug the D405 into a BLUE (USB 3) port")
                return 1
            d = devs[0]
            r["camera"] = {k: d.get_info(getattr(rs.camera_info, k)) for k in
                           ("serial_number", "firmware_version", "usb_type_descriptor")}
            print("--- 3/4 live preview cost (4 s)")
            r["preview"] = preview_cost()
            print(json.dumps(r["preview"]))
            print("--- 4/4 recordings with Studio's recorder (point the camera at something, keep still)")
            r["recordings"] = {}
            tests = [("documents_5s", work, 5.0), ("documents_3s", work, 3.0)]
            if ram and _meminfo().get("MemAvailable", 0) > 1200:
                tests.append(("ram_5s", ram, 5.0))
            else:
                r["recordings"]["ram_5s"] = {"error": "skipped - less than 1.2 GB RAM free"}
            for name, folder, secs in tests:
                print(f"    {name} ...")
                r["recordings"][name] = rec = recording_test(folder, secs, work)
                print("    " + json.dumps(rec))
                time.sleep(2)
        r["temperature_after"] = _temp_c()
        thr = _run(["vcgencmd", "get_throttled"])
        r["throttled_after"] = decode_throttled(thr) if thr else None
    finally:
        out_json = os.path.join(os.path.expanduser("~"), f"henna_stage3a_check_{stamp}.json")
        with open(out_json, "w", encoding="utf-8") as f:
            json.dump(r, f, indent=2)
        if not keep:
            shutil.rmtree(work, ignore_errors=True)
            if ram:
                shutil.rmtree(ram, ignore_errors=True)

    checks = verdict(r)
    print("\n=== STAGE 3A RESULT:", "PASS" if all(c[0] == "OK " for c in checks) else "CHECK", "===")
    s = r["system"]
    print(f"  {s.get('model')} | RAM {s.get('ram_mb')} MB | system on {s.get('root_storage')} | "
          f"Documents on {s.get('documents_storage')} | temp {s.get('temperature')} -> {r.get('temperature_after')} | "
          f"PSU max {s.get('psu_max_current_ma')} mA")
    for flag, name, text in checks:
        print(f"  {flag} {name}: {text}")
    if r.get("throttled_after"):
        print(f"  NO  after the tests: {', '.join(r['throttled_after'])}")
    print(f"  full result: {out_json}")
    print(json.dumps(r))
    return 0


if __name__ == "__main__":
    sys.exit(main())
