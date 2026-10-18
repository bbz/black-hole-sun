# Launch a game instance (game/work by default, see tools/make_work.py). Steam must be running.
#   -Instance NAME  run game/NAME instead of game/work
#   -Server         start as a dedicated server (server.sgpr loader) instead of the client
#   -Listen         with -Server: listen_server.sgpr, which also runs the client view for the local
#                   player (needs patch_binaries.py --real-listen, not --dedicated)
#   -Connect ADDR   client: skip the web matchmaker and connect straight to ip[:port]
#   -Extra '...'    extra arguments, e.g. '-vc needle_useMatchmaker=false'; string values as name='value'
#   -Background     let the game take input while unfocused
#   -Windowed       1280x720 window (screenshot.ps1 can't capture exclusive fullscreen; server: not 640x360)
#   -Dump           run under Sysinternals ProcDump; full dump to game/<instance>/Dumps on a crash
#   -Sound          server: keep the sound on. A dedicated server starts muted; a listen server logs
#                   in and takes the profile's volume. bhs.dll toggles it live (bhs_cmd.py INSTANCE sound)
#
# OPENSSL_ia32cap: the engine statically links OpenSSL 1.0.2 (Jan 2015), whose SHA-1 code for CPUs
# with SHA extensions (Ryzen, newer Intel) crashes on Win64.
# Clearing CPUID.7.EBX bit 29 makes OpenSSL use its generic SHA-1 path.
#
# The web services URL is patched by tools/patch_binaries.py; the release build ignores the
# needle_publicServiceUrlOverride script var.
param([string]$Instance = 'work', [switch]$Server, [switch]$Listen, [string]$Connect = 'auto', [string]$Extra = '',
      [switch]$Background, [switch]$Windowed, [switch]$Dump, [switch]$Sound)
$env:OPENSSL_ia32cap = ':~0x20000000'
$bin = Join-Path $PSScriptRoot "..\game\$Instance\Bin64" | Resolve-Path
$focus = if ($Background) { 'false' } else { 'true' }
# SavesDir/VideoSettings.json overrides -vc, so video settings go into this instance's own copy
function Set-Video([hashtable]$settings) {
    $video = Join-Path $bin '..\SavesDir\VideoSettings.json'
    $cfg = Get-Content $video -Raw | ConvertFrom-Json
    foreach ($k in $settings.Keys) { $cfg.Settings | Add-Member -Force -NotePropertyName $k -NotePropertyValue $settings[$k] }
    # The game asserts if a real-valued setting is written as an integer (ConvertTo-Json turns 0.0
    # into 0), and fails to parse a file with a BOM, then rewrites its fullscreen defaults.
    $json = $cfg | ConvertTo-Json -Depth 4
    $json = [regex]::Replace($json, '("(?:nebula_Quality|r_brightness)":\s*)(-?\d+)(\s*[,\r\n}])', '${1}${2}.0${3}')
    [IO.File]::WriteAllText((Resolve-Path $video), $json, (New-Object Text.UTF8Encoding $false))
}
if ($Server) {
    $loader = if ($Listen) { 'listen_server' } else { 'server' }
    $gameArgs = "-i `$/needle/network/server/$loader.sgpr -d `$/needle_win.bpdeps"
    # the server still renders its debug view: keep it small and cheap (-Windowed: 1280x720)
    $w, $h = if ($Windowed) { 1280, 720 } else { 640, 360 }
    Set-Video @{ r_fullscreen = $false; r_screenWidth = $w; r_screenHeight = $h; r_graphicsQuality = 0
                 shadow_Quality = 0; nebula_Animate = $false; nebula_Light = $false; nebula_Quality = 0.0
                 r_backgroundQuality = 0; r_fxaaEnable = $false; r_vsync = $true }
    # and silent unless -Sound: text edit, since every volume is real-valued and must stay written as
    # one (the game saves settings back, so -Sound has to set it too)
    $game = Join-Path $bin '..\SavesDir\GameSettings.json'
    $volume = if ($Sound) { '100.0' } else { '0.0' }
    $text = [regex]::Replace((Get-Content $game -Raw), '("wwise_masterVolume":\s*)[-\d.]+', "`${1}$volume")
    [IO.File]::WriteAllText((Resolve-Path $game), $text, (New-Object Text.UTF8Encoding $false))
} else {
    $gameArgs = '-i $/needle/loaders/startup.sgpr -l $/needle/ui/loadingscreen/scripts/initial_loading_screen.sgpr -d $/needle_win.bpdeps'
    $gameArgs += " -vc network_ServerAddress=""$Connect"""
    if ($Windowed) { Set-Video @{ r_fullscreen = $false; r_screenWidth = 1280; r_screenHeight = 720 } }
}
# string script vars need double quotes, which are awkward to pass through shells: write them as
# name='value' in -Extra
$gameArgs += " -vc inputRequiresAppFocus=$focus " + ($Extra -replace "'", '"')
$exe = Join-Path $bin 'DeadStar.exe'
if ($Dump) {
    $dumps = New-Item -ItemType Directory -Force (Join-Path $bin '..\Dumps')
    # -e: unhandled exceptions, -ma: full memory, -x: launch the process
    Start-Process -FilePath procdump -WorkingDirectory $bin -ArgumentList "-accepteula -e -ma -x `"$dumps`" `"$exe`" $gameArgs"
} else {
    Start-Process -FilePath $exe -WorkingDirectory $bin -ArgumentList $gameArgs
}
