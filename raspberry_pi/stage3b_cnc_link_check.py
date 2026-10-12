"""Raspberry Pi port - stage 3b: is the CNC's position status fresh while the camera records? (NO MOTION)

On 2026-10-10 a Pi scan was refused: 'Position status became stale during recording' - Studio needs a new
machine status at least every 1.5 s while it records. Stage 3a (camera only) showed no stall in the Pi itself,
so this check adds the two things 3a left out:
  - the real CNC link: Studio v22's own link class (GrblProgramLinkV22) polling '?' 4 times a second, and
  - the real hand-over from the live preview to the recording (the page fetching a picture every 0.4 s).
It measures the age of the last machine status 20 times a second - the exact value Studio's 1.5 s check uses.

SAFETY: the serial port is wrapped so that ONLY '?' (the GRBL status query) can ever be sent - any other byte is
refused before it reaches the USB port. No homing, no jog, no program, no reset. The machine does not move.
It may report Alarm (not homed) - that is fine, '?' works in Alarm.

Phases: A link only (8 s) | B record, camera stopped before (x2) | C Studio's current preview 5 s -> record (x2)
        | D faster preview (web_recorder_fast) 5 s -> record (x2). Recordings are removed at the end.
Close Henna Studio first. CNC USB cable on the Pi, CNC power on, D405 on a blue port. Run:
   ~/henna-venv/bin/python ~/henna-program/raspberry_pi/stage3b_cnc_link_check.py      (about 2 minutes)
Send back the lines after '=== STAGE 3B RESULT'. Options: --port /dev/ttyUSB0, --keep-files.
"""
import datetime as dt
import json
import os
import shutil
import sys
import threading
import time
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

FRESH_S = 1.5                       # Studio's limit (grbl_scan_link.GrblScanLink fresh_s)
SAMPLE_S = 0.05
PAGE_FETCH_S = 0.4                  # the Studio page fetches a preview picture every 0.4 s (Pi log)
CH340 = (0x1A86, 0x7523)
STUDIO_URL = "http://127.0.0.1:8765/"


class StatusQueryOnly:
    """Serial port wrapper: write() passes only b'?' - anything else raises before reaching the port.
    read() is passed through and the arrival time of every non-empty read is kept (rx_times)."""

    def __init__(self, ser):
        self._ser = ser
        self.sent = 0
        self.rx_times = []

    def write(self, data):
        if bytes(data) != b"?":
            raise RuntimeError(f"stage 3b refuses to send {bytes(data)!r} - only '?' is allowed")
        self.sent += 1
        return self._ser.write(data)

    def read(self, count):
        data = self._ser.read(count)
        if data:
            self.rx_times.append(time.monotonic())
        return data

    def __getattr__(self, name):    # close, ... pass through
        return getattr(self._ser, name)


# ---------- pure helpers (unit-tested in tests/test_pi_check_stage3b.py) ----------
def find_ch340(ports):
    """[(device, vid, pid), ...] -> the one CH340 device, else None."""
    ch = [dev for dev, vid, pid in ports if (vid, pid) == CH340]
    return ch[0] if len(ch) == 1 else None


def _max_gap(times, t_start, t_end):
    """Longest time without an event inside [t_start, t_end] (window edges count as boundaries)."""
    pts = [t_start] + sorted(t for t in times if t_start <= t <= t_end) + [t_end]
    return round(max(b - a for a, b in zip(pts, pts[1:])), 3)


def age_stats(samples, t_start, t_end, t_ref, rx_times=()):
    """samples [(monotonic t, status age s or None)] -> worst age in [t_start, t_end], when (s after t_ref),
    time spent over Studio's limit, samples without any status, the longest pause of this Python process
    (python_max_gap_s - the sampler wakes every 0.05 s) and the longest time without bytes from the CNC."""
    win = [(t, a) for t, a in samples if t_start <= t <= t_end]
    ages = [(t, a) for t, a in win if a is not None]
    worst = max(ages, key=lambda x: x[1]) if ages else (None, None)
    return {"samples": len(win), "no_status_samples": len(win) - len(ages),
            "max_age_s": round(worst[1], 3) if worst[1] is not None else None,
            "worst_at_s": round(worst[0] - t_ref, 2) if worst[0] is not None else None,
            "time_over_limit_s": round(SAMPLE_S * sum(1 for _, a in ages if a > FRESH_S), 2),
            "python_max_gap_s": _max_gap([t for t, _ in samples], t_start, t_end) if win else None,
            "rx_max_gap_s": _max_gap(rx_times, t_start, t_end) if rx_times else None}


