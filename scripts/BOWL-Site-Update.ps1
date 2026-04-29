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

$argsList = @("scripts/BOWL-Site-Update.py")
$argsList += "--mode"
$argsList += $Mode
if ($AllowStale) { $argsList += "--allow-stale" }
if ($NoPush) { $argsList += "--no-push" }
if ($NoDeploy) { $argsList += "--no-deploy" }
if ($RemotePip) { $argsList += "--remote-pip" }
if ($SyncApCatalogLocal) { $argsList += "--sync-ap-catalog-local" }

Write-Host "Running BOWL-Site-Update (includes OVR baseline refresh on local league DBs)..." -ForegroundColor Cyan
Write-Host ("Command: python " + ($argsList -join " ")) -ForegroundColor DarkGray

python @argsList

