"""USB link to the GRBL controller (MKS DLC32, CH340 on COM7) - position reading and homing ONLY.

What this module can send, and nothing else:
  ?      status request (realtime, polled about 4 times a second)
  $HZ    home Z            (then)   $H   home all       - only from Idle or Alarm, after confirmation
  !      feed hold, then Ctrl-X soft reset             - STOP
There is deliberately no method to send any other line: programs and moves stay in UGS.
The port is opened with DTR/RTS held low so connecting does not reset the ESP32; if the
controller still restarts (its welcome banner appears), that is reported in messages.
"""
import collections
import re
import threading
import time

BAUD = 115200
STATUS_RE = re.compile(r"^<([A-Za-z]+)(?::\d+)?\|MPos:(-?\d+\.\d+),(-?\d+\.\d+),(-?\d+\.\d+)")
HOMING_TIMEOUT_S = 150.0
FRESH_S = 1.5


class MachineError(RuntimeError):
    """Not connected, busy, refused, or the controller reported an error."""


def _open_serial(port):
    import serial
    s = serial.Serial()
    s.port, s.baudrate, s.timeout, s.write_timeout = port, BAUD, 0.1, 1.0
    s.dtr = False          # keep the ESP32 auto-reset lines quiet
    s.rts = False
    try:
        s.open()
    except serial.SerialException as e:
        if "denied" in str(e).lower() or "busy" in str(e).lower():
            raise MachineError(f"{port} is in use - disconnect UGS (or close the other program) first")
        raise MachineError(f"could not open {port}: {e}")
    return s


class GrblLink:
    def __init__(self, opener=_open_serial):
        self._open = opener
        self._lock = threading.Lock()
        self._ser = None
        self._stop = threading.Event()
        self._resp = collections.deque()
        self._resp_ev = threading.Event()
        self.port = None
        self.state = None
        self.mpos = None
        self.line = None
        self.t_status = 0.0
        self.busy = None
        self.messages = collections.deque(maxlen=30)
        self.restarted = False

    # ---- connection ----
    def connect(self, port="COM7", wait_s=3.0):
        with self._lock:
            if self._ser:
                return self.status()
            self._ser = self._open(port)
            self.port, self.restarted, self.state, self.line = port, False, None, None
            self._stop.clear()
            threading.Thread(target=self._reader, daemon=True).start()
            threading.Thread(target=self._poller, daemon=True).start()
        t0 = time.time()
        while time.time() - t0 < wait_s:
            if self.line:
                return self.status()
            time.sleep(0.05)
        self.disconnect()
        raise MachineError(f"no answer from the controller on {port} - check the USB cable and that it is powered")

    def disconnect(self):
        with self._lock:
            ser, self._ser = self._ser, None
            self._stop.set()
            self.busy = None
        if ser:
            try:
                ser.close()
            except Exception:
                pass
        return self.status()

    def status(self):
        age = time.time() - self.t_status if self.t_status else None
        return {"connected": self._ser is not None, "port": self.port, "state": self.state, "mpos": self.mpos,
                "line": self.line, "age_s": round(age, 2) if age is not None else None, "busy": self.busy,
                "restarted": self.restarted, "messages": list(self.messages)[-8:]}

    def status_line(self):
        """The current '?' line, only when connected, fresh and Idle (for scans and readings)."""
        if not self._ser:
            raise MachineError("machine not connected")
        if not self.line or time.time() - self.t_status > FRESH_S:
            raise MachineError("no fresh position from the controller")
        if self.state != "Idle":
            raise MachineError(f"machine is {self.state}, not Idle - wait until it stops")
        return self.line

    # ---- background ----
    def _write(self, data):
        ser = self._ser
        if not ser:
            raise MachineError("machine not connected")
        ser.write(data)

    def _poller(self):
        while not self._stop.is_set():
            try:
                self._write(b"?")
            except Exception:
                pass
            time.sleep(0.25)

    def _reader(self):
        buf = b""
        while not self._stop.is_set():
            ser = self._ser
            if not ser:
                break
            try:
                chunk = ser.read(256)
            except Exception as e:
                self.messages.append(f"serial read failed: {e}")
                break
            if not chunk:
                continue
            buf += chunk
            while b"\n" in buf:
                raw, buf = buf.split(b"\n", 1)
                self._handle(raw.decode("ascii", "replace").strip())

    def _handle(self, text):
        if not text:
            return
        m = STATUS_RE.match(text)
        if m:
            self.state, self.mpos = m.group(1), [float(m.group(i)) for i in (2, 3, 4)]
            self.line, self.t_status = text[:text.find(">") + 1] if ">" in text else text, time.time()
            return
        if text == "ok" or text.startswith("error"):
            self._resp.append(text)
            self._resp_ev.set()
            return
        if text.lower().startswith("grbl"):
            self.restarted = True
            text = "controller restarted: " + text
        self.messages.append(text)

    def _command(self, line, timeout):
        self._resp.clear()
        self._resp_ev.clear()
        self._write((line + "\n").encode("ascii"))
        t0 = time.time()
        while time.time() - t0 < timeout:
            if self._resp_ev.wait(0.2) and self._resp:
                r = self._resp.popleft()
                if r != "ok":
                    raise MachineError(f"{line} refused by the controller: {r}")
                return
            if self.busy is None:
                raise MachineError(f"{line} stopped")
        raise MachineError(f"{line} did not finish within {timeout:.0f} s")

    # ---- homing and stop ----
    def start_homing(self, on_done=None):
        with self._lock:
            if not self._ser:
                raise MachineError("machine not connected")
            if self.busy:
                raise MachineError(f"machine is busy ({self.busy})")
            if self.state not in ("Idle", "Alarm"):
                raise MachineError(f"machine is {self.state} - homing only from Idle or Alarm")
            self.busy = "homing"

        def run():
            err = None
            try:
                for cmd in ("$HZ", "$H"):
                    self._command(cmd, HOMING_TIMEOUT_S)
            except Exception as e:
                err = str(e)
                self.messages.append("homing failed: " + err)
            finally:
                self.busy = None
            if on_done:
                on_done(err)

        threading.Thread(target=run, daemon=True).start()
        return self.status()

    def stop(self):
        """Feed hold, then soft reset. After a reset during motion the controller needs homing again."""
        if not self._ser:
            raise MachineError("machine not connected")
        self.busy = None
        self._write(b"!")
        time.sleep(0.2)
        self._write(b"\x18")
        self.messages.append("STOP sent (feed hold + reset) - home again before further use")
        return self.status()
