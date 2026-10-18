"""Read, extract and rebuild Armature `.ara` archives (Dead Star `gamedata.ara`).

Usage:
  python -I tools/ara.py list ARCHIVE
  python -I tools/ara.py extract ARCHIVE OUT_DIR
  python -I tools/ara.py pack IN_DIR ARCHIVE
  python -I tools/ara.py scan-names ARCHIVE [EXTRA_FILES...]

`extract` writes every file under OUT_DIR by its archive path, plus `_ara_manifest.tsv`, which
records the original order, hashes and per-entry fields. `pack` rebuilds an archive from such a
directory: files in the manifest keep their order, new files are appended. Packing an unmodified
extraction reproduces the original archive byte-for-byte.

File names are not stored in the archive, only hashes. Known names live in
build/ara_names.txt (gitignored); `scan-names` rebuilds that list from path strings found in the archive
data and any extra files (such as the game DLLs). Files with no known name are extracted to
`_unnamed/<hash>.<ext>`.

Format (little-endian):
  0x00  char[4] "ARMA"
  0x04  u16     version (2)
  0x06  u16     unknown (0x696e), preserved
  0x08  u32     file count
  0x0c  u32     bucket count (power of two)
  0x10  u32     entry table offset
  0x14  u32     data offset
  0x18  u32     0
  0x1c  u32[bucket count]  bucket offsets (0 = empty)
  buckets: u32 count, then count x (u32 hash, u32 entry index), sorted by index;
           a hash goes in bucket (hash & (bucket count - 1))
  entry table: count x {u32 offset, u16 aux, u16 aux (same), u32 size, u32 size (same), u32 0}
               the file occupies size + aux bytes; aux is non-zero only for some models, where the
               last aux bytes look like a separate GPU section (index buffer). Rewriting a model with
               a different layout needs the right aux value
  data: files in entry order, each 16-byte aligned, zero padded
Hash: 32-bit FNV-1a of the upper-cased path without the leading "$", e.g. "/NEEDLE_WIN.BPDEPS".
"""
import re
import struct
import sys
from collections import Counter
from pathlib import Path

NAMES_FILE = Path(__file__).resolve().parent.parent / "build" / "ara_names.txt"
MANIFEST = "_ara_manifest.tsv"
HEADER = struct.Struct("<4sHHIIIII")
ENTRY = struct.Struct("<IHHIII")
ALIGN = 16
MAGIC_EXT = {b"LDOM": "cmdl", b"RTXT": "ctxr", b"!XFC": "cfx", b"<Usi": "xml"}


def path_hash(path):
    h = 0x811C9DC5
    for c in path.lstrip("$").upper().encode():
        h = ((h ^ c) * 0x01000193) & 0xFFFFFFFF
    return h


def align(n):
    return (n + ALIGN - 1) & ~(ALIGN - 1)


class Archive:
    def __init__(self, path):
        self.path = Path(path)
        with open(path, "rb") as f:
            head = f.read(HEADER.size)
            magic, self.version, self.unknown, count, nbuckets, table, data, _ = HEADER.unpack(head)
            if magic != b"ARMA":
                raise ValueError(f"{path}: not an ARMA archive")
            f.seek(0)
            toc = f.read(data)
        self.hashes = [None] * count
        for off in struct.unpack_from(f"<{nbuckets}I", toc, HEADER.size):
            if off:
                n = struct.unpack_from("<I", toc, off)[0]
                pairs = struct.unpack_from(f"<{2 * n}I", toc, off + 4)
                for h, i in zip(pairs[0::2], pairs[1::2]):
                    self.hashes[i] = h
        self.entries = [ENTRY.unpack_from(toc, table + ENTRY.size * i) for i in range(count)]

    def read(self, f, index):
        offset, aux, _, size, _, _ = self.entries[index]
        f.seek(offset)
        return f.read(size + aux)


def load_names():
    names = {}
    if NAMES_FILE.exists():
        for line in NAMES_FILE.read_text().splitlines():
            if line:
                names[path_hash(line)] = line
    return names


def guess_ext(data):
    return MAGIC_EXT.get(data[:4], "bin")


def cmd_list(archive):
    arc, names = Archive(archive), load_names()
    for i, (h, e) in enumerate(zip(arc.hashes, arc.entries)):
        print(f"{i}\t{h:08x}\t{e[3] + e[1]}\t{names.get(h, '?')}")


def cmd_extract(archive, out_dir):
    arc, names = Archive(archive), load_names()
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    rows = [f"#ARMA\t{arc.version}\t{arc.unknown:#06x}"]
    with open(arc.path, "rb") as f:
        for i, (h, e) in enumerate(zip(arc.hashes, arc.entries)):
            data = arc.read(f, i)
            rel = names.get(h, f"/_unnamed/{h:08x}.{guess_ext(data)}").lstrip("/")
            dest = out / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            rows.append(f"{h:08x}\t{e[1]}\t{rel}")
            if i % 1000 == 0:
                print(f"{i}/{len(arc.entries)} {rel}")
    (out / MANIFEST).write_text("\n".join(rows) + "\n")
    named = sum(h in names for h in arc.hashes)
    print(f"Extracted {len(arc.entries)} files ({named} named) to {out}")


