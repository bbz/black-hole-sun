# Post key presses to the Dead Star window only (never to whatever has focus).
# The game must be started with tools/run_work.ps1 -Background.
# Usage: powershell -File tools/sendkey.ps1 [-Instance work|observer|...] Return Escape Down ...
#        (System.Windows.Forms.Keys names)
param([string]$Instance = 'work', [Parameter(Mandatory, ValueFromRemainingArguments)][string[]]$Keys)
Add-Type -AssemblyName System.Windows.Forms
Add-Type @'
using System;
using System.Runtime.InteropServices;
public static class Msg {
    [DllImport("user32.dll")] public static extern bool PostMessage(IntPtr h, uint m, IntPtr w, IntPtr l);
    [DllImport("user32.dll")] public static extern uint MapVirtualKey(uint code, uint type);
}
'@
$proc = Get-Process DeadStar -ErrorAction SilentlyContinue | Where-Object { $_.Path -like "*\game\$Instance\Bin64\*" } | Select-Object -First 1
if (-not $proc -or $proc.MainWindowHandle -eq 0) { throw 'Dead Star window not found' }
$h = $proc.MainWindowHandle
foreach ($k in $Keys) {
    $vk = [uint32][System.Windows.Forms.Keys]$k
    $scan = [Msg]::MapVirtualKey($vk, 0)
    [Msg]::PostMessage($h, 0x100, [IntPtr]$vk, [IntPtr](1 -bor ($scan -shl 16))) | Out-Null          # WM_KEYDOWN
    Start-Sleep -Milliseconds 120
    [Msg]::PostMessage($h, 0x101, [IntPtr]$vk, [IntPtr](1 -bor ($scan -shl 16) -bor 0xC0000000)) | Out-Null  # WM_KEYUP
    Start-Sleep -Milliseconds 400
}
