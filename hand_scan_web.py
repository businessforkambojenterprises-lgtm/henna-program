"""Hand scans with the D405 on a stand - as a web page (user 2026-10-26: "web ui" for hand_scan_collect.py).

  .venv\\Scripts\\python.exe hand_scan_web.py        then open http://127.0.0.1:8770 (the starter opens it)

Close Henna Studio first (one program at a time can use the camera). The page: live camera (colour + depth), ID (no
names), L / R, a note, Record (2 s, averaged), the measurements with warnings, Keep / Record again / Discard, and the
saved scans. Saving and measuring are hand_scan_collect.py's (outputs/hand_scans/<ID>_<L|R>[_n].npz + index.csv).
Only this computer can open it (127.0.0.1).
"""
import csv
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse


ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
import hand_scan_collect as collect  # noqa: E402

PORT = 8770
HOST = "127.0.0.1"


class Camera:
    def __init__(self):
        self.lock = threading.Lock()
        self.pipe, self.align, self.units, self.intr, self.serial = collect.open_camera()
        self.last = None                                  # (depth, colour)
        self.recording = False
        self.pending = None                               # the last recorded scan, waiting for Keep / Discard
        self.stop = threading.Event()
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while not self.stop.is_set():
            if self.recording:
                time.sleep(0.05)
                continue
            try:
                d, c = collect.grab(self.pipe, self.align)
            except RuntimeError:
                continue
            with self.lock:
                self.last = (d, c)

    def jpeg(self):
        import cv2
        with self.lock:
            last = self.last
        if last is None:
            return None
        img = collect.preview(last[0], last[1], self.units, [])
        img = cv2.resize(img, (img.shape[1] // 2, img.shape[0] // 2))
        return cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 75])[1].tobytes()

    def record(self, ident, side, note):
        from henna_preview import hand_detect, hand_scan_stats
        self.recording = True
        try:
            depth_raw, colour = collect.record(self.pipe, self.align)
        finally:
            self.recording = False
        hands = hand_detect.detect_hands(colour)
        res = {"id": ident, "side": side, "note": note, "found": bool(hands)}
        m, hand = None, None
        if hands:
            hand = hands[0]
            try:
                m = hand_scan_stats.measure(depth_raw.astype(float) * self.units * 1000.0, self.intr, hand["landmarks_px"])
                res.update(lines=collect.summary_lines(m), ok=m["ok"])
            except ValueError as e:
                res.update(lines=[f"cannot measure: {e}"], ok=False)
        else:
            res.update(lines=["no hand found - record again"], ok=False)
        import cv2
        img = collect.preview(depth_raw, colour, self.units, [])
        if hand:
            for u, v in hand["landmarks_px"]:
                cv2.circle(img, (int(u), int(v)), 4, (0, 255, 255), -1)
        img = cv2.resize(img, (img.shape[1] // 2, img.shape[0] // 2))
        with self.lock:
            self.pending = dict(res, depth_raw=depth_raw, colour=colour, hand=hand, m=m,
                                jpeg=cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 80])[1].tobytes())
        return res

    def keep(self):
        with self.lock:
            p, self.pending = self.pending, None
        if not p or p["m"] is None:
            raise ValueError("nothing to keep - record a scan with a hand first")
        name = collect.free_name(p["id"], p["side"])
        collect.save(name, p["id"], p["side"], p["note"], p["depth_raw"], p["colour"], self.units, self.intr,
                     self.serial, p["hand"], p["m"])
        return name

    def discard(self):
        with self.lock:
            self.pending = None


def saved_list():
    index = os.path.join(collect.OUT, "index.csv")
    if not os.path.isfile(index):
        return []
    with open(index, encoding="utf-8") as f:
        return list(csv.DictReader(f))[::-1]


PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Hand scans</title><style>
:root{--bg:#1b1a18;--panel:#24231f;--line:#3a3833;--text:#ece9e2;--muted:#a9a49a;--accent:#e79b6a;--ok:#7fcf7f;--bad:#ef7f7f}
body{margin:0;background:var(--bg);color:var(--text);font:15px/1.45 system-ui,Segoe UI,sans-serif}
main{max-width:1150px;margin:0 auto;padding:18px;display:grid;grid-template-columns:minmax(0,1.6fr) minmax(0,1fr);gap:16px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:16px}
h1{font-size:20px;margin:0 0 4px}h2{font-size:16px;margin:0 0 10px}.sub{color:var(--muted);font-size:13px;margin:0 0 12px}
img{width:100%;border-radius:10px;background:#000;display:block}
label{display:block;font-size:12px;color:var(--muted);margin:10px 0 4px;text-transform:uppercase}
input,select{width:100%;box-sizing:border-box;padding:9px 11px;border-radius:10px;border:1px solid var(--line);background:#1b1a18;color:var(--text);font:inherit}
.row{display:flex;gap:8px}.row>*{flex:1}
button{padding:11px 14px;border-radius:10px;border:1px solid var(--line);background:#2d2b27;color:var(--text);font:inherit;font-weight:650;cursor:pointer}
button.primary{background:var(--accent);border-color:var(--accent);color:#1b1a18}button:disabled{opacity:.45;cursor:default}
.msg{margin-top:10px;font-size:14px}.ok{color:var(--ok)}.bad{color:var(--bad)}
table{width:100%;border-collapse:collapse;font-size:13px}td,th{padding:5px 6px;border-bottom:1px solid var(--line);text-align:left}
@media(max-width:820px){main{grid-template-columns:1fr}}
</style></head><body><main>
<div class="card"><h1>Hand scans</h1><p class="sub">D405 on the stand, straight down, about 20-22 cm above the table. Hand palm up, still.
Ask each person first; scans stay on this laptop.</p><img id="cam" alt="Live camera: colour and depth">
<div id="result" hidden><h2 style="margin-top:14px">Result</h2><img id="shot" alt="The recorded scan"><div id="lines" class="msg"></div>
<div class="row" style="margin-top:10px"><button class="primary" id="keep">Keep</button><button id="again">Record again</button><button id="drop">Discard</button></div></div></div>
<div><div class="card"><h2>Next hand</h2>
<label for="id">ID (no names)</label><input id="id" placeholder="e.g. P07" maxlength="20">
<div class="row"><div><label for="side">Hand</label><select id="side"><option value="L">Left</option><option value="R">Right</option></select></div></div>
<label for="note">Note (optional)</label><input id="note" placeholder="e.g. rings, curled fingers" maxlength="120">
<button class="primary" id="rec" style="width:100%;margin-top:14px">Record (2 s)</button><div id="msg" class="msg"></div></div>
<div class="card" style="margin-top:16px"><h2>Saved scans (<span id="count">0</span>)</h2><table><thead><tr><th>File</th><th>Length</th><th>Tips</th><th>Palm</th><th>Heel</th><th></th></tr></thead><tbody id="list"></tbody></table></div></div>
</main><script>
const $=id=>document.getElementById(id);
setInterval(()=>{$('cam').src='/live.jpg?t='+Date.now()},300);
async function api(p,b){const r=await fetch(p,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b||{})});
 const j=await r.json();if(!r.ok)throw new Error(j.error||r.status);return j}
function esc(t){return String(t??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]))}
async function list(){const j=await (await fetch('/list')).json();$('count').textContent=j.length;
 $('list').innerHTML=j.map(r=>`<tr><td>${esc(r.file)}</td><td>${esc(r.hand_length_mm)}</td><td>${esc(r.fingertip_min_mm)}-${esc(r.fingertip_max_mm)}</td><td>${esc(r.palm_centre_mm)}</td><td>${esc(r.heel_mm)}</td><td class="${r.ok==='True'?'ok':'bad'}">${r.ok==='True'?'OK':'warn'}</td></tr>`).join('')}
async function record(){const id=$('id').value.trim();if(!/^[A-Za-z0-9-]{1,20}$/.test(id)){$('msg').className='msg bad';$('msg').textContent='Type an ID: letters, digits, - only';return}
 $('rec').disabled=true;$('msg').className='msg';$('msg').textContent='Recording - keep the hand still...';
 try{const r=await api('/record',{id,side:$('side').value,note:$('note').value});$('shot').src='/shot.jpg?t='+Date.now();$('result').hidden=false;
  $('lines').innerHTML=r.lines.map(l=>`<div class="${/WARNING|cannot|no hand/.test(l)?'bad':''}">${esc(l)}</div>`).join('');$('keep').disabled=!r.found;$('msg').textContent=''}
 catch(e){$('msg').className='msg bad';$('msg').textContent=e.message}finally{$('rec').disabled=false}}
$('rec').onclick=record;$('again').onclick=record;
$('keep').onclick=async()=>{try{const r=await api('/keep');$('result').hidden=true;$('msg').className='msg ok';$('msg').textContent='Saved '+r.saved+'.npz';$('id').value='';$('note').value='';list()}
 catch(e){$('msg').className='msg bad';$('msg').textContent=e.message}};
$('drop').onclick=async()=>{await api('/discard');$('result').hidden=true;$('msg').className='msg';$('msg').textContent='Discarded.'};
list();
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    cam = None

    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json"):
        if not isinstance(body, (bytes, bytearray)):
            body = json.dumps(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        p = urlparse(self.path).path
        if p == "/":
            return self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
        if p == "/live.jpg":
            j = self.cam.jpeg()
            return self._send(200, j, "image/jpeg") if j else self._send(404, {"error": "no picture yet"})
        if p == "/shot.jpg":
            pend = self.cam.pending
            return self._send(200, pend["jpeg"], "image/jpeg") if pend else self._send(404, {"error": "no scan"})
        if p == "/list":
            return self._send(200, saved_list())
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        p = urlparse(self.path).path
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
            if p == "/record":
                ident = str(body.get("id", "")).strip()
                side = str(body.get("side", "")).upper()[:1]
                if not collect.ID_RE.match(ident) or side not in ("L", "R"):
                    raise ValueError("ID (letters, digits, -) and L or R needed")
                return self._send(200, self.cam.record(ident, side, str(body.get("note", ""))[:120]))
            if p == "/keep":
                return self._send(200, {"saved": self.cam.keep()})
            if p == "/discard":
                self.cam.discard()
                return self._send(200, {"ok": True})
            return self._send(404, {"error": "not found"})
        except ValueError as e:
            return self._send(400, {"error": str(e)})


def main():
    from henna_preview import hand_detect
    hand_detect.verify_model()
    Handler.cam = Camera()
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Hand scans: open http://{HOST}:{PORT}  (Ctrl+C here to stop). Saving to {collect.OUT}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        Handler.cam.stop.set()
        Handler.cam.pipe.stop()


if __name__ == "__main__":
    main()
