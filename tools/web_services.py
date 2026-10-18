"""Local stand-in for Armature's Dead Star web services (deadstar.rel.armature.com, offline since
the servers shut down).

Usage: python -I tools/web_services.py [PORT]   (default 28080)
Setup: python -I tools/patch_binaries.py      (points the game at http://127.0.0.1:28080/)
Then:  powershell -File tools/run_work.ps1

Requests look like
  GET /api/public/v1/NeedleAccount/?PlatformAuth=<steam ticket>&ClientVersion=REL-147994&...
Every request is logged to stdout and game/work/Log/web_services.log (full bodies in
web_services_bodies.log). Unknown endpoints get `{}`, so the game's own log
(game/work/Log/GameEventLog.txt) reports which JSON members it wanted: look for
'JSON object has no member "X"'.

Logins are checked with Steam (tools/steam_auth.py): the ticket the game sends has to be genuine
and its account has to own Dead Star, or the login is refused. So Steam must be running and signed
in, and the server won't start without it.

Player data is stored per Steam ID in game/work/Server/players/<steamid>.json. The game PUTs its
(partial) player data to NeedlePlayerDataClient, which is merged into the stored copy.

Response formats are the ones the REL-147994 client expects.
"""
import copy
import hashlib
import json
import re
import struct
import sys
import threading
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ara  # noqa: E402
import steam_auth  # noqa: E402

GAME = Path(__file__).resolve().parent.parent / "game"
WORK = GAME / "work"
ARCHIVE = GAME / "original" / "GameData" / "gamedata.ara"
SKIN_MASTERLIST = "/needle/ships/masterlist/_win/skin_masterlist.cgpr"
LOG = WORK / "Log" / "web_services.log"
BODY_LOG = WORK / "Log" / "web_services_bodies.log"
PLAYERS = WORK / "Server" / "players"
SESSIONS = WORK / "Server" / "sessions.json"

# ship_masterlist.cgpr order: races A-E scouts, then raiders, then frigates
SHIP_TYPES = range(15)

DEFAULT_PLAYER_DATA = {
    "CorePlayerDataEntry": {
        "Version": 1,
        "Level": 1,
        "Fame": 0,
        "CurrentPortraitId": 0,
        "Slot_1_ShipType": 0,
        "Slot_2_ShipType": 5,
        "Slot_3_ShipType": 10,
        "IsDevAccount": False,
    },
    "TrophyData": {
        "TotalKills": 0,
        "TotalAssists": 0,
        "TotalBaseCaptures": 0,
        "TotalOreSpent": 0,
        "TotalVictories": 0,
        "TotalDecisiveVictories": 0,
        "AceTopCaptureCount": 0,
        "AceAttackerCount": 0,
        "AceDefenderCount": 0,
        "AceUpgraderCount": 0,
        "ShipTrophyData": [],
    },
    # SkinId 0 is invalid for most ships; the game picks a default and PUTs it back
    "ShipEntries": [
        {
            "ShipType": t,
            "SkinId": 0,
            "CoreAugmentLevel": 0,
            "System_1_AugmentLevel": 0,
            "System_2_AugmentLevel": 0,
            "System_3_AugmentLevel": 0,
            "System_4_AugmentLevel": 0,
        }
        for t in SHIP_TYPES
    ],
    "SkinIds": [],  # skin_ids(), set in main()
    "PortraitIds": [0],
    "Contracts": [],
    "Augments": [],
    "Population": [],
}

# list members merged element-by-element on this key; other lists are unioned
LIST_KEYS = {
    "ShipEntries": "ShipType",
    "ShipTrophyData": "ShipType",
    "Contracts": "ContractId",
    "Augments": "AugmentId",
    "Population": "PopulationId",
}

lock = threading.Lock()  # sessions, player files and SESSIONS
auth = None  # steam_auth.SteamAuth, set in main()
# session id -> steam id, oldest first; loaded from SESSIONS in main() and saved there on each
# login, so the game's session survives a restart of this server. Each steam id keeps its newest
# SESSIONS_PER_PLAYER: a listen server and a client on one PC log in as the same account.
sessions = {}
SESSIONS_PER_PLAYER = 4


def add_session(session, sid):
    sessions.pop(session, None)
    sessions[session] = sid
    mine = [k for k, v in sessions.items() if v == sid]
    for old in mine[:-SESSIONS_PER_PLAYER]:
        del sessions[old]


def log(line, path=LOG):
    print(line, flush=True) if path == LOG else None
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def steam_id(ticket):
    """SteamID64 a Steam auth session ticket names (u64 at byte 12); steam_auth checks it."""
    try:
        return struct.unpack_from("<Q", ticket, 12)[0]
    except struct.error:
        return 0


def load_json(path, default):
    """The JSON in path, or a copy of default if it's missing, invalid or not default's type."""
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        data = None
    return data if isinstance(data, type(default)) else copy.deepcopy(default)


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(path)


