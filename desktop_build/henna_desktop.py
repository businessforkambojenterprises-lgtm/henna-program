"""Console launcher for the portable Henna Studio v15 desktop distribution.

The persistent ``app`` folder beside the executable contains the original
project sources and assets. Worker invocations stay in that folder; no device
is opened by the launcher or its offline self-test.
"""
from __future__ import annotations

import argparse
import errno
import importlib
import json
import os
from pathlib import Path
import runpy
import socket
import sys
import threading
import webbrowser


WORKER_SCRIPTS = frozenset({
    "step02_inspect_depth.py",
    "step04_hand_detect.py",
    "step21_design_preview.py",
    "step22_design_hover_test.py",
})
APP_DIR_ENV = "HENNA_STUDIO_APP_DIR"


def resolve_app_dir(override=None):
    frozen = bool(getattr(sys, "frozen", False))
    home = Path(sys.executable if frozen else __file__).resolve().parent
    selected = override if override is not None else os.environ.get(APP_DIR_ENV)
    app = Path(selected).resolve() if selected is not None else home / "app"
    app = app.resolve()
    if not app.is_dir():
        raise ValueError(f"The app folder is missing: {app}. Keep it beside HennaStudio.exe.")
    return app


def worker_path(app_dir, requested):
    """Allow only the named original worker files directly in the app folder."""
    app = Path(app_dir).resolve()
    candidate = Path(requested).resolve()
    if candidate.name not in WORKER_SCRIPTS or candidate.parent != app:
        raise ValueError("Only the four bundled Henna worker scripts may be launched.")
    if not candidate.is_file():
        raise ValueError(f"Bundled worker script is missing: {candidate.name}")
    return candidate


def dispatch_worker(app_dir, requested, arguments):
    script = worker_path(app_dir, requested)
    previous_argv = sys.argv
    sys.argv = [str(script), *arguments]
    try:
        runpy.run_path(str(script), run_name="__main__")
    finally:
        sys.argv = previous_argv
    return 0


def _asset(app, relative):
    candidate = (app / relative).resolve()
    if not candidate.is_relative_to(app) or not candidate.is_file():
        raise ValueError(f"Missing or invalid bundled asset: {relative}")
    return candidate


def self_test(app_dir):
    """Import native libraries and run the hand model without camera/serial I/O."""
    app = Path(app_dir).resolve()
    report = {"ok": False, "app_dir": str(app), "hardware_opened": False, "checks": {}}
    try:
        dependencies = {}
        for name in ("numpy", "cv2", "mediapipe", "pyrealsense2", "serial", "matplotlib"):
            module = importlib.import_module(name)
            dependencies[name] = str(getattr(module, "__version__", "imported"))
        report["checks"]["dependencies"] = dependencies
        config = json.loads(_asset(app, "current_calibration.json").read_text(encoding="utf-8"))
        calibration_assets = {}
        for key in ("xy_calibration", "z_calibration", "depth_scale"):
            path = _asset(app, config[key])
            json.loads(path.read_text(encoding="utf-8"))
            calibration_assets[key] = config[key]
        report["checks"]["calibration_assets"] = calibration_assets
        _asset(app, "webui/studio_v14.html")
        _asset(app, "webui_server_v14.py")
        _asset(app, "webui_server_v15.py")
        report["checks"]["web_assets"] = "present"
        hand = importlib.import_module("henna_preview.hand_detect")
        model = _asset(app, "models/hand_landmarker.task")
        report["checks"]["model_sha256"] = hand.verify_model(str(model))
        numpy = importlib.import_module("numpy")
        hands = hand.detect_hands(numpy.zeros((64, 64, 3), dtype=numpy.uint8), model_path=str(model))
        report["checks"]["native_model"] = {"blank_image_hands": len(hands)}
        report["ok"] = True
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
    print(json.dumps(report, indent=2), flush=True)
    return 0 if report["ok"] else 1


def serve(no_browser=False, port=None):
    base = importlib.import_module("webui_server")
    if port is not None:
        base.PORT = port
        base.ALLOWED_HOSTS = {f"127.0.0.1:{port}", f"localhost:{port}"}
    studio = importlib.import_module("webui_server_v15")
    original_server = base.http.server.ThreadingHTTPServer
    url = f"http://{base.HOST}:{base.PORT}"

    class DesktopServer(original_server):
        allow_reuse_address = False

        def server_bind(self):
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            super().server_bind()

        def serve_forever(self, poll_interval=0.5):
            # Construction has successfully bound and activated OUR listening
            # socket. No browser thread exists when the address was occupied.
            if not no_browser:
                def open_browser():
                    try:
                        if not webbrowser.open(url, new=2):
                            print(f"Open {url} in your browser.", flush=True)
                    except Exception as exc:
                        print(f"Could not open the browser ({exc}). Open {url} manually.", flush=True)
                threading.Thread(target=open_browser, name="henna-browser", daemon=True).start()
            return super().serve_forever(poll_interval=poll_interval)

    base.http.server.ThreadingHTTPServer = DesktopServer
    try:
        studio.main()
    except OSError as exc:
        if exc.errno in {errno.EADDRINUSE, 10048} or getattr(exc, "winerror", None) == 10048:
            raise RuntimeError(
                f"Port {base.PORT} is already in use. Close the other Henna Studio server "
                "or choose another port with --port. No existing application was stopped."
            ) from exc
        raise
    finally:
        base.http.server.ThreadingHTTPServer = original_server
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="Henna Studio v15 desktop launcher")
    parser.add_argument("--app-dir", help="Use this persistent project app folder")
    parser.add_argument("--no-browser", action="store_true", help="Start without opening a browser")
    parser.add_argument("--port", type=int, help="Local HTTP port (default: 8765)")
    parser.add_argument("--self-test", action="store_true", help="Offline dependencies/assets/model check")
    parser.add_argument("worker", nargs="?", help=argparse.SUPPRESS)
    parser.add_argument("worker_args", nargs=argparse.REMAINDER, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.port is not None and not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    try:
        app = resolve_app_dir(args.app_dir)
        # Capture relative worker paths before changing the working directory.
        worker = worker_path(app, args.worker) if args.worker else None
        sys.path.insert(0, str(app))
        os.chdir(app)
        # Original workers use sys.executable without launcher switches. Their
        # child process must inherit the same app directory selected here.
        os.environ[APP_DIR_ENV] = str(app)
    except Exception as exc:
        print(f"Henna Studio could not start: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        return 1
    if worker is not None:
        # Preserve the original worker's stdout, stderr, traceback and exit code.
        return dispatch_worker(app, worker, args.worker_args)
    try:
        if args.self_test:
            return self_test(app)
        return serve(no_browser=args.no_browser, port=args.port)
    except Exception as exc:
        print(f"Henna Studio could not start: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
