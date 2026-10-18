"""Print the state of every AI squad and AI-controlled ship in a running Dead Star instance.

Reads the process memory (read-only) of game/<instance>/Bin64/DeadStar.exe, finds component objects
by their type in GameComponentsNeedle_Steam_Release.dll, and prints selected fields. Offsets are
from the REL-147994 build. Scanning takes a minute or two.

Usage: python -I tools/ai_state.py [INSTANCE]   (default server)
"""
import ctypes
import ctypes.wintypes as wt
import struct
import sys

IMAGE_BASE = 0x180000000
DLL = "GameComponentsNeedle_Steam_Release.dll"
VT_SQUAD = 0x1802C2390  # AI squad
VT_AI = 0x1802BEEA0  # AI ship controller
VT_PATROL = 0x1802C20B0  # patrol point
# A component's game object is at +0x10, and the object holding its transform at +0x1b8 in that.
# The transform (3x4 floats: rotation, then position) is at this offset in it:
TRANSFORM_AT = {VT_AI: 0x90, VT_PATROL: 0x88}
ACTIONS = {0: "inactive", 1: "idle", 2: "patrol", 3: "follow-leader", 4: "chase"}

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
psapi = ctypes.WinDLL("psapi", use_last_error=True)


class MBI(ctypes.Structure):
    _fields_ = [("BaseAddress", ctypes.c_uint64), ("AllocationBase", ctypes.c_uint64),
                ("AllocationProtect", wt.DWORD), ("PartitionId", wt.WORD), ("RegionSize", ctypes.c_uint64),
                ("State", wt.DWORD), ("Protect", wt.DWORD), ("Type", wt.DWORD)]


def find_process(instance):
    import subprocess
    out = subprocess.run(["powershell", "-NoProfile", "-Command",
                          f"(Get-Process DeadStar | Where-Object {{ $_.Path -like '*\\game\\{instance}\\Bin64\\*' }}).Id"],
                         capture_output=True, text=True).stdout.split()
    if not out:
        raise SystemExit(f"no DeadStar process for game/{instance}")
    return int(out[0])


def module_base(h, name):
    mods = (ctypes.c_void_p * 1024)()
    need = wt.DWORD()
    psapi.EnumProcessModulesEx(h, mods, ctypes.sizeof(mods), ctypes.byref(need), 3)
    buf = ctypes.create_unicode_buffer(260)
    for m in mods[:need.value // 8]:
        psapi.GetModuleBaseNameW(h, ctypes.c_void_p(m), buf, 260)
        if buf.value.lower() == name.lower():
            return m
    raise SystemExit(f"{name} not loaded")


class Mem:
    def __init__(self, pid):
        self.h = k32.OpenProcess(0x0410, False, pid)  # QUERY_INFORMATION | VM_READ
        if not self.h:
            raise SystemExit(f"OpenProcess failed: {ctypes.get_last_error()}")

    def read(self, addr, n):
        buf = ctypes.create_string_buffer(n)
        got = ctypes.c_size_t()
        if not k32.ReadProcessMemory(self.h, ctypes.c_void_p(addr), buf, n, ctypes.byref(got)):
            return None
        return buf.raw[:got.value]

    def regions(self):
        addr, mbi = 0, MBI()
        while k32.VirtualQueryEx(self.h, ctypes.c_void_p(addr), ctypes.byref(mbi), ctypes.sizeof(mbi)):
            # committed, private, read/write
            if mbi.State == 0x1000 and mbi.Type == 0x20000 and mbi.Protect & 0x04:
                yield mbi.BaseAddress, mbi.RegionSize
            addr = mbi.BaseAddress + mbi.RegionSize
            if addr >= 1 << 47:
                break

    def scan(self, values):
        """Addresses (8-aligned) of qwords equal to any of values -> {value: [addr]}."""
        want = {struct.pack("<Q", v): v for v in values}
        hits = {v: [] for v in values}
        for base, size in self.regions():
            data = self.read(base, size)
            if not data:
                continue
            for pat, v in want.items():
                i = data.find(pat)
                while i >= 0:
                    if i % 8 == 0:
                        hits[v].append(base + i)
                    i = data.find(pat, i + 1)
        return hits


def uid(raw, off):
    return raw[off:off + 16].hex()


def short(raw, off):
    """Component unique id as index (the rest is a generation and a constant tag)."""
    i, gen = struct.unpack_from("<II", raw, off)
    return "-" if i == 0xffffffff or (i == 0 and gen == 0) else f"{i:#x}"


def pos(mem, comp, vt):
    go = struct.unpack("<Q", mem.read(comp + 0x10, 8))[0]
    obj = struct.unpack("<Q", mem.read(go + 0x1b8, 8))[0]
    x, y, z = struct.unpack("<3f", mem.read(obj + TRANSFORM_AT[vt] + 36, 12))
    return f"({x:.0f},{z:.0f})"


def main():
    instance = sys.argv[1] if len(sys.argv) > 1 else "server"
    pid = find_process(instance)
    mem = Mem(pid)
    base = module_base(mem.h, DLL)
    vt_squad, vt_ai, vt_patrol = (base + v - IMAGE_BASE for v in (VT_SQUAD, VT_AI, VT_PATROL))
    hits = mem.scan([vt_squad, vt_ai, vt_patrol])
    patrol = {}
    for a in hits[vt_patrol]:
        patrol[short(mem.read(a, 0x28), 0x18)] = pos(mem, a, VT_PATROL)
    print(f"pid {pid}: {len(hits[vt_squad])} squads, {len(hits[vt_ai])} AI controllers")
    for a in hits[vt_squad]:
        r = mem.read(a, 0x120)
        props = struct.unpack_from("<Q", r, 0x80)[0]
        p = mem.read(props, 0x90) or bytes(0x90)
        members = (struct.unpack_from("<Q", r, 0xb0)[0] - struct.unpack_from("<Q", r, 0xa8)[0]) // 16
        wp = short(r, 0x100)
        print(f"squad {short(r, 0x18)}: members {members} leader {short(r, 200)} "
              f"follow {short(r, 0xe0)} waypoint {wp} at {patrol.get(wp, '?')} "
              f"| patrol={p[0x7c]} autofollow={p[0x7d]}")
    for a in hits[vt_ai]:
        r = mem.read(a, 0x420)
        cur, nxt = struct.unpack_from("<ii", r, 0x244)
        print(f"ai {short(r, 0x18)}: squad {short(r, 0x250)} at {pos(mem, a, VT_AI)} "
              f"action {ACTIONS.get(cur, cur)} (next {ACTIONS.get(nxt, nxt)}) active={r[0x144]} "
              f"idle={r[0x408]} target {short(r, 0x168)}")


if __name__ == "__main__":
    main()
