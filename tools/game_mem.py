"""Locate the game manager and game controller in a running Dead Star instance (read-only).

Usage: python -I tools/game_mem.py [INSTANCE]   (default server)

The game manager's component table is at +0x40118 (entries of 16 bytes: generation, component
pointer; count at +0x40120) and the game controller's unique id at +0x1401b8.
Starting from any component found by its type (an AI controller), its
table entry gives the table, and the table pointer gives the manager.
"""
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ai_state as A  # noqa: E402

MGR_TABLE = 0x40118
MGR_CONTROLLER_ID = 0x1401B8


def find(mem, base):
    vt_ai = base + A.VT_AI - A.IMAGE_BASE
    comp = mem.scan([vt_ai])[vt_ai][0]
    idx, gen = struct.unpack("<II", mem.read(comp + 0x18, 8))
    for entry in mem.scan([comp])[comp]:
        if struct.unpack("<I", mem.read(entry - 8, 4))[0] != gen:
            continue
        table = entry - 8 - idx * 16
        for at in mem.scan([table])[table]:
            mgr = at - MGR_TABLE
            cidx, cgen = struct.unpack("<II", mem.read(mgr + MGR_CONTROLLER_ID, 8))
            count = struct.unpack("<Q", mem.read(mgr + MGR_TABLE + 8, 8))[0]
            if cidx < count:
                g, ctrl = struct.unpack("<IxxxxQ", mem.read(table + cidx * 16, 16))
                if g == cgen:
                    return mgr, ctrl
    raise SystemExit("game manager not found")


def main():
    pid = A.find_process(sys.argv[1] if len(sys.argv) > 1 else "server")
    mem = A.Mem(pid)
    base = A.module_base(mem.h, A.DLL)
    mgr, ctrl = find(mem, base)
    players = struct.unpack("<Q", mem.read(ctrl + 0x80, 8))[0]
    print(f"manager {mgr:#x} controller {ctrl:#x} players {players}")


if __name__ == "__main__":
    main()
