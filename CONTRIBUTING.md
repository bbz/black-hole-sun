# Contributing

Pull requests are welcome. Open an issue first for anything bigger than a fix, so we can agree on
the approach before you spend time on it.

## No game code or data

This keeps the repo publishable. Don't add game binaries, files from `gamedata.ara` (extracted,
edited or repacked), decompiler output, or code from the middleware the game uses (Havok, Wwise,
RAD Telemetry), and no binary diffs that rebuild any of them. Write tools that read or patch the
user's own copy of the game rather than shipping changed files; the same goes for lists of names
from the game, which a scan of the user's copy builds (`ara.py` and `cgpr.py scan-names`). In
comments and docs, describe what
the game does in your own words; the addresses, offsets and byte patterns the code needs are fine,
but never paste a decompiler's or disassembler's output.

No DRM or ownership-check bypasses: changes must not remove or work around copy protection, Steam
or license checks (see [LEGAL.md](LEGAL.md)). Third-party code needs a license compatible with the
AGPL-3.0-only and a note in the pull request saying where it came from.

Before opening a pull request, run the same check CI runs:

```powershell
python -I tools/check_release.py .
```

It rejects unknown file types (add an extension to `ALLOWED_EXT` in `tools/check_release.py` if the
new file is really ours), binaries, files over 256 KB and anything that looks like decompiler
output. If something slips into a commit, rewrite the branch so it's gone from the history too, not
just deleted in a later commit.

## License

The repo is licensed under the [AGPL-3.0-only](LICENSE) with an
[additional permission](LICENSE-EXCEPTION.md) for combining it with Dead Star's own executables
and libraries. By submitting a contribution you agree it's licensed under the same terms.

## Commits

- **Each commit's author is the person who signs it off.** A change someone else drafted is landed
  by re-authoring it, not by committing it under their name with a second sign-off appended.
- **Sign off every commit** under the [Developer Certificate of Origin](DCO) (DCO 1.1):
  `git commit -s` adds `Signed-off-by: Your Name <you@example.com>`, which certifies that you wrote
  the change, or otherwise have the right to submit it, under this repo's license. To sign off
  commits you already made: `git rebase HEAD~N --signoff`. The [DCO app](https://github.com/apps/dco)
  checks every commit.
- **`Signed-off-by:`, `Co-authored-by:` and `Co-developed-by:` each name a person** who stands
  behind the account and can make the DCO certification. Use whatever tools you like to write the
  change; you sign off as the author of the result, and tools and bots don't go in trailers.

## Contributor License Agreement

Every contribution also needs the [Individual CLA](legal/ICLA.md). cla-assistant asks you to accept
it on your first pull request, and the acceptance carries to later ones. It grants the maintainer
the right to relicense your contribution under any terms, without asking you again. You keep the copyright in your work.

## The authorship check

A green DCO says each commit is signed off and a green CLA says an account accepted the agreement;
neither says a person stands behind the account. The `authorship` check adds that: every author,
committer and trailer on a pull request has to appear in
[`.github/AUTHORS.roster`](.github/AUTHORS.roster), which the maintainer edits. Accounts not listed
fail closed, so a first pull request waits until the maintainer adds you to the roster, if they
do; a roster entry carries to later pull requests. The line wants your GitHub login, plus any email you sign trailers with.

If the check blocks your work, the fix is a rebase: re-author under the person who signs off, drop
the trailers naming anyone else, force-push.

## How pull requests land

Work happens in a private development repo, and this repo gets one squashed commit per release.
The maintainer may apply a pull request's changes to the development repo, and they then go out
with a later release. The pull request itself may be closed rather than merged, and its commits
aren't kept in this repo's history.

## Credit

Release commits may name contributors whose work they contain in `Co-authored-by:` trailers. Your
DCO sign-offs and the CLA record are the record of your authorship. [CONTRIBUTORS.md](CONTRIBUTORS.md) is separate: a list you're welcome,
never required, to add yourself to in your first pull request.
