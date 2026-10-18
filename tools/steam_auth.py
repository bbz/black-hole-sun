"""Check Steam auth session tickets with the Steam client: is the ticket genuine, and does the
account it names own Dead Star (its own license, not one borrowed through Family Sharing)?

Used by tools/web_services.py on every login. It loads the game's own steam_api64.dll from
game/original/Bin64 and initialises it as app 366400, so Steam must be running and signed in, and
asks Steam through ISteamUser::BeginAuthSession, the same check a game server makes for a player
who joins. A ticket that fails, or no answer within TIMEOUT seconds, is refused.

Usage (self-test: makes a ticket for this Steam account, checks it, then checks a forged one):
  python -I tools/steam_auth.py
"""
import ctypes
import os
import struct
import threading
import time
from pathlib import Path

APP_ID = 366400
DLL = Path(__file__).resolve().parent.parent / "game" / "original" / "Bin64" / "steam_api64.dll"
TIMEOUT = 10.0
STALE_FOR = 60.0  # see SteamAuth.stale
VALIDATE_AUTH_TICKET_RESPONSE = 143  # ValidateAuthTicketResponse_t: k_iSteamUserCallbacks + 43
BEGIN_RESULTS = {0: "ok", 1: "invalid ticket", 2: "duplicate request", 3: "invalid version",
                 4: "ticket for another game", 5: "expired ticket"}
AUTH_RESPONSES = {0: "ok", 1: "user not connected to Steam", 2: "no license or expired",
                  3: "VAC banned", 4: "logged in elsewhere", 5: "VAC check timed out",
                  6: "ticket canceled", 7: "ticket already used", 8: "ticket invalid",
                  9: "publisher issued ban"}

# CCallbackBase as MSVC lays it out: vtable, u8 flags, int callback id. MSVC puts overloaded
# virtuals in reverse order, so the vtable is Run(param, io_failure, call), Run(param),
# GetCallbackSizeBytes().
RUN_RESULT = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_bool, ctypes.c_uint64)
RUN = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p)
SIZE = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p)


class VTable(ctypes.Structure):
    _fields_ = [("run_result", RUN_RESULT), ("run", RUN), ("size", SIZE)]


class Callback(ctypes.Structure):
    _fields_ = [("vtable", ctypes.POINTER(VTable)), ("flags", ctypes.c_uint8), ("id", ctypes.c_int)]


class SteamAuth:
    def __init__(self):
        os.environ["SteamAppId"] = str(APP_ID)  # in place of steam_appid.txt
        os.add_dll_directory(str(DLL.parent))
        self.api = api = ctypes.CDLL(str(DLL))
        api.SteamAPI_Init.restype = ctypes.c_bool
        if not api.SteamAPI_Init():
            raise RuntimeError("SteamAPI_Init failed: is Steam running and signed in, and does it own Dead Star?")
        api.SteamUser.restype = ctypes.c_void_p
        self.user = api.SteamUser()
        api.SteamAPI_ISteamUser_BeginAuthSession.restype = ctypes.c_int
        api.SteamAPI_ISteamUser_BeginAuthSession.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int,
                                                              ctypes.c_uint64]
        api.SteamAPI_ISteamUser_EndAuthSession.argtypes = [ctypes.c_void_p, ctypes.c_uint64]
        api.SteamAPI_ISteamUser_GetSteamID.restype = ctypes.c_uint64
        api.SteamAPI_ISteamUser_GetSteamID.argtypes = [ctypes.c_void_p]
        api.SteamAPI_ISteamUser_GetAuthSessionTicket.restype = ctypes.c_uint32
        api.SteamAPI_ISteamUser_GetAuthSessionTicket.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int,
                                                                  ctypes.POINTER(ctypes.c_uint32)]
        api.SteamAPI_RegisterCallback.argtypes = [ctypes.c_void_p, ctypes.c_int]
        self.lock = threading.Lock()  # one check at a time; callbacks run on the checking thread
        self.answers = {}  # steam id -> (response, owner steam id)
        # steam id -> times of checks that timed out. Steam's late answer to one of those would look
        # like the answer to the next check for that id, so it's dropped (answers come in order).
        # Steam may never send it, so a timeout is forgotten after STALE_FOR seconds.
        self.stale = {}
        self.vtable = VTable(RUN_RESULT(lambda this, param, failed, call: self.on_response(param)),
                             RUN(lambda this, param: self.on_response(param)), SIZE(lambda this: 20))
        self.callback = Callback(ctypes.pointer(self.vtable), 0, VALIDATE_AUTH_TICKET_RESPONSE)
        api.SteamAPI_RegisterCallback(ctypes.byref(self.callback), VALIDATE_AUTH_TICKET_RESPONSE)

    def on_response(self, param):
        # ValidateAuthTicketResponse_t, packed to 4: u64 steam id, int response, u64 owner steam id
        sid, response, owner = struct.unpack("<QiQ", ctypes.string_at(param, 20))
        now = time.monotonic()
        timeouts = [t for t in self.stale.pop(sid, []) if now - t < STALE_FOR]
        if timeouts:
            if timeouts[1:]:
                self.stale[sid] = timeouts[1:]
            return
        self.answers[sid] = (response, owner)

    def check(self, ticket, sid):
        """None if Steam confirms ticket belongs to sid and sid owns the game, else the reason it
        doesn't."""
        with self.lock:
            self.api.SteamAPI_RunCallbacks()  # take any answer already waiting before this check starts
            self.answers.pop(sid, None)
            begin = self.api.SteamAPI_ISteamUser_BeginAuthSession(self.user, ticket, len(ticket), sid)
            if begin:
                return BEGIN_RESULTS.get(begin, f"BeginAuthSession {begin}")
            try:
                deadline = time.monotonic() + TIMEOUT
                while sid not in self.answers:
                    if time.monotonic() > deadline:
                        self.stale.setdefault(sid, []).append(time.monotonic())
                        return "no answer from Steam"
                    self.api.SteamAPI_RunCallbacks()
                    time.sleep(0.05)
                response, owner = self.answers.pop(sid)
                if response:
                    return AUTH_RESPONSES.get(response, f"response {response}")
                if owner != sid:  # a license borrowed through Steam Family Sharing
                    return f"borrowed from {owner} through Family Sharing; the account has to own the game"
                return None
            finally:
                self.api.SteamAPI_ISteamUser_EndAuthSession(self.user, sid)

    def own_ticket(self):
        buf, size = ctypes.create_string_buffer(1024), ctypes.c_uint32()
        self.api.SteamAPI_ISteamUser_GetAuthSessionTicket(self.user, buf, len(buf), ctypes.byref(size))
        return buf.raw[:size.value]


def main():
    auth = SteamAuth()
    sid = auth.api.SteamAPI_ISteamUser_GetSteamID(auth.user)
    ticket = auth.own_ticket()
    time.sleep(1)  # a new ticket takes a moment to register with Steam
    print(f"own ticket ({len(ticket)} bytes) for {sid}: {auth.check(ticket, sid) or 'accepted'}")
    forged = bytearray(ticket)
    forged[-8] ^= 0xff  # the signature
    print(f"forged ticket: {auth.check(bytes(forged), sid) or 'accepted'}")
    print(f"ticket presented as another account: {auth.check(ticket, sid + 1) or 'accepted'}")


if __name__ == "__main__":
    main()
