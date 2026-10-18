"""Send commands to bhs.dll in a running instance and print what it logs back.

Usage: python -I tools/bhs_cmd.py INSTANCE COMMAND [ARG...]   e.g. bhs_cmd.py listen sound
Several commands can be given separated by ';'. See the top of tools/bhs/bhs.cpp for the list.
"""
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def main():
    if len(sys.argv) < 3:
        raise SystemExit(__doc__)
    folder = REPO / "game" / sys.argv[1] / "bhs"
    log = folder / "log.txt"
    start = log.stat().st_size if log.exists() else 0
    lines = [c.strip() for c in " ".join(sys.argv[2:]).split(";") if c.strip()]
    (folder / "cmd.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    for _ in range(30):  # the DLL reads the file within a frame while the game is running
        time.sleep(0.1)
        if not (folder / "cmd.txt").exists():
            break
    else:
        raise SystemExit("no answer: is the game running with bhs.dll injected?")
    time.sleep(0.2)
    with open(log, encoding="utf-8", errors="replace") as f:
        f.seek(start)
        print(f.read(), end="")


if __name__ == "__main__":
    main()
