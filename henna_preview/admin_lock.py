"""Admin PIN for the Pi (user 2026-10-16: "all the calibration, machine selection and valve selection option should
come under admin panel", locked with a 4-digit PIN).

The PIN is kept only as a salted PBKDF2-SHA256 hash in outputs/admin_pin.json (per device, not in git). Unlocking
gives a random session token, valid for SESSION_S after the last admin action. The Pi server refuses every admin
route without a valid token (webui_server_pi.PiHandler.do_POST). 5 wrong PINs lock unlocking for LOCKOUT_S.
Forgotten PIN: raspberry_pi/admin_pin_reset.py on the Pi itself (needs the Pi's own login) removes it; the next Admin
open asks for a new one. Nothing else is touched by a reset.
"""
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PIN_FILE = os.path.join(ROOT, "outputs", "admin_pin.json")
SESSION_S = 600.0
MAX_TRIES, LOCKOUT_S = 5, 60.0
ITERATIONS = 200_000
PIN_RE = re.compile(r"^\d{4}$")


class AdminError(ValueError):
    pass


def _hash(pin, salt_hex):
    return hashlib.pbkdf2_hmac("sha256", pin.encode("ascii"), bytes.fromhex(salt_hex), ITERATIONS).hex()


class AdminLock:
    def __init__(self, path=PIN_FILE, clock=time.monotonic):
        self.path, self.clock = path, clock
        self.lock = threading.Lock()
        self.tokens, self.wrong, self.locked_until = {}, 0, 0.0

    # ---- the stored PIN ----
    def _read(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                d = json.load(f)
            return d if d.get("hash") and d.get("salt") else None
        except (OSError, ValueError):
            return None

    def pin_set(self):
        return self._read() is not None

    def _matches(self, pin):
        d = self._read()
        return bool(d) and isinstance(pin, str) and hmac.compare_digest(_hash(pin, d["salt"]), d["hash"])

    def _new_token_locked(self):
        token = secrets.token_hex(16)
        self.tokens[token] = self.clock() + SESSION_S
        return token

    # ---- actions ----
    def set_pin(self, pin, current=None, token=None):
        """First PIN: anyone at the Pi. Change: the current PIN or an unlocked session. -> a new session token."""
        pin = str(pin or "")
        if not PIN_RE.match(pin):
            raise AdminError("the PIN must be 4 digits")
        with self.lock:
            if self.pin_set() and not (self._valid_locked(token) or self._matches(str(current or ""))):
                raise AdminError("enter the current PIN to change it")
            salt = secrets.token_hex(16)
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump({"salt": salt, "hash": _hash(pin, salt), "iterations": ITERATIONS,
                           "set": time.strftime("%Y-%m-%dT%H:%M:%S")}, f)
            self.tokens.clear()
            self.wrong = 0
            return self._new_token_locked()

    def unlock(self, pin):
        with self.lock:
            now = self.clock()
            if now < self.locked_until:
                raise AdminError(f"too many wrong PINs - wait {int(self.locked_until - now) + 1} s")
            if not self.pin_set():
                raise AdminError("no admin PIN yet - set one first")
            if not self._matches(str(pin or "")):
                self.wrong += 1
                if self.wrong >= MAX_TRIES:
                    self.wrong, self.locked_until = 0, now + LOCKOUT_S
                    raise AdminError(f"wrong PIN - unlocking is blocked for {LOCKOUT_S:g} s")
                raise AdminError(f"wrong PIN ({MAX_TRIES - self.wrong} tries left)")
            self.wrong = 0
            return self._new_token_locked()

    def _valid_locked(self, token):
        exp = self.tokens.get(token or "")
        now = self.clock()
        if exp is None or now > exp:
            self.tokens.pop(token or "", None)
            return False
        self.tokens[token] = now + SESSION_S                # each admin action keeps it open
        return True

    def valid(self, token):
        with self.lock:
            return self._valid_locked(token)

    def lock_now(self, token=None):
        with self.lock:
            if token:
                self.tokens.pop(token, None)
            else:
                self.tokens.clear()

    def status(self, token=None):
        with self.lock:
            now = self.clock()
            return {"pin_set": self.pin_set(), "unlocked": self._valid_locked(token),
                    "blocked_s": max(0, int(self.locked_until - now) + 1) if now < self.locked_until else 0,
                    "session_s": SESSION_S}

    def reset(self):
        with self.lock:
            try:
                os.remove(self.path)
            except FileNotFoundError:
                pass
            self.tokens.clear()
            self.wrong, self.locked_until = 0, 0.0
