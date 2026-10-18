"""Build a mod that adds an AI squad to each team of a Conquest map, for AI vs AI matches.

The squads are copied from the Freeplay map (region_freeplay_3_circle), which has two
self-contained AI groups ("AI_Group_North_01", "AI_Group_South_01": a NeedleAIManager/Squad with a
NeedleGeneral/Spawner, patrol points and spawn points) plus the templates they spawn ("AI - Template
- LOAD SGPR - ...", the "_capture" AI ship variants that go and take outposts). They are added to the
Conquest layout, moved in front of each team's base and given that team. A group spawns its
squad when its root relay gets message 0xd5d636c1; in Freeplay that comes from "Logic_Recapture"
(a timer plus switches tied to Freeplay's sector captures), so its timer is copied too, without the
switches, with both its timers (one per group) set to start on their own. Map events such as
pirates are untouched and still spawn as team 2.

Where a squad goes: its leader patrols a ring of NeedleWaypoint/PatrolPoints (each links to the
next; WP 01 -> 02 -> 03 -> 04 -> 01). Nothing links the squad to its ring: when it has no current
waypoint, the squad takes the patrol point nearest to its leader, from any ring on the map.
Conquest base sectors have their own defender rings within ~200 of the base, so the spawn cluster sits FRONT_OF_BASE in front of the base, well clear of them, with
WP 01 on it. The rest of the ring is the route: WP 02 on the objective, WP 03 in front of the enemy
base, WP 04 back on the objective, then home to WP 01.

Usage: python -I tools/mod_ai_teams.py [LAYOUT] [EXTRACTED_DIR] [OUT_GAMEDATA_DIR]
  LAYOUT defaults to region_conquest_3_crown (region id 4, force with -vc needle_StartRegion=4)
  defaults: game/extracted -> game/mods/ai_teams/GameData

Teams are hashed names in NeedleGeneral/Owner.Team and NeedleGalaxy/Sector.InitialOwner:
  0x4141138b team 0, 0x6ece7deb team 1, 0x2dd63ff8 team 2 (Old Empire).
"""
import copy
import math
import struct
import sys
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cgpr

TEAM_HASH = {0: 0x4141138B, 1: 0x6ECE7DEB}
LAYOUTS = "needle/space/regions/layouts"
FREEPLAY = f"{LAYOUTS}/freeplay/_win/region_freeplay_3_circle.cgpr"
GROUPS = ["AI_Group_South_01", "AI_Group_North_01"]  # -> team 0, team 1
TEMPLATE_PREFIX = "AI - Template - "
SPAWN_PREFIX = "AI - SpawnPoint"
WAYPOINT_PREFIX = "AI - Squad - WP"
OBJECTIVE = (0.0, 0.0)  # (x, z) the squads fight over: the centre sector
TRIGGER = "Logic_Recapture"  # its BasicTimer fires the groups' relays
FRONT_OF_BASE = 700.0  # distance from the base sector centre towards the map centre


def objects(objs):
    for o in objs:
        yield o
        yield from objects(o["children"])


def prop(comp, path):
    """(start, end) of the property at path (names) in a component body, or None."""
    dec = cgpr.decode_body(comp["body"])
    props = dec["props"] if dec else None
    for part in path:
        found = [p for p in props or [] if p["hash"] == zlib.crc32(part.encode())]
        if not found:
            return None
        if part == path[-1]:
            return found[0]["start"], found[0]["end"]
        props = found[0]["children"]


def component(obj, subtype):
    for c in obj["components"]:
        dec = cgpr.decode_body(c["body"])
        if dec and dec["subtype"] == zlib.crc32(subtype.encode()):
            return c


def patch(comp, span, raw):
    body = bytearray(comp["body"])
    body[span[0]:span[1]] = raw
    comp["body"] = bytes(body)


def position(obj):
    c = component(obj, "BaseGameProperties")
    span = prop(c, ["BaseProperties", "Transform"]) if c else None
    return (c, span, struct.unpack_from("<12f", c["body"], span[0])) if span else (None, None, None)


def move_to(obj, x, z):
    c, span, m = position(obj)
    patch(c, span, struct.pack("<12f", *m[:9], x, m[10], z))


