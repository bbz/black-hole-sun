"""Copy the Steam install of Dead Star into game/original and verify it.

Usage: python -I tools/copy_game.py [STEAM_DEADSTAR_DIR]
"""
import hashlib
import shutil
import sys
from pathlib import Path

DEFAULT_SRC = Path(r"C:\Program Files (x86)\Steam\steamapps\common\DeadStar")
REPO = Path(__file__).resolve().parent.parent
DEST = REPO / "game" / "original"
MANIFEST = REPO / "docs" / "binaries.sha256"


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_SRC
    if not (src / "Bin64" / "DeadStar.exe").exists():
        sys.exit(f"Dead Star not found at {src}")
    DEST.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, DEST, dirs_exist_ok=True)
    print(f"Copied {src} -> {DEST}")

    bad = 0
    for line in MANIFEST.read_text().splitlines():
        digest, name = line.split(maxsplit=1)
        actual = sha256(DEST / name)
        ok = actual == digest
        bad += not ok
        print(f"{'OK  ' if ok else 'DIFF'} {name}")
    if bad:
        print(f"{bad} file(s) differ from the known build (REL-147994); the patches and memory offsets in these tools may not match.")


if __name__ == "__main__":
    main()
