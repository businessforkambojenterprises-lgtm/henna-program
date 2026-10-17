"""Storage guard and recording cleanup on the Pi (user 2026-10-17: "after many scans if sdcard becomes full" - yes to
1 the guard and 2 the cleanup under Admin).

A scan writes about 250-300 MB (~/Documents/*.db3); everything else of a job is a few MB.
1. Guard: new recordings are refused below MIN_FREE_GB free on the card, so it never fills up completely.
2. Cleanup (Admin -> Storage, and at Studio start when 'auto' is on): deletes RECORDINGS ONLY, and only those that are
     older than keep_days (default 14), and
     not the scan of an unfinished job (a job is finished once a run of its program finished), and
   in ~/Documents (*.db3 / *.bag, with their .machine.json note). Never: job folders, programs, logs, calibration or the
   daily-check reference (outputs/). Optionally copies each file to a USB stick first (checked by size) and deletes it
   only after a good copy. Every cleanup returns what it did for one log row.
"""
import glob
import json
import os
import shutil
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REC_DIR = os.path.join(os.path.expanduser("~"), "Documents")
SETTINGS = os.path.join(ROOT, "outputs", "storage_settings.json")
MIN_FREE_GB = 3.0
DEFAULTS = {"keep_days": 14, "auto": True}
GB = 1024 ** 3


class StorageError(ValueError):
    pass


def free_gb(path=REC_DIR):
    p = path if os.path.isdir(path) else os.path.dirname(path) or "."
    u = shutil.disk_usage(p)
    return u.free / GB, u.total / GB


def guard(path=REC_DIR, minimum=MIN_FREE_GB):
    """Raise before a new recording when the card is nearly full."""
    free, _ = free_gb(path)
    if free < minimum:
        raise StorageError(f"Storage nearly full ({free:.1f} GB free, {minimum:g} GB needed for a scan) - "
                           "clean up in Admin, Storage")
    return free


def load_settings(path=SETTINGS):
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        d = {}
    return {"keep_days": int(d.get("keep_days", DEFAULTS["keep_days"])), "auto": bool(d.get("auto", DEFAULTS["auto"]))}


def save_settings(keep_days, auto, path=SETTINGS):
    keep_days = int(keep_days)
    if not 1 <= keep_days <= 365:
        raise StorageError("keep recordings 1 to 365 days")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"keep_days": keep_days, "auto": bool(auto)}, f, indent=2)
    return load_settings(path)


def _real(p):
    return os.path.normcase(os.path.realpath(p))


def needed_recordings(out_root, is_done):
    """Recordings (real paths) that unfinished jobs still use."""
    keep = set()
    for p in glob.glob(os.path.join(out_root, "*-job", "job.json")):
        try:
            with open(p, encoding="utf-8") as f:
                job = json.load(f)
        except (OSError, ValueError):
            continue
        rec = ((job.get("steps") or {}).get("scan") or {}).get("recording")
        if rec and not is_done(job):
            keep.add(_real(rec))
    return keep


def recordings(rec_dir=REC_DIR):
    out = []
    for p in sorted(glob.glob(os.path.join(rec_dir, "*.db3")) + glob.glob(os.path.join(rec_dir, "*.bag"))):
        try:
            st = os.stat(p)
        except OSError:
            continue
        out.append({"path": p, "bytes": st.st_size, "mtime": st.st_mtime})
    return out


def plan(out_root, is_done, keep_days, rec_dir=REC_DIR, now=None):
    """-> (deletable, kept_for_jobs, all) recording lists."""
    now = time.time() if now is None else now
    keep = needed_recordings(out_root, is_done)
    every = recordings(rec_dir)
    old = [r for r in every if now - r["mtime"] > keep_days * 86400]
    deletable = [r for r in old if _real(r["path"]) not in keep]
    kept = [r for r in every if _real(r["path"]) in keep]
    return deletable, kept, every


def summary(out_root, is_done, rec_dir=REC_DIR, settings_path=SETTINGS):
    s = load_settings(settings_path)
    deletable, kept, every = plan(out_root, is_done, s["keep_days"], rec_dir)
    free, total = free_gb(rec_dir)
    return dict(s, free_gb=round(free, 1), total_gb=round(total, 1), min_free_gb=MIN_FREE_GB,
                recordings=len(every), recordings_gb=round(sum(r["bytes"] for r in every) / GB, 2),
                deletable=len(deletable), deletable_gb=round(sum(r["bytes"] for r in deletable) / GB, 2),
                kept_for_unfinished_jobs=len(kept))


def usb_stick(media=None):
    """The first mounted USB stick under /media/<user>/, or None."""
    media = media or os.path.join("/media", os.path.basename(os.path.expanduser("~")))
    for d in sorted(glob.glob(os.path.join(media, "*"))):
        if os.path.isdir(d) and os.access(d, os.W_OK):
            return d
    return None


def cleanup(out_root, is_done, keep_days, export_to=None, rec_dir=REC_DIR, now=None):
    """Delete the old recordings no unfinished job needs; with export_to, copy each first and delete only after the
    copy has the same size. -> {'deleted', 'freed_gb', 'exported', 'export_dir', 'skipped'}"""
    deletable, _, _ = plan(out_root, is_done, keep_days, rec_dir, now)
    rec_root = _real(rec_dir)
    export_dir = None
    if export_to:
        export_dir = os.path.join(export_to, "henna_recordings", time.strftime("%Y%m%d-%H%M%S"))
        os.makedirs(export_dir, exist_ok=True)
    deleted, freed, exported, skipped = [], 0, 0, []
    for r in deletable:
        p = r["path"]
        if os.path.dirname(_real(p)) != rec_root:              # only files directly in ~/Documents
            skipped.append(os.path.basename(p))
            continue
        note = p + ".machine.json"
        if export_dir:
            dst = os.path.join(export_dir, os.path.basename(p))
            try:
                shutil.copyfile(p, dst)
                if os.path.exists(note):
                    shutil.copyfile(note, dst + ".machine.json")
                if os.path.getsize(dst) != r["bytes"]:
                    raise OSError("size differs after copy")
                exported += 1
            except OSError:
                skipped.append(os.path.basename(p))
                continue
        try:
            os.remove(p)
            if os.path.exists(note):
                os.remove(note)
        except OSError:
            skipped.append(os.path.basename(p))
            continue
        deleted.append(os.path.basename(p))
        freed += r["bytes"]
    return {"deleted": len(deleted), "files": deleted, "freed_gb": round(freed / GB, 2), "exported": exported,
            "export_dir": export_dir, "skipped": skipped}
