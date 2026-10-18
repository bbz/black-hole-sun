"""Check a repo tree, and optionally its whole git history, for things it must never contain.

Usage: python -I tools/check_release.py [DIR] [--history]

DIR defaults to the repo this script is in. In a git repo only tracked and untracked-but-not-ignored
files are checked, so game/ and build/ are skipped. Exits 1 if anything is found. CI runs this on
every push and pull request.

Fails on:
  - file types not in ALLOWED_EXT / ALLOWED_NAMES (fail closed: a new kind of file needs adding
    here on purpose), with a clearer message for game data and binaries (GAME_EXT)
  - binary content (any NUL byte) and files over MAX_SIZE
  - text that looks like decompiler output: decompiler pseudo-C (DECOMPILER_RE) or the header comments
    decompilers write (FINGERPRINT_RE). Addresses and offsets in code are fine.
--history also checks every blob reachable from any ref, so a file that was committed and later
deleted is still caught. If that happens, purge it with git filter-repo or start a new history.
"""
import argparse
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# no .json: name lists and other data taken from the game are built locally (ara.py and cgpr.py
# scan-names), never committed
ALLOWED_EXT = {".py", ".ps1", ".md", ".cpp", ".h", ".sha256", ".yml", ".roster"}
ALLOWED_NAMES = {"LICENSE", "NOTICE", "DCO", ".gitignore", ".gitattributes"}
# game archive and cooked data, audio, binaries, other engines' packages
GAME_EXT = {".ara", ".cgpr", ".sgpr", ".ctxr", ".cmat", ".cmdl", ".cloc", ".cfx", ".wem", ".bnk",
            ".bpdeps", ".dll", ".exe", ".pdb", ".lib", ".so", ".pak", ".uasset", ".gpr", ".rep"}
MAX_SIZE = 256 << 10
# decompiler pseudo-C: undefinedN declarations, longlong / uint / byte casts, CONCATnn / SUBnn / ZEXTnn
# helpers, in_ and extraout_ register names, and the names Ghidra makes up: FUN_ / DAT_ / LAB_ / PTR_
# plus an address, param_N (after a type, or twice on a line, so a lone hand-written param_2 passes),
# local_XX (hex with a digit), uVarN / pcVarN / auVarN (Ghidra's type-letter prefixes only, so
# envVar1 passes), unaff_, stack0x
DECOMPILER_RE = re.compile(
    rb"\bundefined[1248]?\s+\*?\w+\s*[;=,)]|\((?:u?longlong|uint|ulong|byte)\s*\*?\)\s*\w|"
    rb"\b(?:CONCAT|SUB|ZEXT|SEXT)\d{2}\(|\bin_[A-Z]{2,3}\b|\bextraout_\w+|"
    rb"\b(?:thunk_)?(?:FUN|DAT|LAB|PTR)_[0-9a-fA-F]{6,}\b|\blocal_[0-9a-f]*\d[0-9a-f]*\b|"
    rb"\b(?:undefined\d?|u?int|u?long|u?longlong|u?short|char|byte|bool|float|double|void)"
    rb"(?:\s*\*+\s*|\s+)param_\d+\b|\bparam_\d+\b.*\bparam_\d+\b|"
    rb"\b(?:a|p{1,2})?[bcdfilsuvw]Var\d+\b|\bunaff_\w+|\bstack0x[0-9a-f]+")
# written so this file doesn't match itself
FINGERPRINT_RE = re.compile(
    rb"(?i)\bdecompiled (?:by|with)\b|\bhex-?rays\b|\bil2cpp ?dumper\b|\bdn ?spy\b|\bilspy\b|"
    rb"\bjadx\b|\bjd-?gui\b|\bfern ?flower\b|\.decompiled\.")


def check_file(path, data):
    """Yield a message for each problem in one file (path is repo-relative, with / separators,
    optionally followed by @blob for a file in history)."""
    name = path.rsplit("/", 1)[-1].split("@")[0]
    ext = "." + name.rsplit(".", 1)[-1].lower() if "." in name.lstrip(".") else ""
    if ext in GAME_EXT:
        yield f"{path}: game data or binary ({ext})"
        return
    if name not in ALLOWED_NAMES and ext not in ALLOWED_EXT:
        yield f"{path}: unknown file type; add it to ALLOWED_EXT in tools/check_release.py if it's ours"
    if b"\0" in data:
        yield f"{path}: binary file"
        return
    if len(data) > MAX_SIZE:
        yield f"{path}: {len(data) >> 10} KB (limit {MAX_SIZE >> 10} KB)"
    for n, line in enumerate(data.splitlines(), 1):
        if DECOMPILER_RE.search(line) or FINGERPRINT_RE.search(line):
            yield f"{path}:{n}: looks like decompiler output: {line.strip()[:100].decode('utf-8', 'replace')}"


def check_files(files):
    """files: {path: bytes}. Returns the list of problems."""
    return [p for path, data in sorted(files.items()) for p in check_file(path, data)]


def tree_files(root):
    if (root / ".git").exists():
        names = subprocess.run(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                               cwd=root, check=True, capture_output=True).stdout.decode().split("\0")
        return {n: (root / n).read_bytes() for n in sorted(names) if n and (root / n).is_file()}
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*"))
            if p.is_file() and ".git" not in p.relative_to(root).parts}


def history_files(root):
    """Every blob reachable from any ref, as {"path@sha": bytes}."""
    paths = {}
    for line in subprocess.run(["git", "rev-list", "--objects", "--all"], cwd=root, check=True,
                               capture_output=True).stdout.decode().splitlines():
        sha, _, path = line.partition(" ")
        if path:
            paths.setdefault(sha, path)
    out = subprocess.run(["git", "cat-file", "--batch"], cwd=root, check=True, capture_output=True,
                         input="".join(f"{sha}\n" for sha in paths).encode()).stdout
    files, pos = {}, 0
    while pos < len(out):
        end = out.index(b"\n", pos)
        sha, kind, size = out[pos:end].decode().split()
        data = out[end + 1:end + 1 + int(size)]
        pos = end + 2 + int(size)
        if kind == "blob":
            files[f"{paths[sha]}@{sha[:10]}"] = data
    return files


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("dir", nargs="?", type=Path, default=REPO)
    ap.add_argument("--history", action="store_true", help="also check every blob in git history")
    args = ap.parse_args()
    files = tree_files(args.dir)
    found = check_files(files)
    if args.history:
        hist = history_files(args.dir)
        found += check_files(hist)
        print(f"history: {len(hist)} blobs")
    for p in found:
        print("!", p)
    print(f"checked {len(files)} files: {len(found)} problem(s)")
    sys.exit(1 if found else 0)


if __name__ == "__main__":
    main()
