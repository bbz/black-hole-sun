# Build tools/bhs/bhs.dll with MinGW-w64 (scoop install mingw). Static runtime, so the DLL needs
# nothing next to it.
$src = Join-Path $PSScriptRoot 'bhs.cpp'
$out = Join-Path $PSScriptRoot 'bhs.dll'
& g++ -std=c++17 -O2 -Wall -shared -static -s -o $out $src -lole32
if ($LASTEXITCODE) { exit $LASTEXITCODE }
"built $out"
