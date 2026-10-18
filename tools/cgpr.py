"""Read and write cooked game object property packages (.cgpr, magic POSS30RV).

Usage:
  python -I tools/cgpr.py dump FILE.cgpr [--raw]  print objects, components and properties
  python -I tools/cgpr.py roundtrip FILE.cgpr     parse + rewrite, check bytes are identical
  python -I tools/cgpr.py set FILE.cgpr OUT.cgpr PATH=VALUE ...
      PATH is object/component/subtype/property[/property...], each part a name or 0xHASH;
      "Name#N" picks the Nth object with that name;
      '*' matches any object. VALUE is f:1.5 (float32), i:7 (int32), u:7, b:1 (byte),
      fl:1,0,0 (float32 list) or x:0a0b (raw hex); it must be the same size as the existing value.
      Transforms are 12 floats: 3x3 rotation (rows = local X, Y, Z axes) then position.
  python -I tools/cgpr.py replace-string FILE.cgpr OUT.cgpr OLD=NEW ...
      replace OLD with NEW inside the package's string table (resource paths, names); the table is
      rebuilt, so lengths may change
  python -I tools/cgpr.py scale-delays FILE.cgpr OUT.cgpr FACTOR
      multiply every message link delay by FACTOR (e.g. 0.05 to speed up a scripted sequence)
  python -I tools/cgpr.py scan-names [EXTRACTED_DIR] [BIN64_DIR]
      write build/crc_names.json, the names the other commands print and accept, from your copy
      of the game (defaults: game/extracted, game/original/Bin64)

Format (little-endian):
  char[4] "POSS", char[4] "30RV"
  u32 n; n x string                 dependency list (strings: LEB128 length + bytes)
  u32 m; m x named entry            (string name, u32 k, k x (string, CResId)) - unseen so far
  root: u32 child count, children
  object:
    u32 version (6), u8[16] GUID, string name, u32 unknown
    u32 component count, components
    u32 child count, children
  component:
    u32 type hash, u16 size, size bytes of body
  component body:
    u32 subtype hash, u16 size S, then S bytes: u8 version, u8[16] component GUID,
    u16 property count, properties
    then a messages-and-links section (kept raw; 10 bytes when empty)
  property:
    u32 name hash, u16 size, payload; a payload that is itself "u16 count + properties" is a
    nested property list
All hashes are CRC32 of the name (e.g. CRC32("BaseGameProperties") = 0xe06de777). Names aren't
stored, so scan-names finds them: every hash used in the extracted .cgpr files is matched against
identifiers in the game's strings (NUL-terminated, ASCII or UTF-16, in the DLLs and the packages'
own strings), the CamelCase words in them, and pairs of those words. A pair can match a hash by
chance, so an odd name is likely wrong. Hashes with no known name print as 0x...
"""
import json
import re
import struct
import sys
import zlib
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
NAMES_FILE = REPO / "build" / "crc_names.json"


class Reader:
    def __init__(self, data):
        self.data = data
        self.pos = 0

    def take(self, n):
        if self.pos + n > len(self.data):
            raise ValueError(f"read past end at {self.pos:#x}")
        b = self.data[self.pos:self.pos + n]
        self.pos += n
        return b

    def u8(self):
        return self.take(1)[0]

    def u16(self):
        return struct.unpack("<H", self.take(2))[0]

    def u32(self):
        return struct.unpack("<I", self.take(4))[0]

    def varint(self):
        value = shift = 0
        while True:
            b = self.u8()
            value |= (b & 0x7F) << shift
            if b < 0x80:
                return value
            shift += 7

    def string(self):
        return self.take(self.varint()).decode("latin-1")


class Writer:
    def __init__(self):
        self.parts = []

    def raw(self, b):
        self.parts.append(bytes(b))

    def u16(self, v):
        self.raw(struct.pack("<H", v))

    def u32(self, v):
        self.raw(struct.pack("<I", v))

    def varint(self, v):
        out = bytearray()
        while v >= 0x80:
            out.append(v & 0x7F | 0x80)
            v >>= 7
        out.append(v)
        self.raw(out)

    def string(self, s):
        b = s.encode("latin-1")
        self.varint(len(b))
        self.raw(b)

    def bytes(self):
        return b"".join(self.parts)


