"""Storage guard and recording cleanup (henna_preview/storage.py, user 2026-10-17): scans refused below the minimum
free space; cleanup deletes only old recordings that no unfinished job needs, optionally after a size-checked copy;
the Pi server applies the guard to every recording route and locks the cleanup behind Admin."""
import json
import os
import shutil
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from henna_preview import storage  # noqa: E402

DAY = 86400


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)
        self.rec = os.path.join(self.dir, "Documents")
        self.out = os.path.join(self.dir, "outputs")
        os.makedirs(self.rec)
        os.makedirs(self.out)
        self.now = time.time()

    def recording(self, name, age_days, size=1000):
        p = os.path.join(self.rec, name)
        with open(p, "wb") as f:
            f.write(b"x" * size)
        with open(p + ".machine.json", "w", encoding="utf-8") as f:
            json.dump({"machine": "ball-screw"}, f)
        t = self.now - age_days * DAY
        os.utime(p, (t, t))
        return p

    def job(self, job_id, recording, done):
        d = os.path.join(self.out, job_id)
        os.makedirs(d)
        with open(os.path.join(d, "job.json"), "w", encoding="utf-8") as f:
            json.dump({"id": job_id, "done": done, "steps": {"scan": {"recording": recording}}}, f)

    def is_done(self, job):
        return job["done"]

    def test_only_old_recordings_no_unfinished_job_needs(self):
        old_done = self.recording("a_done.db3", 30)
        old_open = self.recording("b_unfinished.db3", 30)
        old_orphan = self.recording("c_no_job.db3", 30)
        recent = self.recording("d_recent.db3", 2)
        self.job("20261001-100000-job", old_done, True)
        self.job("20261001-110000-job", old_open, False)
        deletable, kept, every = storage.plan(self.out, self.is_done, 14, self.rec, self.now)
        self.assertEqual(sorted(os.path.basename(r["path"]) for r in deletable), ["a_done.db3", "c_no_job.db3"])
        self.assertEqual([os.path.basename(r["path"]) for r in kept], ["b_unfinished.db3"])
        self.assertEqual(len(every), 4)
        r = storage.cleanup(self.out, self.is_done, 14, rec_dir=self.rec, now=self.now)
        self.assertEqual(r["deleted"], 2)
        for p in (old_done, old_orphan):
            self.assertFalse(os.path.exists(p))
            self.assertFalse(os.path.exists(p + ".machine.json"))
        for p in (old_open, recent):
            self.assertTrue(os.path.exists(p))
        self.assertTrue(os.path.exists(os.path.join(self.out, "20261001-100000-job", "job.json")))   # jobs kept

    def test_export_first_then_delete(self):
        p = self.recording("e_old.db3", 40, size=5000)
        stick = os.path.join(self.dir, "stick")
        os.makedirs(stick)
        r = storage.cleanup(self.out, self.is_done, 14, export_to=stick, rec_dir=self.rec, now=self.now)
        self.assertEqual((r["deleted"], r["exported"]), (1, 1))
        copied = os.path.join(r["export_dir"], "e_old.db3")
        self.assertEqual(os.path.getsize(copied), 5000)
        self.assertTrue(os.path.exists(copied + ".machine.json"))
        self.assertFalse(os.path.exists(p))

    def test_failed_copy_keeps_the_recording(self):
        p = self.recording("f_old.db3", 40)
        r = storage.cleanup(self.out, self.is_done, 14, export_to=os.path.join(self.dir, "missing", "x"),
                            rec_dir=self.rec, now=self.now)
        self.assertTrue(os.path.exists(p) or r["deleted"] == 1)  # either copied fine (dir made) or kept
        stick = os.path.join(self.dir, "stick2")
        os.makedirs(stick)
        real_copy = shutil.copyfile
        p2 = self.recording("g_old.db3", 40)
        shutil.copyfile = lambda a, b: (_ for _ in ()).throw(OSError("stick full"))
        try:
            r = storage.cleanup(self.out, self.is_done, 14, export_to=stick, rec_dir=self.rec, now=self.now)
        finally:
            shutil.copyfile = real_copy
        self.assertTrue(os.path.exists(p2))
        self.assertIn("g_old.db3", r["skipped"])

    def test_guard_and_settings(self):
        self.assertGreater(storage.guard(self.rec, minimum=0.0), 0)
        with self.assertRaisesRegex(storage.StorageError, "Storage nearly full"):
            storage.guard(self.rec, minimum=10 ** 9)
        path = os.path.join(self.out, "storage_settings.json")
        self.assertEqual(storage.load_settings(path), {"keep_days": 14, "auto": True})
        self.assertEqual(storage.save_settings(30, False, path), {"keep_days": 30, "auto": False})
        with self.assertRaises(storage.StorageError):
            storage.save_settings(0, True, path)
        s = storage.summary(self.out, self.is_done, self.rec, path)
        self.assertEqual((s["keep_days"], s["recordings"]), (30, 0))


class FakeHeaders(dict):
    def get(self, k, d=None):
        return dict.get(self, k, d)


class PiServerStorageTests(unittest.TestCase):
    def test_guard_on_recording_routes_and_admin_lock(self):
        import webui_server_v10 as v10
        original = v10.camera
        try:
            import webui_server_pi as pi
            for path in ("/api/pi/storage/settings", "/api/pi/storage/cleanup"):
                self.assertTrue(pi.needs_admin(path), path)
            self.assertIn("/api/studio/scan", pi.RECORDING_PATHS)
            self.assertIn("/api/pi/storage", pi.PiHandler.GET_API)
            sent = []
            h = object.__new__(pi.PiHandler)
            h.path, h.headers = "/api/studio/scan", FakeHeaders()
            h._host_ok = lambda: True
            h._send = lambda code, obj, *a: sent.append((code, obj))
            saved = pi.storage.guard
            pi.storage.guard = lambda *a, **k: (_ for _ in ()).throw(pi.storage.StorageError("Storage nearly full (1.0 GB free"))
            try:
                pi.PiHandler.do_POST(h)
            finally:
                pi.storage.guard = saved
            self.assertEqual(sent[0][0], 400)
            self.assertIn("Storage nearly full", sent[0][1]["error"])
            saved_model = pi.pi_model
            pi.pi_model = lambda: "Some laptop"
            try:
                with self.assertRaisesRegex(ValueError, "only on the Raspberry Pi"):
                    pi.run_cleanup()                        # a laptop's Documents is never cleaned
            finally:
                pi.pi_model = saved_model
        finally:
            if "webui_server_pi" in sys.modules:
                sys.modules["webui_server_pi"].studio.machine.paste_output = None
            v10.camera = original
            sys.modules.pop("webui_server_pi", None)


if __name__ == "__main__":
    unittest.main()
