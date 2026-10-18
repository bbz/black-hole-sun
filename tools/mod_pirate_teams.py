"""Build a mod that turns the Conquest pirate events into AI vs AI: each event's AI squads alternate
between team 0 (Needle) and team 1 (Hunter) instead of team 2 (Old Empire). They alternate because
the event spawns squads in order (its "SET - N" picks how many), so any set gets both teams.
Each event normally picks one of its four squads at random (a Logic/RandomSwitch, logged as
"SET - N"); that switch is replaced by a General/Relay with the same id and links, so all four
squads spawn. The pirate ship templates are also swapped for their "_capture" variants (as Freeplay uses), which
go and capture outposts instead of patrolling around their spawn point.

Usage: python -I tools/mod_pirate_teams.py [EXTRACTED_DIR] [OUT_GAMEDATA_DIR]
  defaults: game/extracted -> game/mods/ai_pirate_teams/GameData

Force the pirate event in a match with script vars (server side):
  -vc needle_StartRegion=4 -vc needle_EventOptions=4 -vc needle_EventChanceOverride=100
  -vc needle_EventInstantStart=1 -vc needle_EventNeverStop=1
(region 4 is Conquest 3 Crown; needle_EventOptions N forces event N-1, Pirates is event 3; the last
two are read by the event's Lua conditions and start it at once instead of after its timer.)

Teams are hashed names in NeedleGeneral/Owner.Team:
  0x4141138b team 0, 0x6ece7deb team 1, 0x2dd63ff8 team 2 (Old Empire), 0xae005226 team 3,
  0xf1bdcd56 none.
"""
import struct
import sys
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cgpr

TEAM_HASH = {0: 0x4141138B, 1: 0x6ECE7DEB, 2: 0x2DD63FF8}
EVENTS = "needle/space/regions/events/conquest/_win"
SQUAD = "AI - SquadManager"
RANDOM_SWITCH_EVENT = bytes.fromhex("f82a1935")  # link event of Logic/RandomSwitch outputs
RELAY_EVENT = bytes.fromhex("1c8ed70e")  # link event of General/Relay outputs


def objects(objs):
    for o in objs:
        yield o
        yield from objects(o["children"])


def random_switch_to_relay(pkg):
    """Replace each Logic/RandomSwitch with a General/Relay that fires all its outputs."""
    relay = next(c for o in objects(pkg["objects"]) for c in o["components"]
                 if (d := cgpr.decode_body(c["body"])) and d["subtype"] == zlib.crc32(b"Relay"))
    changed = 0
    for o in objects(pkg["objects"]):
        for c in o["components"]:
            d = cgpr.decode_body(c["body"])
            if not d or d["subtype"] != zlib.crc32(b"RandomSwitch"):
                continue
            tail = d["tail"].replace(RANDOM_SWITCH_EVENT, RELAY_EVENT)
            head = struct.pack("<IHB", zlib.crc32(b"Relay"), 19, d["version"])  # 19: no properties
            c["type"] = relay["type"]
            c["body"] = head + bytes.fromhex(d["guid"]) + struct.pack("<H", 0) + tail
            changed += 1
    return changed


def main():
    src = Path(sys.argv[1] if len(sys.argv) > 1 else "game/extracted")
    out = Path(sys.argv[2] if len(sys.argv) > 2 else "game/mods/ai_pirate_teams/GameData")
    for f in sorted((src / EVENTS).glob("*_event_pirates.cgpr")):
        pkg = cgpr.parse(f.read_bytes())
        squads = sum(1 for _ in cgpr.find_targets(pkg, f"{SQUAD}/NeedleGeneral/Owner/Team"))
        for n in range(1, squads + 1):
            team = (n - 1) % 2
            for comp, prop in cgpr.find_targets(pkg, f"{SQUAD}#{n}/NeedleGeneral/Owner/Team"):
                body = bytearray(comp["body"])
                body[prop["start"]:prop["end"]] = TEAM_HASH[team].to_bytes(4, "little")
                comp["body"] = bytes(body)
        switches = random_switch_to_relay(pkg)
        for i, t in enumerate(pkg["strings"]):
            if t.startswith("$/needle/ships/ai/") and t.endswith(".cgpr") and "_capture" not in t:
                pkg["strings"][i] = t[:-len(".cgpr")] + "_capture.cgpr"
        dest = out / EVENTS / f.name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(cgpr.build(pkg))
        print(f"{f.name}: {squads} squads, odd -> team 0, even -> team 1, {switches} random switch -> relay")


if __name__ == "__main__":
    main()
