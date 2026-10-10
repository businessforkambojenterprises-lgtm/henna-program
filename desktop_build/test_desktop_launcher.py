"""Launcher dispatch and occupied-port tests; never use project hardware."""
import contextlib
import http.server
import io
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

import henna_desktop as desktop


class WorkerDispatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = self.root / "app"
        self.app.mkdir()
        self.script = self.app / "step22_design_hover_test.py"
        self.script.write_text("import json, sys\nprint(json.dumps(sys.argv))\n", encoding="utf-8")

    def test_exact_bundled_worker_allowed(self):
        self.assertEqual(desktop.worker_path(self.app, self.script), self.script.resolve())

    def test_same_named_external_worker_refused(self):
        external = self.root / self.script.name
        external.write_text("raise AssertionError('must not run')", encoding="utf-8")
        with self.assertRaises(ValueError):
            desktop.worker_path(self.app, external)

    def test_non_worker_in_app_refused(self):
        arbitrary = self.app / "something_else.py"
        arbitrary.write_text("raise AssertionError('must not run')", encoding="utf-8")
        with self.assertRaises(ValueError):
            desktop.worker_path(self.app, arbitrary)

    def test_arguments_and_stdout_preserved(self):
        before = sys.argv
        output = io.StringIO()
        arguments = ["--config", "a file with spaces.json", "--write-gcode"]
        with contextlib.redirect_stdout(output):
            code = desktop.dispatch_worker(self.app, self.script, arguments)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue()), [str(self.script.resolve()), *arguments])
        self.assertIs(sys.argv, before)

    def test_worker_exit_status_preserved(self):
        self.script.write_text("raise SystemExit(7)\n", encoding="utf-8")
        before = sys.argv
        with self.assertRaises(SystemExit) as caught:
            desktop.dispatch_worker(self.app, self.script, [])
        self.assertEqual(caught.exception.code, 7)
        self.assertIs(sys.argv, before)

    def test_frozen_override_and_inherited_app_directory(self):
        with mock.patch.object(sys, "frozen", True, create=True):
            self.assertEqual(desktop.resolve_app_dir(str(self.app)), self.app.resolve())
            with mock.patch.dict(os.environ, {desktop.APP_DIR_ENV: str(self.app)}):
                self.assertEqual(desktop.resolve_app_dir(), self.app.resolve())

    def test_missing_asset_and_outside_asset_refused(self):
        with self.assertRaises(ValueError):
            desktop._asset(self.app, "missing.json")
        outside = self.root / "outside.json"
        outside.write_text("{}", encoding="utf-8")
        with self.assertRaises(ValueError):
            desktop._asset(self.app, "../outside.json")


class ServerTests(unittest.TestCase):
    def fake_modules(self, port):
        base = SimpleNamespace(HOST="127.0.0.1", PORT=port, ALLOWED_HOSTS=set(), http=SimpleNamespace(server=http.server))

        def main():
            server = base.http.server.ThreadingHTTPServer((base.HOST, base.PORT), http.server.BaseHTTPRequestHandler)
            try:
                server.serve_forever()
            finally:
                server.server_close()

        return base, SimpleNamespace(main=main)

    def test_busy_port_refused_without_opening_browser(self):
        with socket.socket() as occupied:
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                occupied.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            occupied.bind(("127.0.0.1", 0))
            occupied.listen(1)
            base, studio = self.fake_modules(occupied.getsockname()[1])
            original_server = http.server.ThreadingHTTPServer
            with mock.patch.object(desktop.importlib, "import_module", side_effect=[base, studio]):
                with mock.patch.object(desktop.webbrowser, "open") as browser:
                    with self.assertRaisesRegex(RuntimeError, "already in use"):
                        desktop.serve()
                    browser.assert_not_called()
            self.assertIs(http.server.ThreadingHTTPServer, original_server)

    def test_no_browser_and_port_override(self):
        base, studio = self.fake_modules(8765)
        with mock.patch.object(http.server.ThreadingHTTPServer, "serve_forever", return_value=None):
            with mock.patch.object(desktop.importlib, "import_module", side_effect=[base, studio]):
                with mock.patch.object(desktop.webbrowser, "open") as browser:
                    self.assertEqual(desktop.serve(no_browser=True, port=0), 0)
                    browser.assert_not_called()
        self.assertEqual(base.PORT, 0)
        self.assertEqual(base.ALLOWED_HOSTS, {"127.0.0.1:0", "localhost:0"})

    def test_closing_the_window_shuts_the_server_down(self):
        base, studio = self.fake_modules(0)
        with mock.patch.object(desktop.importlib, "import_module", side_effect=[base, studio]):
            with mock.patch.object(desktop, "open_window", return_value=True) as window:
                with mock.patch.object(desktop.webbrowser, "open") as browser:
                    self.assertEqual(desktop.serve(port=0), 0)      # returns only after the server stopped
        window.assert_called_once()
        browser.assert_not_called()

    def test_console_is_not_hidden_when_started_from_a_command_prompt(self):
        with mock.patch.object(desktop.os, "name", "posix"):
            self.assertFalse(desktop.console_is_ours())


if __name__ == "__main__":
    unittest.main()
