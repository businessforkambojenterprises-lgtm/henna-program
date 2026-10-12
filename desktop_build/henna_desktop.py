"""Launcher for the portable Henna Studio v20 desktop distribution (own app window).

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
    "step23_design_hop_program.py",
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
        raise ValueError("Only the bundled Henna worker scripts may be launched.")
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
        _asset(app, "webui/studio_v16.html")
        _asset(app, "webui/studio_v17.html")
        _asset(app, "webui/studio_v18.html")
        _asset(app, "webui/studio_v19.html")
        _asset(app, "webui/studio_v20.html")
        _asset(app, "webui_server_v14.py")
        _asset(app, "webui_server_v15.py")
        _asset(app, "webui_server_v16.py")
        _asset(app, "webui_server_v17.py")
        _asset(app, "webui_server_v18.py")
        _asset(app, "webui_server_v19.py")
        _asset(app, "webui_server_v20.py")
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


WINDOW_TITLE = "Henna Studio"


def console_is_ours():
    """True when Windows created this console just for Studio (double-click), not a Command Prompt."""
    if os.name != "nt":
        return False
    try:
        import ctypes
        ids = (ctypes.c_uint32 * 8)()
        return ctypes.windll.kernel32.GetConsoleProcessList(ids, 8) == 1
    except Exception:
        return False


def hide_console(log_path):
    """Hide our own console and keep its messages in a log file."""
    import ctypes
    hwnd = ctypes.windll.kernel32.GetConsoleWindow()
    if hwnd:
        ctypes.windll.user32.ShowWindow(hwnd, 0)
    log = open(log_path, "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stderr = log
    return log


def show_error(message):
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, message, WINDOW_TITLE, 0x10 | 0x40000)
    except Exception:
        pass


def ask_close(message):
    """Windows Yes/No box from any thread; Yes closes."""
    try:
        import ctypes
        return ctypes.windll.user32.MessageBoxW(None, message, WINDOW_TITLE, 0x4 | 0x30 | 0x40000) == 6
    except Exception:
        return True


def open_window(url, studio, storage):
    """Native window (WebView2) until the user closes it. Returns False if no window could be made."""
    try:
        import webview
    except Exception as exc:
        print(f"App window not available ({exc}); using the browser instead.", flush=True)
        return False
    Path(storage).mkdir(parents=True, exist_ok=True)
    window = webview.create_window(WINDOW_TITLE, url, width=1400, height=900, min_size=(900, 640))

    def closing():
        try:
            busy = studio.v14.machine.status().get("busy")
        except Exception:
            busy = None
        if busy:
            return ask_close(f"The machine is busy ({busy}).\n\nClose Henna Studio and STOP the machine?")
        return True

    window.events.closing += closing
    webview.start(gui="edgechromium", private_mode=False, storage_path=str(storage))
    return True


def serve(no_browser=False, port=None, window=True):
    base = importlib.import_module("webui_server")
    if port is not None:
        base.PORT = port
        base.ALLOWED_HOSTS = {f"127.0.0.1:{port}", f"localhost:{port}"}
    studio = importlib.import_module("webui_server_v20")
    original_server = base.http.server.ThreadingHTTPServer
    url = f"http://{base.HOST}:{base.PORT}"
    use_window = window and not no_browser
    started, holder = threading.Event(), {}

    class DesktopServer(original_server):
        allow_reuse_address = False

        def server_bind(self):
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            super().server_bind()

        def serve_forever(self, poll_interval=0.5):
            # Construction has successfully bound and activated OUR listening
            # socket. No browser or window exists when the address was occupied.
            holder["server"] = self
            started.set()
            if not no_browser and not use_window:
                open_browser_later(url)
            return super().serve_forever(poll_interval=poll_interval)

    def port_error(exc):
        if isinstance(exc, OSError) and (exc.errno in {errno.EADDRINUSE, 10048} or getattr(exc, "winerror", None) == 10048):
            return RuntimeError(
                f"Port {base.PORT} is already in use. Close the other Henna Studio server "
                "or choose another port with --port. No existing application was stopped.")
        return None

    base.http.server.ThreadingHTTPServer = DesktopServer
    try:
        if not use_window:
            try:
                studio.main()
            except OSError as exc:
                raise (port_error(exc) or exc) from exc
            return 0
        errors = []

        def run_server():
            try:
                studio.main()
            except BaseException as exc:          # reported to the main thread
                errors.append(exc)
            finally:
                started.set()

        thread = threading.Thread(target=run_server, name="henna-server")
        thread.start()
        started.wait(120)
        if errors:
            raise (port_error(errors[0]) or errors[0]) from errors[0]
        if "server" not in holder:
            raise RuntimeError("Henna Studio did not start in time")
        storage = Path(os.getcwd()) / "outputs" / ".app_window"
        shown = False
        try:
            shown = open_window(url, studio, storage)
            if not shown:
                open_browser_later(url)
                thread.join()                       # browser mode: run until Ctrl+C / console closed
        finally:
            if shown:
                # Window closed: the studio's own shutdown stops a moving machine, disconnects, releases the camera.
                holder["server"].shutdown()
                thread.join()
        return 0
    finally:
        base.http.server.ThreadingHTTPServer = original_server


def open_browser_later(url):
    def open_browser():
        try:
            if not webbrowser.open(url, new=2):
                print(f"Open {url} in your browser.", flush=True)
        except Exception as exc:
            print(f"Could not open the browser ({exc}). Open {url} manually.", flush=True)
    threading.Thread(target=open_browser, name="henna-browser", daemon=True).start()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Henna Studio v20 desktop launcher")
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
        # Relative paths would point inside the app folder after chdir; the window library uses argv[0].
        sys.path[:] = [str(Path(p).resolve()) if p and not os.path.isabs(p) else p for p in sys.path]
        if sys.argv and sys.argv[0]:
            sys.argv[0] = str(Path(sys.argv[0]).resolve())
        sys.path.insert(0, str(app))
        os.chdir(app)
        # Original workers use sys.executable without launcher switches. Their
        # child process must inherit the same app directory selected here.
        os.environ[APP_DIR_ENV] = str(app)
    except Exception as exc:
        print(f"Henna Studio could not start: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        return 1
    hidden = None
    if worker is not None:
        # Preserve the original worker's stdout, stderr, traceback and exit code.
        return dispatch_worker(app, worker, args.worker_args)
    try:
        if args.self_test:
            return self_test(app)
        if not args.no_browser and console_is_ours():
            (app / "outputs").mkdir(parents=True, exist_ok=True)
            hidden = hide_console(app / "outputs" / "studio_console.log")
        return serve(no_browser=args.no_browser, port=args.port)
    except Exception as exc:
        print(f"Henna Studio could not start: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        if hidden:
            show_error(f"Henna Studio could not start:\n\n{exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
