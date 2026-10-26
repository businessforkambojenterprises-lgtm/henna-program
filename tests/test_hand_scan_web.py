"""The hand-scan web page (user 2026-10-26: "web ui"): the page, the live picture, Record -> Keep / Discard and the
saved list, with a stand-in camera (no D405 needed)."""
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import hand_scan_collect as collect  # noqa: E402
import hand_scan_web as web  # noqa: E402
from henna_preview import hand_scan_stats as hs  # noqa: E402
from test_hand_scan_stats import INTR, scene, H, W  # noqa: E402

import numpy as np  # noqa: E402


class FakeCam(web.Camera):
    def __init__(self, hand=True):
        import threading as th
        self.lock = th.Lock()
        self.units, self.intr, self.serial = 0.0001, dict(INTR, width=W, height=H), "TEST"
        self.pending, self.recording, self.hand = None, False, hand
        depth, self.lm = scene()
        self.raw = (depth * 10).astype(np.uint16)
        self.last = (self.raw, np.zeros((H, W, 3), np.uint8))

    def record(self, ident, side, note):
        m = hs.measure(self.raw.astype(float) * self.units * 1000.0, self.intr, self.lm) if self.hand else None
        res = {"id": ident, "side": side, "note": note, "found": self.hand,
               "lines": collect.summary_lines(m) if m else ["no hand found - record again"], "ok": bool(m and m["ok"])}
        hand = {"landmarks_px": self.lm.tolist(), "handedness_label": "Left", "handedness_score": 0.9} if self.hand else None
        self.pending = dict(res, depth_raw=self.raw, colour=self.last[1], hand=hand, m=m, jpeg=b"\xff\xd8jpeg")
        return res


class WebTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)
        self.saved_out, collect.OUT = collect.OUT, self.dir
        self.addCleanup(setattr, collect, "OUT", self.saved_out)
        web.Handler.cam = FakeCam()
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), web.Handler)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}"

    def get(self, p):
        with urllib.request.urlopen(self.base + p) as r:
            return r.status, r.headers["Content-Type"], r.read()

    def post(self, p, body=None):
        req = urllib.request.Request(self.base + p, json.dumps(body or {}).encode(), {"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_page_and_live_picture(self):
        s, t, body = self.get("/")
        self.assertEqual(s, 200)
        self.assertIn(b"Record (2 s)", body)
        s, t, body = self.get("/live.jpg")
        self.assertEqual((s, t), (200, "image/jpeg"))
        self.assertEqual(body[:2], b"\xff\xd8")

    def test_record_keep_and_the_list(self):
        s, r = self.post("/record", {"id": "P07", "side": "l", "note": "rings"})
        self.assertEqual(s, 200)
        self.assertTrue(r["found"])
        self.assertIn("scan OK", r["lines"])
        self.assertEqual(self.get("/shot.jpg")[0], 200)
        s, r = self.post("/keep")
        self.assertEqual((s, r["saved"]), (200, "P07_L"))
        self.assertTrue(os.path.isfile(os.path.join(self.dir, "P07_L.npz")))
        rows = json.loads(self.get("/list")[2])
        self.assertEqual([x["file"] for x in rows], ["P07_L.npz"])
        self.assertEqual(rows[0]["note"], "rings")
        self.assertEqual(self.post("/keep")[0], 400)               # nothing waiting any more

    def test_discard_and_bad_ids(self):
        self.post("/record", {"id": "P08", "side": "R"})
        self.post("/discard")
        self.assertEqual(self.post("/keep")[0], 400)
        self.assertEqual(os.listdir(self.dir), [])
        self.assertEqual(self.post("/record", {"id": "Harsh Kumar", "side": "R"})[0], 400)
        self.assertEqual(self.post("/record", {"id": "P09", "side": "X"})[0], 400)

    def test_no_hand_cannot_be_kept(self):
        web.Handler.cam = FakeCam(hand=False)
        s, r = self.post("/record", {"id": "P10", "side": "L"})
        self.assertFalse(r["found"])
        self.assertEqual(self.post("/keep")[0], 400)


if __name__ == "__main__":
    unittest.main()