def read_object(r):
    obj = {"version": r.u32(), "guid": r.take(16).hex(), "name": r.string(), "unknown": r.u32()}
    obj["components"] = []
    for _ in range(r.u32()):
        ctype = r.u32()
        size = r.u16()
        obj["components"].append({"type": ctype, "body": r.take(size)})
    obj["children"] = [read_object(r) for _ in range(r.u32())]
    return obj


def write_object(w, obj):
    w.u32(obj["version"])
    w.raw(bytes.fromhex(obj["guid"]))
    w.string(obj["name"])
    w.u32(obj["unknown"])
    w.u32(len(obj["components"]))
    for c in obj["components"]:
        if len(c["body"]) > 0xFFFF:
            raise ValueError("component body over 64 KB")
        w.u32(c["type"])
        w.u16(len(c["body"]))
        w.raw(c["body"])
    w.u32(len(obj["children"]))
    for child in obj["children"]:
        write_object(w, child)


def parse(data):
    r = Reader(data)
    if r.take(8) != b"POSS30RV":
        raise ValueError("not a POSS30RV package")
    pkg = {"strings": [r.string() for _ in range(r.u32())]}
    named = r.u32()
    if named:
        raise NotImplementedError(f"{named} named entries")
    pkg["objects"] = [read_object(r) for _ in range(r.u32())]
    if r.pos != len(data):
        raise ValueError(f"{len(data) - r.pos} trailing bytes at {r.pos:#x}")
    return pkg


def build(pkg):
    w = Writer()
    w.raw(b"POSS30RV")
    w.u32(len(pkg["strings"]))
    for s in pkg["strings"]:
        w.string(s)
    w.u32(0)
    w.u32(len(pkg["objects"]))
    for obj in pkg["objects"]:
        write_object(w, obj)
    return w.bytes()


def load_names():
    try:
        return {int(k, 16): v for k, v in json.loads(NAMES_FILE.read_text()).items()}
    except (OSError, ValueError, AttributeError):  # missing, or not a valid names file: run scan-names
        return {}


NAMES = load_names()


def name_of(h):
    return NAMES.get(h, f"0x{h:08x}")


def parse_props(data, start, end):
    """Parse 'u16 count + properties' exactly filling data[start:end]; None if it doesn't fit."""
    if end - start < 2:
        return None
    count = struct.unpack_from("<H", data, start)[0]
    pos, props = start + 2, []
    for _ in range(count):
        if pos + 6 > end:
            return None
        h, size = struct.unpack_from("<IH", data, pos)
        vstart, vend = pos + 6, pos + 6 + size
        if vend > end:
            return None
        props.append({"hash": h, "start": vstart, "end": vend, "children": parse_props(data, vstart, vend)})
        pos = vend
    return props if pos == end else None


def decode_body(body):
    """Decode a component body into its subtype and property tree (offsets into body)."""
    if len(body) < 6:
        return None
    sub, size = struct.unpack_from("<IH", body, 0)
    end = 6 + size
    if end > len(body) or size < 19:
        return None
    return {"subtype": sub, "version": body[6], "guid": body[7:23].hex(),
            "props": parse_props(body, 23, end), "tail": body[end:]}


def set_prop(body, h, raw):
    """Return body with top-level property h set to raw bytes, appended if it isn't there."""
    dec = decode_body(body)
    if not dec or dec["props"] is None:
        raise ValueError("component body has no property list")
    body = bytearray(body)
    for p in dec["props"]:
        if p["hash"] == h:
            body[p["start"]:p["end"]] = raw
            break
    else:
        end = len(body) - len(dec["tail"])
        body[end:end] = struct.pack("<IH", h, len(raw)) + raw
        struct.pack_into("<H", body, 23, len(dec["props"]) + 1)
    size = len(body) - len(dec["tail"]) - 6
    if size > 0xFFFF:
        raise ValueError("component body over 64 KB")
    struct.pack_into("<H", body, 4, size)
    return bytes(body)


