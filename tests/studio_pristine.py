"""The older Studio modules as they are before any later Studio version is imported - for the v10-v23 tests.

Importing Studio v24-v29 patches the older modules on purpose (v24-v26: v10.cal per machine and the machine link's
calibration; v28: v11.prosthetic_only and v10.camera; v29: v17.checked_program, v14's standoff and jog rules ...).
The v24-v29 tests import them while the suite runs, so whether a v10-v23 test saw the original or the patched module
depended on which test file happened to run first (2026-10-22: a v29 test file sorting first broke 18 older tests).

remember() records the original values when a test module is loaded - unittest loads every test module before it
runs any test, and no test module imports v24 or later at load time, so these are the originals. A v10-v23 test
module calls apply() in setUpModule and undo() in tearDownModule: its tests see the original modules whatever ran
before, and the later tests get back exactly what was there."""
import os
import sys

_MISSING = object()
_SEEN = {}                 # (module name, attribute) -> original value
_LINKS = {}                # id(link) -> (link, own _calibration or _MISSING)

ATTRS = {
    "webui_server_v10": ("cal", "log_row", "camera", "STEP22", "CAL_FILE"),
    "webui_server_v11": ("prosthetic_only",),
    "webui_server_v14": ("MIN_STANDOFF_MM", "jog_step_ok", "JOG_STEP_TEXT"),
    "webui_server_v17": ("checked_program", "STEP23"),
    "henna_preview.grbl_program_link": ("jog_step_ok", "JOG_STEP_TEXT", "TRAVEL_Z", "X_MAX", "Y_MAX"),
    # the machine link's motion values: Studio v25+ sets them from the ACTIVE machine's registry entry (the user moves
    # the belt drive's scan position and travel Z), the v10-v23 tests expect the original belt-drive constants
    "henna_preview.grbl_scan_link": ("POSE", "TRAVEL_Z", "ROUTE", "COMMANDS"),
}
# The shared machine link: which object each module's `machine` names and its class are left as they are (v15-v22
# point the older modules at the newest link on purpose, and their tests check that); only the calibration source
# that v24-v26 give it is put back.
LINK_MODULES = ("webui_server_v13", "webui_server_v14", "webui_server_v15", "webui_server_v16", "webui_server_v17",
                "webui_server_v20", "webui_server_v21", "webui_server_v22", "webui_server_v23")
# What the v10-v23 tests read as "the calibration": a frozen copy of the belt drive's file as it was when they were
# written, NOT the live current_calibration.json - the user moves the belt drive's scan position, travel Z and floor.
FIXTURE_CAL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "belt_drive_calibration.json")
OVERRIDES = {("webui_server_v10", "CAL_FILE"): FIXTURE_CAL}
LATER = ("webui_server_v24", "webui_server_v25", "webui_server_v26", "webui_server_v27", "webui_server_v28",
         "webui_server_v29")


def remember():
    """Record the original values of the modules loaded so far (only before any v24+ module is loaded)."""
    if any(m in sys.modules for m in LATER):
        return
    for name, attrs in ATTRS.items():
        mod = sys.modules.get(name)
        if mod is None:
            continue
        for a in attrs:
            if (name, a) not in _SEEN and hasattr(mod, a):
                _SEEN[(name, a)] = getattr(mod, a)
    for name in LINK_MODULES:
        link = getattr(sys.modules.get(name), "machine", None)
        if link is not None and id(link) not in _LINKS:
            _LINKS[id(link)] = (link, link.__dict__.get("_calibration", _MISSING))


def apply():
    """Put the original values back; -> what was there, for undo()."""
    now = []
    for (name, a), value in _SEEN.items():
        mod = sys.modules.get(name)
        if mod is not None:
            now.append((mod, a, getattr(mod, a, _MISSING)))
            setattr(mod, a, OVERRIDES.get((name, a), value))
    links = []
    for link, cal in _LINKS.values():
        links.append((link, link.__dict__.get("_calibration", _MISSING)))
        _set_cal(link, cal)
    return now, links


def undo(state):
    now, links = state
    for mod, a, value in now:
        if value is _MISSING:
            if hasattr(mod, a):
                delattr(mod, a)
        else:
            setattr(mod, a, value)
    for link, cal in links:
        _set_cal(link, cal)


def _set_cal(link, cal):
    if cal is _MISSING:
        link.__dict__.pop("_calibration", None)
    else:
        link._calibration = cal
