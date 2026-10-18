"""Build a mod that has the ai_teams squads fly player ships instead of NPC ships.

Each AI ship template in the ai_teams Conquest layout (needle/ships/ai/<class>/..._capture.cgpr) is
replaced with a player ship of the same class (scout, raider or frigate), races A to E in turn, so a
squad flies a mix of races. The player ship prefab (needle/ships/race<r>/<class>/.../ship_race<r>_<class>.cgpr)
is changed in two ways:

- Pilot: its NeedleController/Player component is swapped for the template's NeedleController/AI
  and StateMachine/Animated (the AI's tuning and its state machine, ai_main.cesm). StateMachine/Animated
  names the state machine by string index (property 0x64182123), so that index is remapped into the
  player package's string table. Its aggro range (0x12c337f7, default 300) is raised to AGGRO_RANGE:
  with the default, the `_capture` AI only chases an enemy within ~260, well inside a player ship's
  weapon range, so it looked blind until shot at.
- Looks: a player prefab is built with placeholder assets (needle/ships/dummyrace/dummycategory/...)
  that the game replaces at spawn with the player's paint package (ship_race<r>_<class>_paint0000_package.cgpr
  lists the real model, skin, collision and material). Here the placeholders are pointed at the real
  assets directly, with paint 0000.

Usage: python -I tools/mod_ai_pilots.py [EXTRACTED_DIR] [AI_TEAMS_GAMEDATA] [OUT_GAMEDATA_DIR]
  defaults: game/extracted, game/mods/ai_teams/GameData -> game/mods/ai_pilots/GameData
Install it over ai_teams (it contains the layout too).
"""
import re
import struct
import sys
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cgpr  # noqa: E402

LAYOUT = "needle/space/regions/layouts/conquest/_win/region_conquest_3_crown.cgpr"
RACES = "abcde"
AI_TEMPLATE = re.compile(r"^\$/needle/ships/ai/(scout|raider|frigate)/script/_win/ship_ai_\w+?_(\w+)_capture\.cgpr$")
STATE_MACHINE = 0x64182123  # StateMachine/Animated: state machine path (string index)
AGGRO_RANGE_PROP = 0x12c337f7  # NeedleController/AI: threat range
AGGRO_RANGE = 600.0  # = the ships' scanner range, so they engage what their team can see
SUB_AI, SUB_ANIMATED, SUB_PLAYER = (zlib.crc32(n) for n in (b"AI", b"Animated", b"Player"))
DUMMY = "needle/ships/dummyrace/dummycategory/"


def subtype(c):
    d = cgpr.decode_body(c["body"])
    return d and d["subtype"]


def root(pkg):
    return next(o for o in pkg["objects"] if o["name"] == "Ship (Base)")


def pilot(player, ai):
    """Swap player's Player controller for ai's AI controller and state machine (in place)."""
    controllers = [c for c in root(ai)["components"] if subtype(c) in (SUB_AI, SUB_ANIMATED)]
    assert len(controllers) == 2, len(controllers)
    for k, c in enumerate(controllers):
        if subtype(c) == SUB_AI:
            controllers[k] = dict(c, body=cgpr.set_prop(c["body"], AGGRO_RANGE_PROP, struct.pack("<f", AGGRO_RANGE)))
        if subtype(c) != SUB_ANIMATED:
            continue
        prop = next(p for p in cgpr.decode_body(c["body"])["props"] if p["hash"] == STATE_MACHINE)
        path = ai["strings"][struct.unpack_from("<i", c["body"], prop["start"])[0]]
        if path not in player["strings"]:
            player["strings"].append(path)
        body = bytearray(c["body"])
        body[prop["start"]:prop["end"]] = struct.pack("<i", player["strings"].index(path))
        controllers[k] = dict(c, body=bytes(body))
    comps = root(player)["components"]
    i = next(k for k, c in enumerate(comps) if subtype(c) == SUB_PLAYER)
    comps[i:i + 1] = controllers


def dress(player, race, cls):
    """Point the placeholder assets at race/cls's real ones (paint 0000)."""
    real = f"needle/ships/race{race}/{cls}/"
    for i, s in enumerate(player["strings"]):
        if DUMMY in s:
            s = s.replace(DUMMY, real).replace("ship_dummyrace_dummycategory", f"ship_race{race}_{cls}")
            player["strings"][i] = s.replace("_dummyskin.cmat", "_paint0000.cmat")


def main():
    src = Path(sys.argv[1] if len(sys.argv) > 1 else "game/extracted")
    teams = Path(sys.argv[2] if len(sys.argv) > 2 else "game/mods/ai_teams/GameData")
    out = Path(sys.argv[3] if len(sys.argv) > 3 else "game/mods/ai_pilots/GameData")
    layout = cgpr.parse((teams / LAYOUT).read_bytes())
    used = {}  # class -> ships made so far
    for i, s in enumerate(layout["strings"]):
        m = AI_TEMPLATE.match(s)
        if not m:
            continue
        cls, role = m.groups()
        race = RACES[used.get(cls, 0) % len(RACES)]
        used[cls] = used.get(cls, 0) + 1
        base = f"needle/ships/race{race}/{cls}/script/_win/ship_race{race}_{cls}"
        player = cgpr.parse((src / f"{base}.cgpr").read_bytes())
        pilot(player, cgpr.parse((src / s[2:]).read_bytes()))
        dress(player, race, cls)
        for path in player["strings"]:  # every asset it now names must exist
            if path.startswith("$/") and "." in path and not (src / path[2:]).exists():
                print(f"  warning: {path} not found")
        rel = f"{base}_ai_{role}.cgpr"
        (out / rel).parent.mkdir(parents=True, exist_ok=True)
        (out / rel).write_bytes(cgpr.build(player))
        layout["strings"][i] = "$/" + rel
        print(f"{s[2:]} -> {rel}")
    (out / LAYOUT).parent.mkdir(parents=True, exist_ok=True)
    (out / LAYOUT).write_bytes(cgpr.build(layout))
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