STRING_RE = re.compile(rb"(?<=\0)[\x20-\x7e]{2,120}(?=\0)")
WSTRING_RE = re.compile(rb"(?:[\x20-\x7e]\0){2,120}")
IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
WORD_RE = re.compile(r"[A-Z][a-z0-9]+|[A-Z]+(?![a-z])|[a-z0-9]+")
PREFIXES = ("CComponent", "CCPLdr", "CGameObjectComponent", "C", "m_", "k")


def scan_names(extracted, bin64):
    """{hash: name} for the hashes used in extracted's .cgpr files."""
    hashes, strings = set(), set()

    def add_props(props):
        for p in props or []:
            hashes.add(p["hash"])
            add_props(p["children"])

    def add_objects(objs):
        for o in objs:
            strings.add(o["name"])
            for c in o["components"]:
                hashes.add(c["type"])
                dec = decode_body(c["body"])
                if dec:
                    hashes.add(dec["subtype"])
                    add_props(dec["props"])
            add_objects(o["children"])

    for path in Path(extracted).rglob("*.cgpr"):
        try:
            pkg = parse(path.read_bytes())
        except (ValueError, NotImplementedError):
            continue
        strings.update(pkg["strings"])
        add_objects(pkg["objects"])
    for path in [*Path(bin64).glob("*.dll"), *Path(bin64).glob("*.exe")]:
        data = path.read_bytes()
        strings.update(m.decode() for m in STRING_RE.findall(data))
        strings.update(m.decode("utf-16-le") for m in WSTRING_RE.findall(data))

    found = {}  # hash -> (rank, name): a whole string beats a part of one beats a pair of words

    def candidate(name, rank):
        h = zlib.crc32(name.encode())
        if h in hashes and (h not in found or rank < found[h][0]):
            found[h] = (rank, name)

    words = set()
    for s in strings:
        if IDENT_RE.fullmatch(s):
            candidate(s, 0)
        if " " in s and len(s) < 60:  # "Weight Object Avoidance" -> WeightObjectAvoidance
            candidate("".join(w[:1].upper() + w[1:] for w in s.split()), 2)
        for ident in IDENT_RE.findall(s):
            candidate(ident, 1)
            for prefix in PREFIXES:
                if ident.startswith(prefix) and len(ident) > len(prefix):
                    candidate(ident[len(prefix):], 1)
            parts = WORD_RE.findall(ident)
            for i in range(len(parts)):
                for j in range(i + 1, min(len(parts), i + 5) + 1):
                    candidate("".join(parts[i:j]), 2)
            words.update(w for w in parts if re.fullmatch(r"[A-Z][a-z]+[0-9]?", w))
    # Pairs of words a + b, without trying every pair: crc32(b, crc32(a)) is crc32(b) ^ L(crc32(a)),
    # where L is linear and depends only on len(b) (L(x) = crc32(zeros, x) ^ crc32(zeros)). So for each
    # b and unnamed hash h, look up the a with L(crc32(a)) == h ^ crc32(b). The smallest (a, b) wins.
    vocab = sorted(w.encode() for w in words)
    unnamed = hashes - found.keys()
    pairs = {}
    for n in sorted({len(b) for b in vocab}):
        zeros = bytes(n)
        z = zlib.crc32(zeros)
        by_l = {}
        for a in vocab:
            by_l.setdefault(zlib.crc32(zeros, zlib.crc32(a)) ^ z, a)
        for b in vocab:
            if len(b) != n:
                continue
            cb = zlib.crc32(b)
            for h in unnamed:
                a = by_l.get(h ^ cb)
                if a is not None and (h not in pairs or (a, b) < pairs[h]):
                    pairs[h] = (a, b)
    for h, (a, b) in pairs.items():
        found[h] = (3, (a + b).decode())
    print(f"{len(found)} of {len(hashes)} hashes named, from {len(strings)} strings and {len(vocab)} words")
    return {h: name for h, (_, name) in found.items()}


