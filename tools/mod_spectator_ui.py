"""Build a mod that hides the "LAUNCH [F]" banner and launch ring, for a spectator instance.

They are the HUD's spawn launch indicator (needle/ui/hud/script/_win/spawn_launch_indicator.cgpr; the
respawn menu has a look-alike banner that isn't the one shown). A spectator can't launch, so they
just cover the view (and the ship being followed, which is centred under the banner). Every
component that draws (text, sprites, models) is removed; the objects themselves stay, since other
objects send them messages. Install it only into the spectator's instance (e.g. game/listen/GameData):
players need the banner.

Usage: python -I tools/mod_spectator_ui.py [EXTRACTED_DIR] [OUT_GAMEDATA_DIR]
  defaults: game/extracted -> game/mods/spectator_ui/GameData
"""
import sys
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cgpr  # noqa: E402

INDICATOR = "needle/ui/hud/script/_win/spawn_launch_indicator.cgpr"
# drawing components: TextRender/*, Model/*, NeedleUI/Sprite and NeedleUI/SpriteAnimation
DRAW_TYPES = {zlib.crc32(n) for n in (b"TextRender", b"Model")}
DRAW_SUBTYPES = {zlib.crc32(n) for n in (b"Sprite", b"SpriteAnimation")}


def subtree(objs):
    for o in objs:
        yield o
        yield from subtree(o["children"])


def draws(c):
    if c["type"] in DRAW_TYPES:
        return True
    dec = cgpr.decode_body(c["body"])
    return c["type"] == zlib.crc32(b"NeedleUI") and dec and dec["subtype"] in DRAW_SUBTYPES


def main():
    src = Path(sys.argv[1] if len(sys.argv) > 1 else "game/extracted")
    out = Path(sys.argv[2] if len(sys.argv) > 2 else "game/mods/spectator_ui/GameData")
    pkg = cgpr.parse((src / INDICATOR).read_bytes())
    removed = 0
    for o in subtree(pkg["objects"]):
        keep = [c for c in o["components"] if not draws(c)]
        removed += len(o["components"]) - len(keep)
        o["components"] = keep
    (out / INDICATOR).parent.mkdir(parents=True, exist_ok=True)
    (out / INDICATOR).write_bytes(cgpr.build(pkg))
    print(f"removed {removed} drawing components; wrote {out / INDICATOR}")


if __name__ == "__main__":
    main()