def cmd_pack(in_dir, archive):
    src = Path(in_dir)
    lines = (src / MANIFEST).read_text().splitlines()
    _, version, unknown = lines[0].split("\t")
    files = []  # (hash, aux, path)
    seen = set()
    for line in lines[1:]:
        h, aux, rel = line.split("\t")
        files.append((int(h, 16), int(aux), rel))
        seen.add(rel)
    for p in sorted(src.rglob("*")):
        rel = p.relative_to(src).as_posix()
        if p.is_file() and rel != MANIFEST and rel not in seen:
            files.append((path_hash("/" + rel), 0, rel))
            print(f"Adding new file /{rel}")
    if len({h for h, _, _ in files}) != len(files):
        raise ValueError("duplicate path hashes")

    count = len(files)
    nbuckets = 8192
    while nbuckets < count // 2:
        nbuckets *= 2
    buckets = [[] for _ in range(nbuckets)]
    for i, (h, _, _) in enumerate(files):
        buckets[h & (nbuckets - 1)].append((h, i))

    bucket_offsets, bucket_data = [], bytearray()
    base = HEADER.size + 4 * nbuckets
    for b in buckets:
        if not b:
            bucket_offsets.append(0)
            continue
        bucket_offsets.append(base + len(bucket_data))
        bucket_data += struct.pack(f"<I{2 * len(b)}I", len(b), *[x for pair in b for x in pair])
    table = base + len(bucket_data)
    data_start = align(table + ENTRY.size * count)

    sizes = [(src / rel).stat().st_size for _, _, rel in files]
    entries, offsets, offset = bytearray(), [], data_start
    for (h, aux, rel), size in zip(files, sizes):
        entries += ENTRY.pack(offset, aux, aux, size - aux, size - aux, 0)
        offsets.append(offset)
        offset = align(offset + size)

    with open(archive, "wb") as f:
        f.write(HEADER.pack(b"ARMA", int(version), int(unknown, 16), count, nbuckets, table, data_start, 0))
        f.write(struct.pack(f"<{nbuckets}I", *bucket_offsets))
        f.write(bucket_data)
        f.write(entries)
        f.write(b"\0" * (data_start - f.tell()))
        for (_, _, rel), offset in zip(files, offsets):
            f.write(b"\0" * (offset - f.tell()))
            f.write((src / rel).read_bytes())
    print(f"Packed {count} files into {archive}")


PATH_RE = re.compile(rb"\$?/[A-Za-z0-9_/.\-]{3,200}\.[A-Za-z0-9_]{1,12}")


def cmd_scan_names(archive, *extra):
    arc = Archive(archive)
    wanted = set(arc.hashes)
    found = load_names()
    candidates = set()
    for src in [archive, *extra]:
        with open(src, "rb") as f:
            tail = b""
            while chunk := f.read(64 << 20):
                buf = tail + chunk
                candidates.update(PATH_RE.findall(buf))
                candidates.update(m.replace(b"\0", b"") for m in re.findall(rb"(?:[A-Za-z0-9_$/.\-]\0){4,120}", buf))
                tail = buf[-400:]
    hits = {}
    for raw in candidates:
        s = raw.decode("ascii", "ignore").lstrip("$")
        if not s.startswith("/"):
            s = "/" + s
        # strings are often followed by junk bytes; try each prefix that still has an extension
        for end in range(len(s), s.find(".", s.rfind("/")) + 1, -1):
            h = path_hash(s[:end])
            if h in wanted:
                hits.setdefault(h, set()).add(s[:end])
    # a junk-suffixed string can collide with another entry's hash; prefer common extensions
    ext_count = Counter(n.rsplit(".", 1)[-1] for names in hits.values() for n in names)
    for h, names in hits.items():
        if h not in found:
            found[h] = max(names, key=lambda n: (ext_count[n.rsplit(".", 1)[-1]], -len(n)))
    found = {h: n for h, n in found.items() if h in wanted}
    NAMES_FILE.parent.mkdir(parents=True, exist_ok=True)
    NAMES_FILE.write_text("\n".join(sorted(found.values())) + "\n")
    print(f"{len(found)} of {len(arc.hashes)} names known; wrote {NAMES_FILE}")


COMMANDS = {"list": cmd_list, "extract": cmd_extract, "pack": cmd_pack, "scan-names": cmd_scan_names}

if __name__ == "__main__":
    if len(sys.argv) < 3 or sys.argv[1] not in COMMANDS:
        sys.exit(__doc__)
    COMMANDS[sys.argv[1]](*sys.argv[2:])
