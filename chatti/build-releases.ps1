# Builds every shipping firmware variant into releases\, one directory each.
#
# Why this exists: build.py leaves exactly one result behind in build\, and the
# language is baked in at compile time (CONFIG_LANGUAGE_* -> lang_config.h). Two
# languages therefore mean two builds, and the second one overwrites the first.
# This script runs them in turn and copies each result out before the next one
# starts.
#
# What lands in releases\<variant>\ is the build's own image set at its own
# offsets, plus flash_args - not merged-binary.bin alone. That distinction
# matters: writing the merged image at 0x0 fills the gap at 0x9000 with 0xFF and
# erases NVS, so Wi-Fi and the server address are gone (see CLAUDE.md section 4).
# merged-binary.bin is copied along anyway, because a first install wants it.
#
# The chatti-firmware.json written next to the images is what Chatti Control
# reads to label the variant in its dropdown. Without it the panel falls back to
# the directory name, which works but reads worse.
#
# Usage - from anywhere, the ESP-IDF environment is set up here:
#     powershell -File chatti\build-releases.ps1
#     powershell -File chatti\build-releases.ps1 -Only en-US

param(
    [string]$Board = 'waveshare/esp32-s3-touch-lcd-1.83',
    [string]$Only = ''
)

# Deliberately NOT 'Stop': ESP-IDF's export.ps1 writes its banner to stderr, and
# PowerShell 5.1 turns a native command's stderr into a terminating
# NativeCommandError. Exit codes are checked by hand instead.
$ErrorActionPreference = 'Continue'

$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo

# The order is the order Chatti Control offers them in; the first one is the
# default it preselects.
$variants = @(
    [pscustomobject]@{ Language = 'en-US'; Dir = 'chatti-eng'; Label = 'English'; Name = 'Chatti-ENG' },
    [pscustomobject]@{ Language = 'de-DE'; Dir = 'chatti-de';  Label = 'Deutsch'; Name = 'Chatti-DE'  }
)
if ($Only) { $variants = $variants | Where-Object { $_.Language -eq $Only } }
if (-not $variants) { Write-Host "FATAL: no variant matches -Only $Only"; exit 1 }

# Single source of truth for the version: the same line build.py reads.
$version = (Select-String -Path 'CMakeLists.txt' -Pattern '^set\(PROJECT_VER "(.+)"\)').Matches[0].Groups[1].Value
if (-not $version) { Write-Host 'FATAL: PROJECT_VER not found in CMakeLists.txt'; exit 1 }
Write-Host "Project version: $version"

. .\chatti\idf-init.ps1
if (-not $env:IDF_PATH) { Write-Host 'FATAL: ESP-IDF environment not initialized'; exit 1 }

function Save-Variant($v, $version) {
    $out = Join-Path $repo "releases\$($v.Dir)-v$version"
    if (Test-Path $out) { Remove-Item $out -Recurse -Force }
    New-Item -ItemType Directory -Force $out | Out-Null

    # Straight from the build's own manifest, so a new image in the partition
    # table is picked up without touching this script.
    $fa = Get-Content 'build\flasher_args.json' -Raw | ConvertFrom-Json
    foreach ($p in $fa.flash_files.PSObject.Properties) {
        $dst = Join-Path $out $p.Value
        New-Item -ItemType Directory -Force (Split-Path $dst) | Out-Null
        Copy-Item (Join-Path 'build' $p.Value) $dst -Force
        Write-Host "  $($p.Name)  $($p.Value)"
    }
    Copy-Item 'build\flasher_args.json' $out -Force
    foreach ($extra in @('build\flash_args', 'build\xiaozhi.elf', 'build\merged-binary.bin')) {
        if (Test-Path $extra) { Copy-Item $extra $out -Force }
    }

    [pscustomobject]@{
        name     = $v.Name
        label    = $v.Label
        language = $v.Language
        version  = $version
        board    = $Board
    } | ConvertTo-Json | Set-Content (Join-Path $out 'chatti-firmware.json') -Encoding utf8
    Write-Host "saved -> $out"
}

foreach ($v in $variants) {
    Write-Host "=============== BUILD $($v.Language) ==============="
    python chatti\build-win.py $Board --language $v.Language
    if ($LASTEXITCODE -ne 0) { Write-Host "FATAL: $($v.Language) build failed ($LASTEXITCODE)"; exit 1 }
    # Not fatal: the per-offset images above are what the panel flashes, the
    # merged image is only a convenience for a first install.
    idf.py -C . merge-bin
    Save-Variant $v $version
}

Write-Host '=============== DONE ==============='
