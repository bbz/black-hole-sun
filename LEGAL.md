# Legal

black-hole-sun is a community project that lets owners of Dead Star (PC, Steam) play it again now
that its online services are gone, and mod it: AI players, ship and weapon edits, and more.

## What this project is

- Independently written tools (Python scripts and a DLL) that work with the user's own copy of
  Dead Star. They copy the Steam install into `game/`, patch those copies (never the Steam install
  or `game/original`), run a local stand-in for the game's web services, edit game data the user
  extracted from their own copy, and load our own code into a running game.
- Licensed under the AGPL-3.0-only with an additional permission under section 7. See
  [LICENSE](LICENSE), [LICENSE-EXCEPTION.md](LICENSE-EXCEPTION.md) and [NOTICE](NOTICE).

## What this project is not

- **Not a copy of the game.** This repository contains no Dead Star executables, libraries, data,
  assets or source code, decompiled or otherwise, and no lists of names taken from them: the tools
  build those from your copy. You need your own copy of Dead Star from Steam.
- **Not a DRM bypass.** Nothing here removes or works around copy protection, Steam, or
  ownership and license checks. The game still runs through Steam: Steam has to be running and
  your account has to own Dead Star. `steam_appid.txt`, which `make_work.py` adds, is Steamworks'
  own mechanism for starting a game outside the Steam client's launcher; Steam still checks the
  license. The patches only point the game at a local server instead of Armature's shut-down one,
  let a server instance accept network players, and remove the 16:9 limit. The local web services
  check every login with Steam and refuse a ticket that isn't genuine or an account that doesn't
  own Dead Star.
- **Not affiliated with Armature Studio.** "Dead Star" appears here only to say what the tools work
  with. See [TRADEMARKS.md](TRADEMARKS.md).
- **Not commercial.** The project charges nothing, sells nothing and runs no ads.
- **PC only.** It supports only the Steam build `REL-147994`, not the PlayStation 4 version.

## Rights holders

If you represent Armature Studio or another rights holder and have concerns, send them privately
with the "Report a vulnerability" button under the repo's Security tab (only the maintainer sees
these), open an issue at https://github.com/bbz/black-hole-sun/issues, or file a request directly
with GitHub.
