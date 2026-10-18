"""Publish HEAD to the release repo as one squashed commit.

Usage: python -I tools/release.py [-m MESSAGE] [--remote NAME] [--dry-run]

This repo (bbz/black-hole-sun-private) is where work happens. The release repo (bbz/black-hole-sun,
git remote `release`) only has one commit per release: its tree is HEAD's tree and its parent is
the previous release, so none of this repo's history goes out. Steps:
  1. refuse if the working tree has uncommitted changes, so the tree checked is the tree released
  2. tools/check_release.py on the tree (game files, binaries, decompiler output); stop on any hit
  3. fetch release/main, commit HEAD^{tree} on top of it (skipped if the tree hasn't changed), with a
     Co-authored-by trailer for every other author in this repo's history, since their commits
     don't go out
  4. push it to release/main
To bring a pull request from the release repo back here: `git format-patch` there, `git am` here
(which keeps its author, so the next release credits them).
"""
import argparse
import datetime
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_release  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
BRANCH = "main"


def git(*args, check=True):
    r = subprocess.run(["git", *args], cwd=REPO, check=check, capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else None


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("-m", "--message", help="commit message (default: Release YYYY-MM-DD)")
    ap.add_argument("--remote", default="release")
    ap.add_argument("--dry-run", action="store_true", help="make the commit but don't push it")
    args = ap.parse_args()

    if git("status", "--porcelain"):
        sys.exit("uncommitted changes; commit or stash them first")
    found = check_release.check_files(check_release.tree_files(REPO))
    for p in found:
        print("!", p)
    if found:
        sys.exit(f"{len(found)} problem(s); nothing released")

    git("fetch", "-q", args.remote)
    parent = git("rev-parse", "-q", "--verify", f"{args.remote}/{BRANCH}", check=False)
    tree = git("rev-parse", "HEAD^{tree}")
    if parent and git("rev-parse", f"{parent}^{{tree}}") == tree:
        print(f"{args.remote}/{BRANCH} already has this tree; nothing to release")
        return
    message = args.message or f"Release {datetime.date.today().isoformat()}"
    me = git("config", "user.email")
    authors = dict.fromkeys(a for a in git("log", "--reverse", "--format=%an <%ae>").splitlines()
                            if not a.endswith(f"<{me}>"))
    if authors:
        message += "\n\n" + "\n".join(f"Co-authored-by: {a}" for a in authors)
    commit = git("commit-tree", tree, *(["-p", parent] if parent else []), "-m", message)
    print(f"{commit[:10]} (from {git('rev-parse', '--short', 'HEAD')})\n{message}")
    if args.dry_run:
        print(f"dry run; push with: git push {args.remote} {commit}:refs/heads/{BRANCH}")
        return
    subprocess.run(["git", "push", args.remote, f"{commit}:refs/heads/{BRANCH}"], cwd=REPO, check=True)


if __name__ == "__main__":
    main()
