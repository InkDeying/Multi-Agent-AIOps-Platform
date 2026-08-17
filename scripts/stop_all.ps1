# Multi-Agent AIOps Platform - full local Windows stopper
#
# Stops processes started by scripts/run_all.ps1. Docker infrastructure is kept
# running by default; pass -Infra to run `docker compose down` as well.
#
# Examples:
#   .\scripts\stop_all.ps1
#   .\scripts\stop_all.ps1 -Infra

[CmdletBinding()]
param(
    [switch]$Infra
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$RunDir = Join-Path $ProjectRoot ".run"
Set-Location $ProjectRoot

function Get-RecordedPid {
    param([string]$PidFile)

    if (-not (Test-Path -LiteralPath $PidFile)) {
        return $null
    }

    $raw = Get-Content -LiteralPath $PidFile -ErrorAction SilentlyContinue | Select-Object -First 1
    $pidValue = 0
    if ([int]::TryParse([string]$raw, [ref]$pidValue) -and $pidValue -gt 0) {
        return $pidValue
    }
    return $null
}

function Stop-ProcessTree {
    param(
        [int]$ProcessId,
        [string]$Name
    )

    $process = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    if (-not $process) {
        Write-Host "[skip] $Name is not running (pid=$ProcessId)" -ForegroundColor DarkYellow
        return
    }

    Write-Host "[stop] $Name (pid=$ProcessId)" -ForegroundColor Yellow
    # taskkill /T is needed for Uvicorn's supervisor and its child workers.
    & taskkill.exe /PID $ProcessId /T /F | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
    }
}

if (Test-Path -LiteralPath $RunDir) {
    Get-ChildItem -LiteralPath $RunDir -Filter '*.pid' -File -ErrorAction SilentlyContinue |
        Sort-Object Name |
        ForEach-Object {
            $pidValue = Get-RecordedPid -PidFile $_.FullName
            if ($pidValue) {
                Stop-ProcessTree -ProcessId $pidValue -Name $_.BaseName
            }
            Remove-Item -LiteralPath $_.FullName -Force -ErrorAction SilentlyContinue
        }
}

# Fallback for processes whose PID file was removed or whose launcher crashed.
# Match the current project path so unrelated Python/Uvicorn applications stay up.
$projectPattern = [regex]::Escape($ProjectRoot)
$orphaned = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object {
        $_.ProcessId -ne $PID -and
        $_.CommandLine -and
        $_.CommandLine -match $projectPattern -and
        (
            $_.CommandLine -match 'uvicorn(?:\.exe)?\s+app\.main:app' -or
            $_.CommandLine -match 'app\.diagnosis_worker' -or
            $_.CommandLine -match 'mcp_servers\\.*_server\.py'
        )
    }

foreach ($process in $orphaned) {
    Stop-ProcessTree -ProcessId ([int]$process.ProcessId) -Name "orphan-$($process.ProcessId)"
}

if ($Infra) {
    Write-Host "[stop] Docker infrastructure (docker compose down)..." -ForegroundColor Yellow
    docker compose down
    if ($LASTEXITCODE -ne 0) {
        throw "docker compose down failed"
    }
}

Write-Host "[stop_all] complete. Docker volumes were not removed." -ForegroundColor Green