def show_value(raw, strings):
    if len(raw) == 4:
        f, i = struct.unpack("<f", raw)[0], struct.unpack("<i", raw)[0]
        return f"f={f:g} i={i}" if abs(f) < 1e9 and (f == 0 or abs(f) > 1e-6) else f"i={i}"
    if len(raw) == 1:
        return f"b={raw[0]}"
    if len(raw) == 2:
        v = struct.unpack("<H", raw)[0]
        hint = f" str={strings[v]!r}" if v < len(strings) and strings[v] else ""
        return f"u16={v}{hint}"
    if len(raw) == 16:
        return f"guid={raw.hex()}"
    if len(raw) % 4 == 0 and len(raw) <= 64:
        return "floats=" + ",".join(f"{x:g}" for x in struct.unpack(f"<{len(raw) // 4}f", raw))
    return f"x={raw[:48].hex()}{'...' if len(raw) > 48 else ''}"


def dump_props(body, props, strings, pad):
    for p in props:
        if p["children"]:
            print(f"{pad}{name_of(p['hash'])}:")
            dump_props(body, p["children"], strings, pad + "  ")
        else:
            print(f"{pad}{name_of(p['hash'])} = {show_value(body[p['start']:p['end']], strings)}")


def dump_object(obj, strings, raw=False, depth=0):
    pad = "  " * depth
    print(f"{pad}{obj['name']!r}  guid={obj['guid']}")
    for c in obj["components"]:
        body = c["body"]
        dec = decode_body(body)
        if dec is None or dec["props"] is None:
            print(f"{pad}  [{name_of(c['type'])}] (undecoded, {len(body)} bytes)")
            continue
        print(f"{pad}  [{name_of(c['type'])}/{name_of(dec['subtype'])}]")
        dump_props(body, dec["props"], strings, pad + "    ")
        if raw or len(dec["tail"]) != 10:
            print(f"{pad}    (messages/links: {dec['tail'].hex()})")
    for child in obj["children"]:
        dump_object(child, strings, raw, depth + 1)


def match(part, h, name=None):
    if part == "*":
        return True
    if part.startswith("0x"):
        return int(part, 16) == h
    return zlib.crc32(part.encode()) == h or part == name


def find_targets(pkg, path):
    """Yield (component, prop) for an object/component/subtype/prop/... path."""
    parts = path.split("/")
    if len(parts) < 4:
        raise SystemExit(f"path too short: {path}")

    def objects(objs):
        for o in objs:
            yield o
            yield from objects(o["children"])

    # "Name#N" picks the Nth object (1-based, depth-first order) among those named Name
    name, _, nth = parts[0].rpartition("#") if "#" in parts[0] else (parts[0], "", "")
    seen = 0
    for obj in objects(pkg["objects"]):
        if not match(name, None, obj["name"]) and name != "*":
            continue
        seen += 1
        if nth and seen != int(nth):
            continue
        for c in obj["components"]:
            if not match(parts[1], c["type"]):
                continue
            dec = decode_body(c["body"])
            if not dec or dec["props"] is None or not match(parts[2], dec["subtype"]):
                continue
            props = dec["props"]
            for i, part in enumerate(parts[3:]):
                found = [p for p in props if match(part, p["hash"])]
                if not found:
                    break
                if i == len(parts) - 4:
                    for p in found:
                        yield c, p
                props = found[0]["children"] or []


def encode_value(text, size):
    kind, _, v = text.partition(":")
    raw = {"f": lambda: struct.pack("<f", float(v)), "i": lambda: struct.pack("<i", int(v, 0)),
           "u": lambda: struct.pack("<I", int(v, 0)), "b": lambda: bytes([int(v, 0)]),
           "x": lambda: bytes.fromhex(v),
           "fl": lambda: b"".join(struct.pack("<f", float(x)) for x in v.split(","))}[kind]()
    if len(raw) != size:
        raise SystemExit(f"value {text} is {len(raw)} bytes, property has {size}")
    return raw


