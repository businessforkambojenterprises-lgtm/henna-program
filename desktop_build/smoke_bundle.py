"""Offline integration smoke checks for the portable Henna Studio v18 bundle.

All writable application data goes into a fresh copy at WORK/test_app. Never
opens a browser or calls a camera, serial, homing, program, or movement API.
The original project is not imported or modified. Uses the Python stdlib only.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
import traceback
from urllib.error import URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen


READ_PATHS = frozenset({
    "/", "/classic", "/api/studio/calibration", "/api/studio/designs",
    "/api/studio/jobs", "/api/studio/job",
})
WRITE_PATHS = frozenset({"/api/studio/job_new"})
WORKERS = (
    "step02_inspect_depth.py", "step04_hand_detect.py",
    "step21_design_preview.py", "step22_design_hover_test.py", "step23_design_hop_program.py",
)


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def app_fingerprint(root):
    """Detect even unintended log/cache writes in the released app folder."""
    result = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            result[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def load_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def json_from_stdout(text):
    decoder = json.JSONDecoder()
    for offset, char in enumerate(text):
        if char == "{":
            try:
                value, _ = decoder.raw_decode(text[offset:])
            except ValueError:
                continue
            if isinstance(value, dict) and "ok" in value and "app_dir" in value:
                return value
    raise AssertionError("Self-test did not print its JSON result; inspect self_test.log")


class Harness:
    def __init__(self, bundle, work, port):
        self.bundle = bundle.resolve()
        self.exe = self.bundle / "HennaStudio.exe"
        self.source_app = self.bundle / "app"
        self.work = work.resolve()
        self.app = self.work / "test_app"
        self.port = port
        self.url = f"http://127.0.0.1:{port}"
        self.env = os.environ.copy()
        self.env["HENNA_STUDIO_APP_DIR"] = str(self.app)
        self.env["PYTHONDONTWRITEBYTECODE"] = "1"
        self.proc = None
        self.server_log = None
        self.report = {
            "ok": False,
            "started_utc": datetime.now(timezone.utc).isoformat(),
            "bundle": str(self.bundle),
            "test_app": str(self.app),
            "hardware_endpoints_requested": False,
            "browser_opened": False,
            "checks": {},
            "requests": [],
        }

    @property
    def creationflags(self):
        return getattr(subprocess, "CREATE_NO_WINDOW", 0)

    def request(self, path, payload=None, timeout=10):
        method = "GET" if payload is None else "POST"
        route = urlsplit(path).path
        allowed = READ_PATHS if method == "GET" else WRITE_PATHS
        require(route in allowed, f"Endpoint outside offline smoke allowlist: {method} {route}")
        headers = {}
        data = None
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = Request(self.url + path, data=data, headers=headers, method=method)
        with urlopen(req, timeout=timeout) as response:
            body = response.read()
            kind = response.headers.get("Content-Type", "")
            require(response.status == 200, f"Unexpected HTTP response for {path}: {response.status}")
        self.report["requests"].append({"method": method, "path": path, "status": 200})
        if "application/json" in kind:
            return json.loads(body)
        return body.decode("utf-8")

    def command(self, name, args, timeout=120):
        result = subprocess.run(
            [str(self.exe), "--app-dir", str(self.app), *args],
            cwd=self.work, env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", timeout=timeout,
            creationflags=self.creationflags,
        )
        log = self.work / f"{name}.log"
        log.write_text(result.stdout + "\n--- stderr ---\n" + result.stderr, encoding="utf-8")
        require(result.returncode == 0, f"{name} exited {result.returncode}; inspect {log}")
        return result

    def assert_port_free(self):
        # Checking by binding does not send any HTTP or machine traffic.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as candidate:
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                candidate.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            candidate.bind(("127.0.0.1", self.port))

    def start(self, name):
        self.assert_port_free()
        self.server_log = (self.work / f"{name}.log").open("wb")
        self.proc = subprocess.Popen(
            [str(self.exe), "--app-dir", str(self.app), "--no-browser", "--port", str(self.port)],
            cwd=self.work, env=self.env, stdout=self.server_log, stderr=subprocess.STDOUT,
            creationflags=self.creationflags,
        )
        start = time.monotonic()
        deadline = start + 60
        last_error = None
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise AssertionError(f"Server exited {self.proc.returncode}; inspect {name}.log")
            try:
                calibration = self.request("/api/studio/calibration", timeout=2)
            except (URLError, TimeoutError, ConnectionError) as exc:
                last_error = str(exc)
                time.sleep(0.25)
                continue
            require(calibration == load_json(self.app / "current_calibration.json"),
                    "Live server calibration differs from copied application calibration")
            self.report["checks"][name] = {"ready_seconds": round(time.monotonic() - start, 3)}
            return calibration
        raise TimeoutError(f"Server not ready within 60 seconds: {last_error}")

    def stop(self):
        if self.proc is not None:
            if self.proc.poll() is None:
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
                    self.proc.wait(timeout=10)
            self.proc = None
        if self.server_log is not None:
            self.server_log.close()
            self.server_log = None

    def run(self):
        require(self.exe.is_file(), f"Bundle executable missing: {self.exe}")
        require(self.source_app.is_dir(), f"Bundle app folder missing: {self.source_app}")
        require(not self.work.is_relative_to(self.bundle), "Work directory must be outside release bundle")
        require(not self.app.exists(), "Use a fresh work directory; test_app already exists")
        self.work.mkdir(parents=True, exist_ok=True)
        before = app_fingerprint(self.source_app)
        require(bool(before), "Release app folder is empty")
        shutil.copytree(self.source_app, self.app)
        try:
            cal = load_json(self.app / "current_calibration.json")
            require(cal["capture_pose_mpos"] == [145.8, 42.6, -40.0], "Capture pose changed")
            require(cal["travel_z"] == -20.0, "Travel Z changed")
            require(cal["floor_above_skin_mm"] == 1.0, "Skin clearance floor changed")
            require(cal["draw_feed_mm_min"] == 300.0, "Draw feed changed")
            require(cal["travel_feed_mm_min"] == 1000.0, "Travel feed changed")
            require(cal["frames"] == 15, "Median frame count changed")
            for key in ("xy_calibration", "z_calibration", "depth_scale"):
                asset = (self.app / cal[key]).resolve()
                require(asset.is_relative_to(self.app), f"Calibration escapes app folder: {key}")
                load_json(asset)
            for name in ("webui/studio_v14.html", "webui/index_v10.html", "webui_server_v14.py",
                         "webui_server_v13.py", "henna_preview/grbl_program_link.py",
                         "webui_server_v15.py", "henna_preview/grbl_program_link_v15.py",
                         "webui_server_v16.py", "webui/studio_v16.html", "webui_server_v17.py",
                         "webui/studio_v17.html", "henna_preview/hop_path.py", "henna_preview/program_reread_v2.py",
                         "webui_server_v18.py", "webui/studio_v18.html", "henna_preview/stroke_font.py",
                         "models/hand_landmarker.task", *WORKERS):
                require((self.app / name).is_file(), f"Required app asset missing: {name}")
            self.report["checks"]["assets_and_fixed_rules"] = "passed"

            self_test = json_from_stdout(self.command("self_test", ["--self-test"]).stdout)
            require(self_test.get("ok") is True, "Frozen self-test did not pass")
            require(self_test.get("hardware_opened") is False, "Self-test hardware flag is not false")
            require(Path(self_test["app_dir"]).resolve() == self.app, "Self-test used the wrong app folder")
            self.report["checks"]["self_test"] = self_test

            worker_results = {}
            for worker in WORKERS:
                result = self.command(Path(worker).stem + "_help", [str(self.app / worker), "--help"])
                require("usage:" in result.stdout.lower(), f"{worker} help text missing")
                require(worker in result.stdout, f"{worker} did not reach its own argparse handler")
                worker_results[worker] = "help passed"
            self.report["checks"]["frozen_workers"] = worker_results

            self.start("server_first_start")
            studio = self.request("/")
            classic = self.request("/classic")
            require("Henna Studio" in studio, "Studio root page not served")
            require("<html" in classic.lower(), "Classic page not served")
            designs = self.request("/api/studio/designs")["designs"]
            require(bool(designs), "Design gallery is empty")
            for design in designs:
                require((self.app / design["svg"]).is_file(), f"Missing gallery SVG: {design['svg']}")
            jobs_before = self.request("/api/studio/jobs")["jobs"]
            require(jobs_before == [], "Fresh packaged app unexpectedly contains existing jobs")
            job = self.request("/api/studio/job_new", {"subject": "prosthetic"})
            require(job["schema"] == "studio_job/1", "Unexpected new-job schema")
            require(job["subject"] == "prosthetic", "New job subject mismatch")
            require(job["motion_ready"] is False, "motion_ready must remain false")
            require(job["steps"] == {} and job["status"] == "scan", "New job has unexpected work state")
            job_path = self.app / "outputs" / "webui" / job["id"] / "job.json"
            require(load_json(job_path) == job, "Job was not persisted in copied app folder")
            require(len(self.request("/api/studio/jobs")["jobs"]) == 1, "New job not listed")
            self.report["checks"]["pages_gallery_and_new_job"] = {
                "design_count": len(designs), "job_id": job["id"], "motion_ready": job["motion_ready"],
            }

            self.stop()
            self.start("server_restart")
            reloaded = self.request("/api/studio/job?" + urlencode({"id": job["id"]}))
            require(reloaded == job, "Job changed or disappeared after restarting executable")
            require(reloaded["motion_ready"] is False, "Restarted job motion_ready changed")
            require(len(self.request("/api/studio/jobs")["jobs"]) == 1, "Restarted jobs list mismatch")
            self.report["checks"]["job_persistence_after_restart"] = "passed"
        finally:
            self.stop()
            after = app_fingerprint(self.source_app)
            require(before == after, "Smoke checks modified the release app folder")
            self.report["checks"]["release_app_unchanged"] = True
        self.report["ok"] = True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", required=True, type=Path, help="Directory containing HennaStudio.exe and app/")
    parser.add_argument("--work", required=True, type=Path, help="Fresh scratch directory outside the bundle")
    parser.add_argument("--port", default=8876, type=int)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    harness = Harness(args.bundle, args.work, args.port)
    try:
        harness.run()
    except Exception as exc:
        harness.report["error"] = f"{type(exc).__name__}: {exc}"
        harness.report["traceback"] = traceback.format_exc()
    finally:
        harness.stop()
        harness.report["finished_utc"] = datetime.now(timezone.utc).isoformat()
        # Avoid creating a result inside a release bundle even for invalid input.
        if not harness.work.is_relative_to(harness.bundle):
            harness.work.mkdir(parents=True, exist_ok=True)
            (harness.work / "smoke_result.json").write_text(
                json.dumps(harness.report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(harness.report, indent=2))
    return 0 if harness.report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
