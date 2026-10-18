# black-hole-sun

Tools to play and mod **Dead Star** (Armature Studio, 2016, Steam build `REL-147994`) on your own copy.

The goal is to let people who own Dead Star play it again, now that its online services are gone, and
to give them a way to mod it: AI players, ship and weapon edits, and more.

> This repo contains **no game files**. You need your own copy of Dead Star from Steam. Game files are
> copied into `game/`, which is gitignored. Don't commit or publish game binaries, decompiled engine
> code, or third-party middleware code (Havok, Wwise, RAD Telemetry).
>
> **No DRM bypass.** Nothing here removes or works around copy protection, Steam, or ownership
> checks: the game still runs through Steam, which checks that your account owns it. The tools
> point the game at a local server in place of the shut-down official one, and patch only copies
> in `game/`, never your Steam install. Details in [LEGAL.md](LEGAL.md).
>
> Not affiliated with or endorsed by Armature Studio or the game's publishers. Copyright (C) 2026 bbz.
> The tools in this repo are licensed under the GNU Affero General Public License, version 3 only
> (`AGPL-3.0-only`, see [LICENSE](LICENSE) and [NOTICE](NOTICE)), with an additional permission
> under section 7 for combining them with Dead Star's own executables and libraries
> ([LICENSE-EXCEPTION.md](LICENSE-EXCEPTION.md)). That covers this repo's own code and notes only,
> and grants no rights in Dead Star itself. If you run a modified version of these tools as a
> service other people use over a network (for example a public `web_services.py`), AGPL section 13
> requires you to offer those users your modified source. Name use: [TRADEMARKS.md](TRADEMARKS.md).
> Contributions: see [CONTRIBUTING.md](CONTRIBUTING.md).

## Setup

