param(
    [ValidateSet("regular", "fullremoterebuild")]
    [string]$Mode = "regular",
    [switch]$AllowStale,
    [switch]$NoPush,
    [switch]$NoDeploy,
    [switch]$RemotePip,
    [switch]$SyncApCatalogLocal
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
Set-Location -LiteralPath $repoRoot

function Import-DeployEnvFile {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return }
    Get-Content -LiteralPath $Path | ForEach-Object {
        $line = $_.Trim()
        if (-not $line -or $line.StartsWith("#") -or $line -notmatch "=") { return }
        $parts = $line -split "=", 2
        $key = $parts[0].Trim()
        $val = $parts[1].Trim().Trim('"')
        if ($key) { Set-Item -Path "Env:$key" -Value $val }
    }
}

Import-DeployEnvFile (Join-Path $scriptDir "deploy-live-vps.env.example")
Import-DeployEnvFile (Join-Path $scriptDir "deploy-live-vps.env")

$argsList = @("scripts/run_site_update.py", "bowl")
$argsList += "--mode"
$argsList += $Mode
if ($AllowStale) { $argsList += "--allow-stale" }
if ($NoPush) { $argsList += "--no-push" }
if ($NoDeploy) { $argsList += "--no-deploy" }
if ($RemotePip) { $argsList += "--remote-pip" }
if ($SyncApCatalogLocal) { $argsList += "--sync-ap-catalog-local" }

Write-Host "Running site update (bowl workflow → DigitalOcean VPS by default)..." -ForegroundColor Cyan
Write-Host ("Deploy host: " + $(if ($env:PA_HOST) { $env:PA_HOST } else { "(from deploy-live-vps.env)" })) -ForegroundColor DarkGray
Write-Host ("Command: python " + ($argsList -join " ")) -ForegroundColor DarkGray

python @argsList