def front_of(base):
    bx, bz = base
    d = math.hypot(bx, bz) or 1.0
    return bx - bx / d * FRONT_OF_BASE, bz - bz / d * FRONT_OF_BASE


def translate(root, dx, dz):
    for o in objects([root]):
        c, span, m = position(o)
        if span:
            m = list(m)
            m[9] += dx
            m[11] += dz
            patch(c, span, struct.pack("<12f", *m))


def base_positions(pkg):
    """Team -> (x, z) of the sector it initially owns."""
    bases = {}
    for o in objects(pkg["objects"]):
        sector = component(o, "Sector")
        span = prop(sector, ["InitialOwner"]) if sector else None
        if not span:
            continue
        owner = struct.unpack_from("<I", sector["body"], span[0])[0]
        for team, h in TEAM_HASH.items():
            if owner == h:
                m = position(o)[2]
                bases[team] = (m[9], m[11])
    return bases


def main():
    layout = sys.argv[1] if len(sys.argv) > 1 else "region_conquest_3_crown"
    src = Path(sys.argv[2] if len(sys.argv) > 2 else "game/extracted")
    out = Path(sys.argv[3] if len(sys.argv) > 3 else "game/mods/ai_teams/GameData")
    rel = f"{LAYOUTS}/conquest/_win/{layout}.cgpr"
    pkg = cgpr.parse((src / rel).read_bytes())
    free = cgpr.parse((src / FREEPLAY).read_bytes())
    bases = base_positions(pkg)
    if set(bases) != {0, 1}:
        raise SystemExit(f"{layout}: expected a base sector for teams 0 and 1, found {bases}")

    for o in free["objects"]:
        if not o["name"].startswith(TEMPLATE_PREFIX):
            continue
        t = copy.deepcopy(o)
        loader = component(t, "AutoLoadPackage")
        span = prop(loader, ["ScriptPackage"])
        path = free["strings"][struct.unpack_from("<i", loader["body"], span[0])[0]]
        if path not in pkg["strings"]:
            pkg["strings"].append(path)
        patch(loader, span, struct.pack("<i", pkg["strings"].index(path)))
        pkg["objects"].append(t)

    for team, name in enumerate(GROUPS):
        group = copy.deepcopy(next(o for o in free["objects"] if o["name"] == name))
        tx, tz = front_of(bases[team])
        ex, ez = front_of(bases[1 - team])
        gx, gz = position(group)[2][9], position(group)[2][11]
        translate(group, tx - gx, tz - gz)  # manager, waypoints and spawn points keep their layout
        spawns = [o for o in group["children"] if o["name"].startswith(SPAWN_PREFIX)]
        cx = sum(position(o)[2][9] for o in spawns) / len(spawns)
        cz = sum(position(o)[2][11] for o in spawns) / len(spawns)
        for o in spawns:
            translate(o, tx - cx, tz - cz)
        route = {"01": (tx, tz), "02": OBJECTIVE, "03": (ex, ez), "04": OBJECTIVE}
        for o in group["children"]:
            if o["name"].startswith(WAYPOINT_PREFIX):
                move_to(o, *route[o["name"].split()[-1]])
        for o in objects([group]):
            owner = component(o, "Owner")
            span = prop(owner, ["Team"]) if owner else None
            if span:
                patch(owner, span, struct.pack("<I", TEAM_HASH[team]))
        pkg["objects"].append(group)
        print(f"{name} -> team {team}: spawns and WP 01 at ({tx:.0f}, {tz:.0f}), WP 02/04 at "
              f"({OBJECTIVE[0]:.0f}, {OBJECTIVE[1]:.0f}), WP 03 at ({ex:.0f}, {ez:.0f})")

    trigger = copy.deepcopy(next(o for o in free["objects"] if o["name"] == TRIGGER))
    trigger["components"] = [c for c in trigger["components"]
                             if component({"components": [c]}, "BaseGameProperties")
                             or component({"components": [c]}, "BasicTimer")]
    for timer in trigger["components"]:  # one timer per group
        if component({"components": [timer]}, "BasicTimer"):
            patch(timer, prop(timer, ["AutoStart"]), b"\x01")
    pkg["objects"].append(trigger)
    print(f"{TRIGGER}: timers only, AutoStart on")

    dest = out / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(cgpr.build(pkg))
    print(f"wrote {dest}")


if __name__ == "__main__":
    main()
