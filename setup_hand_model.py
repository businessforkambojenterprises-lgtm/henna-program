"""Get the approved hand model (models/hand_landmarker.task) and check its SHA-256.

Source URL and expected SHA-256 come from models/MODEL_INFO.txt (approved 2026-10-01).
A present file is only checked; a missing or wrong one is downloaded to a temporary name and
kept only if its SHA-256 matches. Exit code 0 = model present and correct.
"""
import hashlib
import os
from pathlib import Path
import sys
import urllib.request

ROOT = Path(__file__).resolve().parent
INFO = ROOT / "models" / "MODEL_INFO.txt"
MODEL = ROOT / "models" / "hand_landmarker.task"


def expected():
    url = sha = None
    for line in INFO.read_text(encoding="utf-8").splitlines():
        key, _, value = line.strip().partition(":")
        if key == "Source" and url is None:
            url = value.strip()
        elif key == "SHA-256" and sha is None:
            sha = value.strip().lower()
    if not url or not sha:
        raise ValueError(f"{INFO} has no Source or SHA-256 line")
    return url, sha


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def main():
    url, want = expected()
    if MODEL.is_file() and sha256(MODEL) == want:
        print(f"Hand model OK ({MODEL.name}, SHA-256 matches).")
        return 0
    print(f"Downloading the hand model from {url} ...")
    part = MODEL.with_name(MODEL.name + ".download")
    try:
        with urllib.request.urlopen(url, timeout=60) as response, open(part, "wb") as out:
            while block := response.read(1 << 20):
                out.write(block)
            out.flush()
            os.fsync(out.fileno())
        got = sha256(part)
        if got != want:
            print(f"REFUSED: downloaded SHA-256 {got} is not the approved {want}. Model not installed.")
            return 1
        os.replace(part, MODEL)
    except OSError as error:
        print(f"Download failed: {error}. Copy models\\hand_landmarker.task from the old PC instead.")
        return 1
    finally:
        if part.exists():
            part.unlink()
    print("Hand model downloaded and verified (SHA-256 matches).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
