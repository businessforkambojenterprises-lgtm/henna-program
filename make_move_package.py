"""Make the move package for a new PC (see NEW_LAPTOP_SETUP.md). Reads only; writes two zips on the Desktop.

essentials zip: the project (with .git, jobs and hand model; .venv and old build/smoke copies left out), the
desktop app without its backup-before-* folders, Claude's project folder, the shortcut and the restore script.
recordings zip: the .db3 scans in Documents. Each zip is written as .part, re-read (CRC of every file, every
expected file present with its size) and only then renamed. Refuses while Studio holds port 8765.
Run with --dry-run to see the contents and sizes only.
"""
import argparse
import datetime
import os
from pathlib import Path
import socket
import sys
import zipfile

ROOT = Path(__file__).resolve().parent
HOME = Path.home()
DESKTOP = HOME / "Desktop"
APP = DESKTOP / "HennaStudio-Windows-x64"
SHORTCUT = DESKTOP / "Henna Studio.lnk"
DOCUMENTS = HOME / "Documents"
CLAUDE_KEY = str(ROOT).replace(":", "-").replace("\\", "-").replace("/", "-").replace("+", "-")
CLAUDE = HOME / ".claude" / "projects" / CLAUDE_KEY
TOP = "Ai+d405_move"
SKIP_DIRS = {".venv", "__pycache__"}
SKIP_OUTPUTS = ("HennaStudio-Windows-x64-", "build-", "smoke-")


def walk(base, arc, skip=lambda rel: False):
    """Yield (file, arcname) for every file under base; skip(rel_dir) leaves a folder out."""
    for folder, dirs, files in os.walk(base):
        rel = Path(folder).relative_to(base)
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not skip(rel / d))
        for name in sorted(files):
            yield Path(folder) / name, f"{arc}/{(rel / name).as_posix()}"


def project_skip(rel):
    parts = rel.parts
    return len(parts) == 2 and parts[0] == "outputs" and parts[1].startswith(SKIP_OUTPUTS)


def essentials():
    yield from walk(ROOT, f"{TOP}/Ai+d405", project_skip)
    yield from walk(APP, f"{TOP}/HennaStudio-Windows-x64",
                    lambda rel: len(rel.parts) == 1 and rel.parts[0].startswith("backup-before-"))
    yield from walk(CLAUDE, f"{TOP}/claude_project")
    yield SHORTCUT, f"{TOP}/Henna Studio.lnk"
    yield ROOT / "restore_on_new_pc.bat", f"{TOP}/restore_on_new_pc.bat"
    yield ROOT / "NEW_LAPTOP_SETUP.md", f"{TOP}/READ_ME_FIRST.md"


def recordings():
    for f in sorted(DOCUMENTS.glob("*.db3")):
        yield f, f"{TOP}/recordings/{f.name}"


def port_in_use(port=8765):
    with socket.socket() as s:
        try:
            s.bind(("127.0.0.1", port))
        except OSError:
            return True
    return False


def write_zip(target, entries):
    """Write entries to target via a .part file, verify it, then rename. Returns (files, bytes)."""
    part = target.with_name(target.name + ".part")
    expected = {}
    with zipfile.ZipFile(part, "w", zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as z:
        for path, arc in entries:
            z.write(path, arc)
            expected[arc] = (path, z.getinfo(arc).file_size)
    with open(part, "rb+") as f:
        os.fsync(f.fileno())
    with zipfile.ZipFile(part) as z:
        bad = z.testzip()
        if bad:
            raise RuntimeError(f"{part.name}: CRC error in {bad}")
        sizes = {i.filename: i.file_size for i in z.infolist()}
    for arc, (path, size) in expected.items():
        if sizes.get(arc) != size:
            raise RuntimeError(f"{part.name}: {arc} missing or wrong size")
        live = "/claude_project/" in arc            # this conversation keeps writing its chat file
        if not live and path.stat().st_size != size:
            raise RuntimeError(f"{arc} changed while it was being packed - close Studio and run again")
    os.replace(part, target)
    return len(expected), sum(size for _, size in expected.values())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="list sizes only, write nothing")
    args = parser.parse_args()
    for need in (APP, SHORTCUT, CLAUDE, ROOT / "models" / "hand_landmarker.task"):
        if not need.exists():
            print(f"STOP: {need} is missing.")
            return 1
    stamp = datetime.date.today().isoformat()
    plan = [(DESKTOP / f"{TOP}_{stamp}_essentials.zip", essentials),
            (DESKTOP / f"{TOP}_{stamp}_recordings.zip", recordings)]
    if args.dry_run:
        for target, entries in plan:
            files = list(entries())
            print(f"{target.name}: {len(files)} files, {sum(p.stat().st_size for p, _ in files) / 1e6:,.0f} MB")
        return 0
    if port_in_use():
        print("STOP: port 8765 is in use - close Henna Studio first (its jobs and log must not change while packing).")
        return 1
    for target, _ in plan:
        if target.exists():
            print(f"STOP: {target} already exists - move or delete it first.")
            return 1
    for target, entries in plan:
        print(f"Writing {target.name} ...", flush=True)
        files, size = write_zip(target, entries())
        print(f"  verified: {files} files, {size / 1e6:,.0f} MB unpacked, zip {target.stat().st_size / 1e6:,.0f} MB")
    print("Both zips verified. Next: NEW_LAPTOP_SETUP.md Part A step 3.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