def scale_delays(pkg, factor):
    """Scale message link delays. In the messages/links section each link names its target by a
    16-byte GUID (an object or component in this package) followed by a float32 delay in seconds."""
    guids = set()

    def objects(objs):
        for o in objs:
            yield o
            yield from objects(o["children"])

    for o in objects(pkg["objects"]):
        guids.add(bytes.fromhex(o["guid"]))
        for c in o["components"]:
            dec = decode_body(c["body"])
            if dec:
                guids.add(bytes.fromhex(dec["guid"]))
    changed = 0
    for o in objects(pkg["objects"]):
        for c in o["components"]:
            dec = decode_body(c["body"])
            if not dec:
                continue
            body = bytearray(c["body"])
            start = len(body) - len(dec["tail"])
            for i in range(start, len(body) - 20):
                if bytes(body[i:i + 16]) in guids:
                    delay = struct.unpack_from("<f", body, i + 16)[0]
                    if 0 < delay <= 600:
                        struct.pack_into("<f", body, i + 16, delay * factor)
                        changed += 1
            c["body"] = bytes(body)
    return changed


def main():
    if sys.argv[1:2] == ["scan-names"]:
        extracted = sys.argv[2] if len(sys.argv) > 2 else REPO / "game" / "extracted"
        bin64 = sys.argv[3] if len(sys.argv) > 3 else REPO / "game" / "original" / "Bin64"
        for d, what in ((extracted, "extracted game data (ara.py extract)"), (bin64, "game Bin64")):
            if not Path(d).is_dir():
                raise SystemExit(f"{d}: not a directory; expected {what}")
        names = scan_names(extracted, bin64)
        if not names:  # don't replace a good names file with an empty one
            raise SystemExit(f"no names found; {NAMES_FILE} left as it was")
        NAMES_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = NAMES_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps({f"{h:08x}": n for h, n in sorted(names.items())}, indent=0) + "\n")
        tmp.replace(NAMES_FILE)
        print(f"wrote {NAMES_FILE}")
        return
    cmd, path = sys.argv[1], sys.argv[2]
    data = open(path, "rb").read()
    pkg = parse(data)
    if cmd == "dump":
        for i, s in enumerate(pkg["strings"]):
            print(f"string[{i}] {s!r}")
        for obj in pkg["objects"]:
            dump_object(obj, pkg["strings"], raw="--raw" in sys.argv)
    elif cmd == "roundtrip":
        out = build(pkg)
        print("identical" if out == data else f"DIFFERENT ({len(out)} vs {len(data)} bytes)")
    elif cmd == "set":
        out_path = sys.argv[3]
        for assignment in sys.argv[4:]:
            target, _, value = assignment.partition("=")
            hits = list(find_targets(pkg, target))
            if not hits:
                raise SystemExit(f"no match for {target}")
            for comp, prop in hits:
                body = bytearray(comp["body"])
                old = bytes(body[prop["start"]:prop["end"]])
                body[prop["start"]:prop["end"]] = encode_value(value, len(old))
                comp["body"] = bytes(body)
                print(f"{target}: {old.hex()} -> {comp['body'][prop['start']:prop['end']].hex()}")
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_bytes(build(pkg))
        print(f"wrote {out_path}")
    elif cmd == "replace-string":
        out_path = sys.argv[3]
        for pair in sys.argv[4:]:
            old, _, new = pair.partition("=")
            hits = [i for i, t in enumerate(pkg["strings"]) if old in t]
            for i in hits:
                pkg["strings"][i] = pkg["strings"][i].replace(old, new)
            print(f"{old!r} -> {new!r}: {len(hits)} strings")
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_bytes(build(pkg))
        print(f"wrote {out_path}")
    elif cmd == "scale-delays":
        out_path, factor = sys.argv[3], float(sys.argv[4])
        print(f"scaled {scale_delays(pkg, factor)} delays")
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_bytes(build(pkg))
        print(f"wrote {out_path}")


if __name__ == "__main__":
    main()