def cause(stats):
    """Which side was silent during the worst gap: the CNC's bytes (USB/controller) or Python itself."""
    if stats["max_age_s"] is None or stats["max_age_s"] <= FRESH_S:
        return ""
    if stats["python_max_gap_s"] is not None and stats["python_max_gap_s"] > 1.0:
        return f" -> Python itself paused {stats['python_max_gap_s']} s (camera library holding Python)"
    if stats["rx_max_gap_s"] is not None and stats["rx_max_gap_s"] > 1.0:
        return f" -> no bytes from the CNC for {stats['rx_max_gap_s']} s while Python ran (USB link or controller)"
    return " -> bytes arrived and Python ran: status lines were not usable"


def usb_controller(sys_path):
    """/sys/devices/.../xhci-hcd.1/usb3/3-1/... -> 'xhci-hcd.1' (the Pi 5 has two USB controllers)."""
    for part in sys_path.split("/"):
        if part.startswith("xhci-hcd"):
            return part
    return None


def usb_map(tty):
    """{'cnc': controller of the tty, 'camera': controller of the D405} from /sys (Linux only)."""
    out = {"cnc": None, "camera": None}
    try:
        out["cnc"] = usb_controller(os.path.realpath(f"/sys/class/tty/{os.path.basename(tty)}/device"))
        base = "/sys/bus/usb/devices"
        for d in os.listdir(base):
            try:
                with open(os.path.join(base, d, "idVendor")) as f1, open(os.path.join(base, d, "idProduct")) as f2:
                    if (f1.read().strip(), f2.read().strip()) == ("8086", "0b5b"):
                        out["camera"] = usb_controller(os.path.realpath(os.path.join(base, d)))
            except OSError:
                continue
    except OSError:
        pass
    return out


def grade(max_age):
    if max_age is None:
        return "NO ", "no machine status at all"
    if max_age <= 1.0:
        return "OK ", f"worst status age {max_age} s (limit {FRESH_S} s)"
    if max_age <= FRESH_S:
        return "NO ", f"worst status age {max_age} s - close to the {FRESH_S} s limit"
    return "NO ", f"worst status age {max_age} s - over the {FRESH_S} s limit: the refusal is reproduced"


# ---------- the check ----------
def studio_running():
    try:
        urllib.request.urlopen(STUDIO_URL, timeout=1)
        return True
    except Exception:
        return False


