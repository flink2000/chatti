# Initializes an ESP-IDF environment in the current PowerShell session.
#
# Usage:
#   . chatti\idf-init.ps1                 # ESP-IDF 6.0.2 (default, what xiaozhi needs)
#   . chatti\idf-init.ps1 -Version 5.4.4  # ESP-IDF 5.4.4
#
# The environment applies to THIS shell only, never system-wide.
#
# Set CHATTI_IDF_TOOLS to point at your Espressif installation if it is not in
# one of the usual places (the installer's default is %USERPROFILE%\.espressif;
# this project was developed with it on D: because C: was short on space).
#
# Why the PATH is prepended manually instead of just calling export.ps1:
#   export.ps1 derives the name of the Python virtualenv from whichever "python"
#   comes first in PATH. A system Python 3.13 will send it looking for a py3.13
#   env that does not exist, while the ESP-IDF envs are built on the bundled
#   Python 3.11 (idf5.4_py3.11_env / idf6.0_py3.11_env), and it aborts.
#
# Why not Initialize-Idf.ps1 (the launcher the installer creates):
#   It asks idf-env for the interpreter path, and idf-env only knows the
#   installation under a path spelled with forward slashes and a trailing slash.
#   Any normally written IDF_PATH fails to match and it returns "null".

param(
    [ValidateSet('6.0.2', '5.4.4')]
    [string]$Version = '6.0.2'
)

# --- find the Espressif installation ---------------------------------------
# Searched rather than hardcoded: the installer offers a target directory, and
# a fixed path would only ever work on the machine it was written on.
$candidates = @()
if ($env:CHATTI_IDF_TOOLS) { $candidates += $env:CHATTI_IDF_TOOLS }
if ($env:IDF_TOOLS_PATH)   { $candidates += $env:IDF_TOOLS_PATH }
$candidates += (Join-Path $env:USERPROFILE '.espressif')
$candidates += @('C:\Espressif', 'D:\Espressif', 'E:\Espressif', 'F:\Espressif')

$toolsPath = $null
$idfPath = $null
foreach ($c in $candidates) {
    if (-not $c) { continue }
    $p = Join-Path $c "frameworks\esp-idf-v$Version"
    if (Test-Path (Join-Path $p 'export.ps1')) {
        $toolsPath = $c
        $idfPath = $p
        break
    }
}

if (-not $idfPath) {
    throw ("ESP-IDF $Version nicht gefunden. Gesucht unter:`n  " +
           (($candidates | Where-Object { $_ }) -join "`n  ") +
           "`nSetze CHATTI_IDF_TOOLS auf dein Espressif-Verzeichnis.")
}

$env:IDF_TOOLS_PATH   = $toolsPath
$env:PYTHONNOUSERSITE = 'True'
$env:PYTHONPATH       = $null

# The bundled Python and Git, by whatever version number this installation uses.
# Pinning "3.11.2" and "2.44.0" broke as soon as the tools were updated.
$prepend = @()
foreach ($tool in @('idf-python', 'idf-git')) {
    $dir = Join-Path $toolsPath "tools\$tool"
    if (-not (Test-Path $dir)) { continue }
    $newest = Get-ChildItem $dir -Directory -ErrorAction SilentlyContinue |
              Sort-Object Name -Descending | Select-Object -First 1
    if (-not $newest) { continue }
    # idf-git keeps its executables one level further down, under cmd\.
    $bin = if ($tool -eq 'idf-git') { Join-Path $newest.FullName 'cmd' } else { $newest.FullName }
    if (Test-Path $bin) { $prepend += $bin }
}
if ($prepend) { $env:PATH = ($prepend -join ';') + ';' + $env:PATH }

. "$idfPath\export.ps1"
