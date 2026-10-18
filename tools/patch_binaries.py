"""Apply byte patches to the binaries in game/<instance>/Bin64 (starting from game/original each time).

Usage: python -I tools/patch_binaries.py [--services-url URL] [--instance NAME] [--real-listen]
                                         [--dedicated] [--no-widescreen]

Patches:
  services-url  NeedleCommon: replace the hard-coded web services base URL
                https://deadstar.rel.armature.com/ (the release build ignores the
                needle_publicServiceUrlOverride script var). Default http://127.0.0.1:28080/, which is
                tools/web_services.py. At most 39 characters, must end with '/'.
  real-listen   GameComponentsNeedle: the shipped server.cgpr starts the server in a single-player
                mode that takes no network connections. Make it always listen on UDP 27500 instead,
                so the server accepts network clients. Only needed on a server instance.
  dedicated     GameComponentsNeedle: once listening, skip logging in and connecting the server's own
                local player, so the server only hosts. Implies real-listen.
  widescreen    Renderer (on by default): the renderer forces a 16:9 aspect ratio, render target and
                viewport, so 21:9 screens get black bars. Turn that off.
"""
import argparse
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ORIGINAL = REPO / "game" / "original" / "Bin64"

SERVICES_DLL = "NeedleCommon_Steam_Release.dll"
SERVICES_URL = b"https://deadstar.rel.armature.com/"
SERVICES_URL_SPACE = 40  # string plus zero padding before the next literal, including terminator

COMPONENTS_DLL = "GameComponentsNeedle_Steam_Release.dll"
RENDERER_DLL = "Renderer_Steam_Release.dll"
# (file offset, original bytes, patched bytes, description)
REAL_LISTEN = (0x1717AB, bytes.fromhex("7438"), bytes.fromhex("eb38"),
               "always listen on the network")
DEDICATED = (0x171825, bytes.fromhex("0f840ffeffff"), bytes.fromhex("e910feffff90"),
             "no local player on the server")
WIDESCREEN = (0x38F84, bytes.fromhex("c6869103000001"), bytes.fromhex("c6869103000000"),
              "don't force 16:9")


def patch_services_url(data, url):
    raw = url.encode("ascii")
    if len(raw) >= SERVICES_URL_SPACE or not url.endswith("/"):
        raise SystemExit(f"services URL must end with '/' and be under {SERVICES_URL_SPACE} chars")
    at = data.find(SERVICES_URL + b"\0")
    if at < 0 or data.count(SERVICES_URL) != 1:
        raise SystemExit(f"{SERVICES_DLL}: original services URL not found exactly once")
    if data[at + len(SERVICES_URL):at + SERVICES_URL_SPACE] != b"\0" * (SERVICES_URL_SPACE - len(SERVICES_URL)):
        raise SystemExit(f"{SERVICES_DLL}: unexpected bytes after services URL")
    data[at:at + SERVICES_URL_SPACE] = raw.ljust(SERVICES_URL_SPACE, b"\0")
    print(f"{SERVICES_DLL}: services URL at file offset {at:#x} -> {url}")


def patch_bytes(name, data, patch):
    offset, old, new, desc = patch
    if data[offset:offset + len(old)] != old:
        raise SystemExit(f"{name}: unexpected bytes at {offset:#x}: {data[offset:offset + len(old)].hex()}")
    data[offset:offset + len(new)] = new
    print(f"{name}: {offset:#x} {old.hex()} -> {new.hex()} ({desc})")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--services-url", default="http://127.0.0.1:28080/")
    ap.add_argument("--instance", default="work", help="game/<instance> to patch (default work)")
    ap.add_argument("--real-listen", action="store_true", help="server: listen on the network")
    ap.add_argument("--dedicated", action="store_true", help="server: no local player (implies --real-listen)")
    ap.add_argument("--no-widescreen", action="store_true", help="keep the 16:9 clamp (black bars)")
    args = ap.parse_args()
    args.real_listen |= args.dedicated
    work = REPO / "game" / args.instance / "Bin64"
    work.mkdir(parents=True, exist_ok=True)

    data = bytearray((ORIGINAL / SERVICES_DLL).read_bytes())
    patch_services_url(data, args.services_url)
    (work / SERVICES_DLL).write_bytes(data)

    data = bytearray((ORIGINAL / COMPONENTS_DLL).read_bytes())
    if args.real_listen:
        patch_bytes(COMPONENTS_DLL, data, REAL_LISTEN)
    if args.dedicated:
        patch_bytes(COMPONENTS_DLL, data, DEDICATED)
    (work / COMPONENTS_DLL).write_bytes(data)

    data = bytearray((ORIGINAL / RENDERER_DLL).read_bytes())
    if not args.no_widescreen:
        patch_bytes(RENDERER_DLL, data, WIDESCREEN)
    (work / RENDERER_DLL).write_bytes(data)
    print(f"Wrote {work}")


if __name__ == "__main__":
    main()