def list_ports():
    import serial.tools.list_ports as lp
    return [(p.device, p.vid, p.pid) for p in lp.comports()]


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    keep = "--keep-files" in argv
    port = argv[argv.index("--port") + 1] if "--port" in argv else None
    if studio_running():
        print("STOP: Henna Studio is running - close its window first (it holds the CNC port and the camera).")
        return 1
    ports = list_ports()
    port = port or find_ch340(ports)
    if not port:
        print(f"STOP: no single CH340 (CNC) port found. Ports: {ports}. Check the CNC USB cable and power, "
              "or give it with --port /dev/ttyUSB0")
        return 1
    import pyrealsense2 as rs
    if not rs.context().query_devices():
        print("STOP: no RealSense camera found - plug the D405 into a BLUE (USB 3) port")
        return 1

    from henna_preview.grbl_program_link_v22 import GrblProgramLinkV22
    from henna_preview.grbl_scan_link import _open_serial
    from henna_preview.web_recorder import CameraService
    from henna_preview.web_recorder_fast import FastCameraService

    def calibration():
        with open(os.path.join(REPO, "current_calibration.json"), encoding="utf-8") as f:
            return json.load(f)

    wrapped = []

    def opener(p):
        w = StatusQueryOnly(_open_serial(p))
        wrapped.append(w)
        return w

    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    work = os.path.join(os.path.expanduser("~"), "Documents", f"henna_pi_check_3b_{stamp}")
    os.makedirs(work, exist_ok=True)
    link = GrblProgramLinkV22(calibration_provider=calibration, opener=opener)
    samples, stop = [], threading.Event()

    def sampler():
        while not stop.is_set():
            with link._lock:
                t_status = link.t_status
            now = time.monotonic()
            samples.append((now, now - t_status if t_status else None))
            stop.wait(SAMPLE_S)

    r = {"time": dt.datetime.now().isoformat(timespec="seconds"), "port": port, "phases": {}}
    cam = None
    try:
        print(f"--- connecting to {port} (only '?' is sent - the machine does not move)")
        st = link.connect(port)
        time.sleep(2.0)
        st = link.status()
        r["machine"] = {"state": st["state"], "mpos": st["mpos"], "restarted_on_connect": st["restarted"]}
        r["usb"] = usb_map(port)
        print(json.dumps(r["machine"]), json.dumps(r["usb"]))
        threading.Thread(target=sampler, daemon=True).start()

        def rx():
            return wrapped[-1].rx_times if wrapped else []

        print("--- A: link only, 8 s")
        t0 = time.monotonic()
        time.sleep(8.0)
        r["phases"]["A_link_only"] = age_stats(samples, t0, time.monotonic(), t0, rx())

        def page(stop_page, c):      # the Studio page fetching preview pictures
            while not stop_page.is_set():
                c.jpeg()
                stop_page.wait(PAGE_FETCH_S)

        runs = [("B_record_from_stopped", CameraService, False), ("B_record_from_stopped_2", CameraService, False),
                ("C_studio_preview_then_record", CameraService, True), ("C_studio_preview_then_record_2", CameraService, True),
                ("D_fast_preview_then_record", FastCameraService, True), ("D_fast_preview_then_record_2", FastCameraService, True)]
        for name, cls, with_preview in runs:
            print(f"--- {name}")
            cam = cls()
            stop_page = threading.Event()
            res = {}
            t_phase = time.monotonic()
            if with_preview:
                cam.start_preview()
                threading.Thread(target=page, args=(stop_page, cam), daemon=True).start()
                c0, w0 = time.process_time(), time.monotonic()
                time.sleep(5.0)
                res["preview_cpu_percent"] = round(100 * (time.process_time() - c0) / (time.monotonic() - w0))
            t_press = time.monotonic()
            try:
                info = cam.record(os.path.join(work, f"{name}.db3"), 5.0)
                res.update(frame_pairs=info["frame_pairs"], depth_units_m=info["depth_units_m"])
            except Exception as e:
                res["error"] = f"{type(e).__name__}: {e}"
            t_done = time.monotonic()
            stop_page.set()
            cam.stop()
            res["record_s"] = round(t_done - t_press, 1)
            res["preview"] = age_stats(samples, t_phase, t_press, t_press, rx()) if with_preview else None
            res["recording"] = age_stats(samples, t_press, t_done, t_press, rx())
            r["phases"][name] = res
            print("    " + json.dumps(res))
            cam = None
            time.sleep(2.0)
        r["status_queries_sent"] = sum(w.sent for w in wrapped)
        st = link.status()
        r["machine_after"] = {"state": st["state"], "mpos": st["mpos"], "restarted": st["restarted"],
                              "messages": st["messages"]}
    finally:
        stop.set()
        if cam is not None:
            try:
                cam.stop()
            except Exception:
                pass
        try:
            link.disconnect()
        except Exception as e:
            print(f"Note: disconnect: {e}")
        out_json = os.path.join(os.path.expanduser("~"), f"henna_stage3b_check_{stamp}.json")
        with open(out_json, "w", encoding="utf-8") as f:
            json.dump(r, f, indent=2)
        if not keep:
            shutil.rmtree(work, ignore_errors=True)

    lines = [("OK " if r["phases"]["A_link_only"]["max_age_s"] is not None and
              r["phases"]["A_link_only"]["max_age_s"] <= 1.0 else "NO ", "A_link_only",
              grade(r["phases"]["A_link_only"]["max_age_s"])[1])]
    for name, res in r["phases"].items():
        if name == "A_link_only":
            continue
        flag, text = grade(res["recording"]["max_age_s"])
        extra = f" at +{res['recording']['worst_at_s']} s after Record{cause(res['recording'])}"
        if res.get("preview"):
            pf, pt = grade(res["preview"]["max_age_s"])
            flag = "NO " if "NO " in (flag, pf) else flag
            extra += (f"; during preview {res['preview']['max_age_s']} s{cause(res['preview'])}, "
                      f"preview CPU {res.get('preview_cpu_percent')} %")
        if "error" in res:
            flag, extra = "NO ", extra + f"; {res['error']}"
        lines.append((flag, name, text + extra + f"; {res.get('frame_pairs')} frame pairs"))
    print("\n=== STAGE 3B RESULT:", "PASS" if all(f == "OK " for f, _, _ in lines) else "CHECK", "===")
    m = r.get("machine", {})
    print(f"  port {port} | machine {m.get('state')} at {m.get('mpos')} | after: {r.get('machine_after', {}).get('state')} | "
          f"only '?' sent: {r.get('status_queries_sent')} times")
    u = r.get("usb", {})
    if u.get("cnc") and u.get("camera"):
        print(f"  USB: CNC on {u['cnc']}, camera on {u['camera']} -> "
              f"{'SAME USB controller' if u['cnc'] == u['camera'] else 'different USB controllers'}")
    for flag, name, text in lines:
        print(f"  {flag} {name}: {text}")
    if r.get("machine_after", {}).get("messages"):
        print(f"  controller messages: {r['machine_after']['messages']}")
    print(f"  full result: {out_json}")
    print(json.dumps(r))
    return 0


if __name__ == "__main__":
    sys.exit(main())
