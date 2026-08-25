# Puts the Chatti shortcut on the desktop.
#
# The shortcut is not checked in as a .lnk: that file stores an absolute path,
# so a committed one would point at the machine it was made on. It is generated
# here instead, from wherever this clone happens to sit.

$ErrorActionPreference = 'Stop'

$here   = Split-Path -Parent $MyInvocation.MyCommand.Path
$target = Join-Path $here 'chatti-control.cmd'
$icon   = Join-Path $here 'chatti.ico'

if (-not (Test-Path $target)) {
    throw "Not found: $target - run this from the clone, not from a copy."
}

# GetFolderPath follows a Desktop redirected into OneDrive; %USERPROFILE%\Desktop does not.
$desktop = [Environment]::GetFolderPath('Desktop')
$link    = Join-Path $desktop 'Chatti.lnk'

$shortcut = (New-Object -ComObject WScript.Shell).CreateShortcut($link)
$shortcut.TargetPath       = $target
$shortcut.WorkingDirectory = $here
$shortcut.IconLocation     = "$icon,0"
# 7 = minimised. The console window is the off switch, not something to look at.
$shortcut.WindowStyle      = 7
$shortcut.Description      = 'Starts Chatti Control and opens it in the browser'
$shortcut.Save()

Write-Host ""
Write-Host "Shortcut created: $link"
Write-Host "  -> $target"
