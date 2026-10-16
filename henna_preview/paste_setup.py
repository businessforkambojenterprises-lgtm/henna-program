"""The machine's paste setup, chosen in Admin (user 2026-10-16: valve selection under the admin panel).

outputs/paste_setup.json (per device): {"dispenser": "none" | "pneumatic" | "servo_ssr", "start_s", "stop_s"}.
The Pi server applies it to every program it writes (the operator's Program step shows it, cannot change it);
checked with dispense.settings, the same rules as the Program step.
"""
import json
import os

from henna_preview import dispense

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SETUP_FILE = os.path.join(ROOT, "outputs", "paste_setup.json")


def load(path=SETUP_FILE):
    """The saved setup, or None (nothing chosen in Admin yet)."""
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return None
    if d.get("dispenser") in (None, "", "none"):
        return {"dispenser": "none", "label": "None - hover only"}
    cfg = dispense.settings(d["dispenser"], d.get("start_s"), d.get("stop_s"))
    return cfg


def save(dispenser, start_s=None, stop_s=None, path=SETUP_FILE):
    cfg = dispense.settings(dispenser, start_s, stop_s)
    out = {"dispenser": "none"} if cfg is None else {k: cfg[k] for k in ("dispenser", "start_s", "stop_s")}
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
    return load(path)


def apply(params, setup):
    """Program-route params with the Admin's paste choice (None: unchanged - no setup saved yet)."""
    if setup is None:
        return params
    p = dict(params or {})
    p["dispenser"] = setup["dispenser"]
    if setup["dispenser"] != "none":
        p["start_delay_s"], p["stop_delay_s"] = setup["start_s"], setup["stop_s"]
    return p
