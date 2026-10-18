# Save a screenshot of the Dead Star window's own contents to a PNG.
# Uses PrintWindow, so other windows on top are never captured; fails if the game isn't running.
# Usage: powershell -File tools/screenshot.ps1 OUT.png [-Instance work|server|...]
param([Parameter(Mandatory)][string]$Out, [string]$Instance = 'work')
Add-Type -AssemblyName System.Drawing
Add-Type @'
using System;
using System.Runtime.InteropServices;
public static class Win {
    [StructLayout(LayoutKind.Sequential)] public struct RECT { public int L, T, R, B; }
    [DllImport("user32.dll")] public static extern bool GetClientRect(IntPtr h, out RECT r);
    [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr h, IntPtr hdc, uint flags);
    [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
    [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr h);
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int cmd);
}
'@
[Win]::SetProcessDPIAware() | Out-Null
$proc = Get-Process DeadStar -ErrorAction SilentlyContinue | Where-Object { $_.Path -like "*\game\$Instance\Bin64\*" } | Select-Object -First 1
if (-not $proc -or $proc.MainWindowHandle -eq 0) { throw 'Dead Star window not found' }
# a minimized window has no client area; restore it without activating (SW_SHOWNOACTIVATE)
if ([Win]::IsIconic($proc.MainWindowHandle)) { [Win]::ShowWindow($proc.MainWindowHandle, 4) | Out-Null; Start-Sleep -Milliseconds 500 }
$r = New-Object Win+RECT
[Win]::GetClientRect($proc.MainWindowHandle, [ref]$r) | Out-Null
$bmp = New-Object System.Drawing.Bitmap ($r.R - $r.L), ($r.B - $r.T)
$g = [System.Drawing.Graphics]::FromImage($bmp)
$hdc = $g.GetHdc()
# 1 = PW_CLIENTONLY, 2 = PW_RENDERFULLCONTENT (needed for DirectX windows)
[Win]::PrintWindow($proc.MainWindowHandle, $hdc, 3) | Out-Null
$g.ReleaseHdc($hdc)
$bmp.Save($Out, [System.Drawing.Imaging.ImageFormat]::Png)
"$($bmp.Width)x$($bmp.Height) -> $Out"
