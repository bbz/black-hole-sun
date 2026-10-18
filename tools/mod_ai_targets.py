"""Build a mod that lets AI ships target enemy outposts.

An AI only targets a NeedleScannable/Generic whose properties have flag 0x103d1485 set (on by
default). Outposts
("Structure (Base)" in needle/structures/outposts/...) turn it off, so AI ships never shoot them,
even while their turrets shoot back. This turns it on for every outpost prefab. Mines, bombs, cargo
and capital ship power nodes keep it off.

Usage: python -I tools/mod_ai_targets.py [EXTRACTED_DIR] [OUT_GAMEDATA_DIR]
  defaults: game/extracted -> game/mods/ai_targets/GameData
"""
import sys
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cgpr  # noqa: E402

OUTPOSTS = "needle/structures/outposts"
TARGETABLE = 0x103d1485
SCANNABLE = zlib.crc32(b"NeedleScannable")
STRUCTURE = "Structure (Base)"


def objects(objs):
    for o in objs:
        yield o
        yield from objects(o["children"])


def main():
    src = Path(sys.argv[1] if len(sys.argv) > 1 else "game/extracted")
    out = Path(sys.argv[2] if len(sys.argv) > 2 else "game/mods/ai_targets/GameData")
    for path in sorted((src / OUTPOSTS).rglob("*.cgpr")):
        pkg = cgpr.parse(path.read_bytes())
        changed = 0
        for o in objects(pkg["objects"]):
            if o["name"] != STRUCTURE:
                continue
            for c in o["components"]:
                if c["type"] == SCANNABLE:
                    c["body"] = cgpr.set_prop(c["body"], TARGETABLE, b"\x01")
                    changed += 1
        if changed:
            rel = path.relative_to(src)
            (out / rel).parent.mkdir(parents=True, exist_ok=True)
            (out / rel).write_bytes(cgpr.build(pkg))
            print(f"{rel.as_posix()}: {changed}")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