def merge(stored, update, key=None):
    if isinstance(stored, dict) and isinstance(update, dict):
        for k, v in update.items():
            stored[k] = merge(stored.get(k), v, k) if k in stored else v
        return stored
    if isinstance(stored, list) and isinstance(update, list):
        id_key = LIST_KEYS.get(key)
        if id_key:
            by_id = {e.get(id_key): e for e in stored if isinstance(e, dict)}
            for e in update:
                if isinstance(e, dict) and e.get(id_key) in by_id:
                    merge(by_id[e[id_key]], e)
                else:
                    stored.append(e)
            return stored
        return stored + [e for e in update if e not in stored]
    return update


def player_path(sid):
    return PLAYERS / f"{sid}.json"


def session_player(query):
    session = query.get("SessionId", [""])[0]
    with lock:
        return sessions.get(session)


def account_response(query, body):
    ticket = query.get("PlatformAuth", [""])[0]
    try:
        raw = bytes.fromhex(ticket)
    except ValueError:
        raw = b""
    sid = steam_id(raw)
    refused = auth.check(raw, sid) if sid else "no ticket"
    if refused:
        log(f"  login refused for steam id {sid}: {refused}")
        return {}
    session = hashlib.sha256(ticket.encode()).hexdigest()  # must be exactly 64 characters
    with lock:
        add_session(session, sid)
        save_json(SESSIONS, sessions)
        data = load_json(player_path(sid), DEFAULT_PLAYER_DATA)
        save_json(player_path(sid), data)
    log(f"  login: steam id {sid}")
    return {
        "SessionId": session,
        "PlayerData": data,
        "ScriptVars": [],
        "LoginMessage": {"Id": -1, "Type": 0, "Data": "", "Title": "", "Body": ""},
    }


def player_data_response(query, body):
    sid = session_player(query)
    if sid is None:
        log("  player data: unknown session")
        return {}
    try:
        update = json.loads(body)
    except ValueError:
        update = None
    update = update.get("PlayerData", {}) if isinstance(update, dict) else None
    if not isinstance(update, dict):
        log("  player data: body isn't a JSON object with a PlayerData object; ignored")
        return {}
    with lock:
        data = merge(load_json(player_path(sid), DEFAULT_PLAYER_DATA), update)
        save_json(player_path(sid), data)
    log(f"  player data: saved {sorted(update)} for {sid}")
    return {}


def motd_response(query, body):
    return {
        "Motd": {
            "id": "1",  # digits in a string (parsed with strtoui64)
            "url": "",
            "header": "BLACK HOLE SUN",
            "body": "Running on a local stand-in server.",
            "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        }
    }


# dedicated server started with tools/run_work.ps1 -Instance server -Server
GAME_SERVER = ("127.0.0.1", 27500)


def matchmaker_response(query, body):
    # Complete=false would presumably make the client poll again.
    # RequestId is the match reservation: a GUID string (NNeedleMatchMaker::IsValidReservationId)
    return {
        "Complete": True,
        "RequestId": str(uuid.uuid4()),
        "ServerIpAddress": GAME_SERVER[0],
        "ServerPort": GAME_SERVER[1],
    }


ROUTES = {
    "NeedleAccount": account_response,
    "NeedleMessagesClient": motd_response,
    "NeedlePlayerDataClient": player_data_response,
    "NeedleMatchMaker": matchmaker_response,
}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def handle_any(self):
        url = urlparse(self.path)
        query = parse_qs(url.query)
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length).decode("utf-8", "replace") if length else ""
        # path: /api/public/v1/<Service>/<rest>
        parts = [p for p in url.path.split("/") if p]
        service = parts[3] if len(parts) > 3 else ""
        shown = {k: (v[0][:16] + "..." if k in ("PlatformAuth", "SessionId") else v[0]) for k, v in query.items()}
        stamp = f"{datetime.now():%H:%M:%S}"
        log(f"{stamp} {self.command} {url.path} {shown} body={len(body)} bytes")
        if body:
            log(f"{stamp} {self.command} {url.path}\n{body}\n", BODY_LOG)

        handler = ROUTES.get(service)
        payload = handler(query, body) if handler else {}
        if not handler:
            log(f"  unhandled service {service!r}, replying {{}}")
        data = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    do_GET = do_POST = do_PUT = do_DELETE = handle_any

    def log_message(self, *args):
        pass


def skin_ids():
    """Every skin id, in order: the ~SKIN_nnnn~ entries in the user's skin_masterlist.cgpr, read from
    game/original's archive (tools/copy_game.py)."""
    archive = ara.Archive(ARCHIVE)
    with open(ARCHIVE, "rb") as f:
        data = archive.read(f, archive.hashes.index(ara.path_hash(SKIN_MASTERLIST)))
    return list(dict.fromkeys(int(n) for n in re.findall(rb"~SKIN_(\d+)~", data)))


def main():
    global auth
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 28080
    try:
        DEFAULT_PLAYER_DATA["SkinIds"] = skin_ids()
    except (OSError, ValueError) as e:
        sys.exit(f"can't read skin ids from {ARCHIVE} (run tools/copy_game.py first): {e}")
    for s, sid in load_json(SESSIONS, {}).items():  # older versions kept every session: prune them
        add_session(s, sid)
    try:
        auth = steam_auth.SteamAuth()
    except (OSError, RuntimeError) as e:
        sys.exit(f"can't check logins with Steam: {e}")
    log(f"{datetime.now():%Y-%m-%d %H:%M:%S} listening on http://127.0.0.1:{port}/")
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
