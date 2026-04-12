<#
.SYNOPSIS
  Sync raw CSVs + static assets to PythonAnywhere, then optionally run imports and reload the web app.

.REQUIREMENTS
  - Windows OpenSSH client (ssh/scp). In PowerShell: Get-Command ssh, Get-Command scp
  - SSH key auth to BoiledEgg1974@ssh.pythonanywhere.com (recommended)

.USAGE
  From repo root:
    powershell -ExecutionPolicy Bypass -File scripts\deploy_pythonanywhere.ps1

  Skip imports / reload:
    powershell -ExecutionPolicy Bypass -File scripts\deploy_pythonanywhere.ps1 -SkipImports -SkipReload
#>

[CmdletBinding()]
param(
    [string]$PaUser = "BoiledEgg1974",
    [string]$RemoteHost = "ssh.pythonanywhere.com",
    [string]$RemoteProject = "/home/BoiledEgg1974/boys-of-winter-hockey-website",
    [string]$RemoteVenvBin = "/home/BoiledEgg1974/boys-of-winter-hockey-website/venv/bin",
    [string]$WsgiFile = "",
    [string]$LocalRepoRoot = "",
    [switch]$SkipImports,
    [switch]$SkipReload
)

$ErrorActionPreference = "Stop"

function Test-CommandExists {
    param([string]$Name)
    return [bool](Get-Command $Name -ErrorAction SilentlyContinue)
}

if (-not (Test-CommandExists "ssh")) {
    Write-Error "OpenSSH 'ssh' not found. Install OpenSSH Client (Windows Optional Feature) or use Git for Windows SSH."
}
if (-not (Test-CommandExists "scp")) {
    Write-Error "OpenSSH 'scp' not found. Install OpenSSH Client (Windows Optional Feature) or use Git for Windows SCP."
}

if (-not $LocalRepoRoot) {
    $LocalRepoRoot = Split-Path -Parent $PSScriptRoot
}
$LocalRepoRoot = (Resolve-Path $LocalRepoRoot).Path

$localRaw = Join-Path $LocalRepoRoot "data\imports\raw"
$localStatic = Join-Path $LocalRepoRoot "app\static"

foreach ($p in @($localRaw, $localStatic)) {
    if (-not (Test-Path $p)) {
        Write-Error "Local path missing: $p"
    }
}

$remote = "${PaUser}@${RemoteHost}"
$destRoot = "${remote}:$RemoteProject"

Write-Host "Local repo:  $LocalRepoRoot"
Write-Host "Remote root: $RemoteProject"
Write-Host ""

Write-Host "Ensuring remote directories exist ..."
$mkdirCmd = "mkdir -p `"$RemoteProject/data/imports/raw`" `"$RemoteProject/app/static`""
& ssh $remote $mkdirCmd

Write-Host "Uploading data/imports/raw -> $RemoteProject/data/imports/raw ..."
# scp -r copies directory contents into target; * = immediate children (league folders + files)
& scp -r "$localRaw\*" "${destRoot}/data/imports/raw/"

Write-Host "Uploading app/static -> $RemoteProject/app/static ..."
& scp -r "$localStatic\*" "${destRoot}/app/static/"

if ($SkipImports) {
    Write-Host "SkipImports: not running server-side imports."
} else {
    Write-Host "Running imports on PythonAnywhere (venv: $RemoteVenvBin) ..."
    $py = "$RemoteVenvBin/python"
    $importPy = "$RemoteProject/scripts/import_data.py"
    # One line for reliable ssh argument passing from PowerShell
    $remoteCmd = (
        "set -euo pipefail; cd $RemoteProject; " +
        "source $RemoteVenvBin/activate; " +
        "export LEAGUE_SLUG=bowl-historical; $py $importPy; " +
        "export LEAGUE_SLUG=bowl-fantasy; $py $importPy; " +
        "export LEAGUE_SLUG=bowl-cap; $py $importPy"
    )
    & ssh $remote bash -lc $remoteCmd
}

if ($SkipReload) {
    Write-Host "SkipReload: not touching web app reload."
} else {
    if (-not $WsgiFile) {
        $WsgiFile = "/var/www/${PaUser}_wsgi.py"
    }
    Write-Host "Reloading web app: touch $WsgiFile (override with -WsgiFile if your Web tab points elsewhere)"
    $reloadCmd = "touch `"$WsgiFile`""
    & ssh $remote $reloadCmd
}

Write-Host ""
Write-Host "Deploy finished."
