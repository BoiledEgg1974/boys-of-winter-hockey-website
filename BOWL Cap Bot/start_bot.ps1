param(
    [string]$EnvFile = ".env"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Import-DotEnvFile([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) { return }
    Get-Content -LiteralPath $Path | ForEach-Object {
        $line = $_.Trim()
        if (-not $line -or $line.StartsWith("#")) { return }
        $idx = $line.IndexOf("=")
        if ($idx -lt 1) { return }
        $name = $line.Substring(0, $idx).Trim()
        $value = $line.Substring($idx + 1).Trim()
        [Environment]::SetEnvironmentVariable($name, $value, "Process")
    }
}

Set-Location -LiteralPath $PSScriptRoot
Import-DotEnvFile -Path (Join-Path $PSScriptRoot $EnvFile)

if (-not $env:SITE_API_BASE_URL) { $env:SITE_API_BASE_URL = "http://127.0.0.1:5000" }
if (-not $env:LEAGUE_SLUG) { $env:LEAGUE_SLUG = "bowl-cap" }

$required = @("DISCORD_BOT_TOKEN", "DISCORD_EVENTS_SHARED_SECRET")
$missing = @($required | Where-Object { [string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable($_, "Process")) })
if ($missing.Count -gt 0) {
    throw "Missing required environment variable(s): $($missing -join ', '). Populate .env or set them in the shell."
}

Write-Host "Starting BOWL Cap bot..." -ForegroundColor Cyan
Write-Host "League: $env:LEAGUE_SLUG" -ForegroundColor DarkGray
Write-Host "API:    $env:SITE_API_BASE_URL" -ForegroundColor DarkGray

python ".\BOWL-Cap-Bot.py"