1. Install tools (via [scoop](https://scoop.sh)):
   ```powershell
   scoop bucket add extras
   scoop install git gh python mingw
   ```
2. Clone this repo, then copy the game in and verify the binaries:
   ```powershell
   python -I tools/copy_game.py            # default Steam path
   python -I tools/copy_game.py "D:\SteamLibrary\steamapps\common\DeadStar"
   ```
   This copies to `game/original/` and checks against `docs/binaries.sha256`. Treat `game/original`
   as read-only; make a separate copy for anything you modify or run with patches:
   ```powershell
   python -I tools/make_work.py            # game/work (or: make_work.py NAME for game/NAME)
   ```
3. Build the archive's file name list (`build/ara_names.txt`), so `ara.py extract` writes files
   by path instead of by hash:
   ```powershell
   python -I tools/ara.py scan-names game/original/GameData/gamedata.ara (Get-ChildItem game/original/Bin64/*.dll, game/original/Bin64/*.exe).FullName
   ```

## Running the game on modern CPUs

On CPUs with SHA extensions (AMD Ryzen, Intel Ice Lake and later) the game crashes during startup,
in the old OpenSSL it ships with. Setting this environment variable avoids it:

```
OPENSSL_ia32cap=:~0x20000000
```

`tools/run_work.ps1` sets it for you. To fix the Steam install too, set the variable for your user
(`setx OPENSSL_ia32cap :~0x20000000`) and restart Steam, or put
`cmd /c "set OPENSSL_ia32cap=:~0x20000000 && %command%"` in the game's Steam launch options.

With the fix, the game reaches the title screen, then shows CONNECTION_ERROR, because Armature's
account service is gone. The next section replaces it.

## Logging in with local web services

```powershell
python -I tools/patch_binaries.py     # game/work now uses http://127.0.0.1:28080/
python -I tools/web_services.py       # leave running
powershell -File tools/run_work.ps1
```

Press Enter on the title screen. The game logs in against `web_services.py`, creates a Steam lobby
and reaches the matchmaking screen. Player data is kept in `game/work/Server/players/<steamid>.json`.

`web_services.py` checks every login with Steam: the game's login ticket has to be genuine and its
account has to own Dead Star, or the login is refused. It needs Steam running and signed in, and
Steam shows you as playing Dead Star while it runs.

## Playing a match on a local server

The same game binaries run the server. The server instance needs an extra patch to accept
connections on UDP 27500.

```powershell
python -I tools/make_work.py server
python -I tools/patch_binaries.py --instance server --dedicated   # or --real-listen for a listen server
python -I tools/web_services.py                                 # leave running
powershell -File tools/run_work.ps1 -Instance server -Server -Background
powershell -File tools/run_work.ps1                             # client: Enter, then Find Match
```

The local matchmaker sends the client to `127.0.0.1:27500`, and the client joins the server's
match. Allow both through Windows Firewall when asked. With `--dedicated` the server only hosts.
With `--real-listen` alone it's a listen server that also logs in and adds its own local player.
Either way its window shows a debug-camera view of the match.

The client can also skip the matchmaker: `run_work.ps1 -Connect 127.0.0.1:27500`.

`patch_binaries.py` also removes the 16:9 limit by default, so ultrawide screens get no black bars
(`--no-widescreen` keeps it).

## Editing game data

The game's data is in `GameData/gamedata.ara`. Extract it once:

```powershell
python -I tools/ara.py extract game/original/GameData/gamedata.ara game/extracted
python -I tools/cgpr.py scan-names      # names for cgpr.py, in build/crc_names.json
```

You don't need to repack it: a file at `GameData/<archive path>` in an instance is loaded in place of
the archived copy. For example, copy `needle/loc/frontend/_win/frontend_strings.cloc` into
`game/work/GameData/needle/loc/frontend/_win/`, edit it, and run `run_work.ps1`.
(`ara.py pack` rebuilds a whole archive if you want one; packing an unmodified extraction gives
back the original byte-for-byte.)

Ships, weapons and other game objects are `.cgpr` property files. `tools/cgpr.py` dumps and edits
them (all commands are listed at the top of `tools/cgpr.py`):

```powershell
python -I tools/cgpr.py dump game/extracted/PATH.cgpr
python -I tools/cgpr.py set game/extracted/PATH.cgpr game/work/GameData/PATH.cgpr "OBJECT/COMPONENT/SUBTYPE/PROPERTY=f:500"
```

Values must keep their size. Names come from `build/crc_names.json` (`cgpr.py scan-names`); a hash
with no known name shows as `0x...`, and a few of the names may be wrong.

To make the logo intro fast (about 12 s to 4 s before the title, the rest is loading):

```powershell
$L = "needle/ui/legalmenu/script/_win/legal.cgpr"
python -I tools/cgpr.py scale-delays game/extracted/$L game/work/GameData/$L.tmp 0.02
python -I tools/cgpr.py set game/work/GameData/$L.tmp game/work/GameData/$L "Sequence Handler/Timer/BasicTimer/Time=f:0.05"
Remove-Item game/work/GameData/$L.tmp
```

## Watching an AI-vs-AI match

A listen server in observer mode is a spectator with a real game view. Three mods make it an
AI-vs-AI match to watch:

- `ai_teams` (`tools/mod_ai_teams.py`): an AI squad on each team of the Conquest map Crown.
- `ai_pilots` (`tools/mod_ai_pilots.py`, on top of ai_teams): the squads fly player ships (races A
  to E) instead of NPC ships. The map's own base defenders stay NPCs, as in a normal match.
- `spectator_ui` (`tools/mod_spectator_ui.py`, spectator instance only): no LAUNCH banner or launch
  ring.

`bhs.dll`, our code loaded into the listen server, runs the spectator camera and switches every 8 s
to a fighting AI ship on the other team. It also sends each ai_teams squad to the nearest capture
point its team doesn't own.

```powershell
python -I tools/make_work.py listen
python -I tools/patch_binaries.py --instance listen --real-listen
python -I tools/mod_ai_teams.py; python -I tools/mod_ai_pilots.py; python -I tools/mod_spectator_ui.py
Copy-Item -Recurse -Force game/mods/ai_teams/GameData/*, game/mods/ai_pilots/GameData/*, game/mods/spectator_ui/GameData/* game/listen/GameData/
python -I tools/web_services.py                                 # leave running
powershell -File tools/run_work.ps1 -Instance listen -Server -Listen -Windowed -Background `
    -Extra '-vc needle_StartRegion=4 -vc needle_EnableObserverMode=true -vc needle_ObserverModeSkipAI=false'
# once the match has started:
$env:PATH = "$env:USERPROFILE\scoop\apps\mingw\current\bin;$env:PATH"; powershell -File tools/bhs/build.ps1
python -I tools/inject.py listen
python -I tools/bhs_cmd.py listen sound 0                      # mute it
```

`tools/bhs_cmd.py INSTANCE COMMAND` sends the DLL a command and prints its reply: `sound` toggles
mute (`sound 50` sets the volume, in the Windows mixer), `director 0` stops switching, `ai_next 1`
jumps to team 1, `only 37` follows only the player-ship AIs (`ai_list` shows the kinds). The full
list is at the top of `tools/bhs/bhs.cpp`; the DLL logs to `game/<instance>/bhs/log.txt`. Inject it
once per game launch: after unloading and reloading it a few times, the view stopped following.
`python -I tools/ai_state.py` prints every AI squad and ship (position, action, waypoint) from a
running instance.

Useful game options (`-Extra '-vc NAME=VALUE'`): `needle_StartRegion` (4 = Conquest Crown),
`needle_GodMode` and `needle_ShipCloakPlayer` to fly among the AI unharmed.

## Tools

```
tools/copy_game.py       Copy the Steam install into game/original and verify it
tools/make_work.py       Create game/work (or game/NAME), a runnable copy for testing mods
tools/run_work.ps1       Launch an instance (CPU crash fix; -Server, -Connect, -Dump)
tools/patch_binaries.py  Patch an instance to use web_services.py (--dedicated / --real-listen for servers)
tools/web_services.py    Local stand-in for the game's web services (login, MOTD, player data, matchmaker)
tools/steam_auth.py      Check login tickets with Steam (used by web_services.py)
tools/ara.py             List / extract / pack gamedata.ara; scan-names builds the name list
tools/cgpr.py            Dump / edit .cgpr game object property files; scan-names builds its name list
tools/mod_*.py           Mods: AI teams, AI pilots, AI targets, pirate teams, spectator UI
tools/bhs/               bhs.dll: our code inside the game (spectator director, commands)
tools/inject.py          Load bhs.dll into a running instance
tools/bhs_cmd.py         Send a command to bhs.dll and print the reply
tools/ai_state.py        Print AI squads and ships from a running instance
tools/screenshot.ps1     Capture the game window
tools/check_release.py   Check the repo for game files, binaries and decompiler output
tools/release.py         Publish a release (maintainer)
docs/binaries.sha256     SHA-256 of the supported game build
game/                    (gitignored) your game copies; game/original is never modified
build/                   (gitignored) local working files, incl. ara_names.txt and crc_names.json
```

## Roadmap

- **AI matches**: more ships per squad, all Conquest maps, each ship using its own abilities, and a
  map overview camera for spectating.
- **Unattended runs**: dedicated servers with no client, several at once, starting the next match
  when one ends.
- **Stats and balance**: per-match stats (ships, kills, deaths, captures, winner), grouped by meta (a
  ship + loadout + role playstyle). Then mod variants that tone down the dominant meta, compared
  against the unmodded game. AI skill isn't human skill, so the results are relative between
  variants, not human balance.
- **More data editing**: messages and links, colours and shaders, resizing values, textures and
  strings.

## Security and Legal Requests

To report a security issue, or to send a legal request privately,
use the "Report a vulnerability" button under this repo's Security tab.
Only the maintainer can see these reports.

You can also file a request directly with GitHub.
