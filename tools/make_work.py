"""Create game/work (or game/NAME), a runnable copy of the game for testing mods.

Usage: python -I tools/make_work.py [NAME]

Copies Bin64 and SavesDir from game/original, hard-links GameData/gamedata.ara (no extra 3.4 GB;
don't modify it in place, write a new archive instead), and adds steam_appid.txt so the game can
start outside the Steam library. Steam must be running. Existing files are kept.
"""
import os
import shutil
import sys
from pathlib import Path

STEAM_APPID = "366400"
REPO = Path(__file__).resolve().parent.parent
ORIGINAL = REPO / "game" / "original"


def main():
    work = REPO / "game" / (sys.argv[1] if len(sys.argv) > 1 else "work")
    for d in ("Bin64", "SavesDir"):
        shutil.copytree(ORIGINAL / d, work / d, dirs_exist_ok=True)
    (work / "Log").mkdir(parents=True, exist_ok=True)
    (work / "Bin64" / "steam_appid.txt").write_text(STEAM_APPID)

    archive = work / "GameData" / "gamedata.ara"
    archive.parent.mkdir(parents=True, exist_ok=True)
    if not archive.exists():
        os.link(ORIGINAL / "GameData" / "gamedata.ara", archive)
    print(f"Ready: {work / 'Bin64' / 'DeadStar.exe'}")


if __name__ == "__main__":
    main()
