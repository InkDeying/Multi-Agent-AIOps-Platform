# Multi-Agent AIOps Platform - full local Windows launcher
#
# Starts the same topology as scripts/run_all.sh:
#   1. Docker infrastructure: Redis, Postgres, etcd, MinIO, Milvus, open-webSearch
#   2. Local MCP servers
#   3. Local multi-process Uvicorn API
#   4. Local diagnosis workers
#
# Examples:
#   .\scripts\run_all.ps1
#   .\scripts\run_all.ps1 -Workers 5 -UvicornWorkers 2
#   .\scripts\run_all.ps1 -SkipInfra
#   .\scripts\run_all.ps1 -SkipMcp
#   .\scripts\run_all.ps1 -PythonPath .\.venv\Scripts\python.exe

[CmdletBinding()]
param(
    [ValidateRange(1, 32)]
    [int]$Workers = 3,

    [ValidateRange(1, 16)]
    [int]$UvicornWorkers = 4,

    [ValidateRange(1, 65535)]
    [int]$AppPort = 9900,

    [string]$PythonPath = "",

    [switch]$SkipInfra,
    [switch]$SkipMcp
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$LogDir = Join-Path $ProjectRoot "logs"
$RunDir = Join-Path $ProjectRoot ".run"

New-Item -ItemType Directory -Force -Path $LogDir, $RunDir | Out-Null
Set-Location $ProjectRoot

function Resolve-PythonPath {
    param([string]$RequestedPath)

    if ($RequestedPath) {
        $resolved = Resolve-Path -LiteralPath $RequestedPath -ErrorAction SilentlyContinue
        if (-not $resolved) {
            throw "Python interpreter not found: $RequestedPath"
        }
        return $resolved.Path
    }

    $venvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $venvPython) {
        return $venvPython
    }

    if ($env:CONDA_PREFIX) {
        $condaPython = Join-Path $env:CONDA_PREFIX "python.exe"
        if (Test-Path -LiteralPath $condaPython) {
            return $condaPython
        }
    }

    $command = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }

    throw "Python not found. Create .venv or pass -PythonPath explicitly."
}

function Test-TcpPort {
    param(
        [string]$HostName = "127.0.0.1",
        [int]$Port,
        [int]$TimeoutMs = 500
    )

    $client = [System.Net.Sockets.TcpClient]::new()
    try {
        $connect = $client.ConnectAsync($HostName, $Port)
        return $connect.Wait($TimeoutMs) -and $client.Connected
    } catch {
        return $false
    } finally {
        $client.Dispose()
    }
}

function Wait-TcpPort {
    param(
        [string]$Name,
        [int]$Port,
        [int]$TimeoutSec = 30
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        if (Test-TcpPort -Port $Port) {
            Write-Host "[ready] $Name is listening on 127.0.0.1:$Port" -ForegroundColor Green
            return $true
        }
        Start-Sleep -Milliseconds 500
    }

    Write-Host "[warn] $Name did not become ready on port $Port within ${TimeoutSec}s" -ForegroundColor Yellow
    return $false
}

function Test-DockerCompose {
    try {
        docker compose version *> $null
        return $LASTEXITCODE -eq 0
    } catch {
        return $false
    }
}

function Test-RecordedProcess {
    param([string]$PidFile)

    if (-not (Test-Path -LiteralPath $PidFile)) {
        return $false
    }

    $savedPid = Get-Content -LiteralPath $PidFile -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $savedPid) {
        return $false
    }

    return $null -ne (Get-Process -Id ([int]$savedPid) -ErrorAction SilentlyContinue)
}

function Start-PythonProcess {
    param(
        [string]$Name,
        [string[]]$Arguments,
        [int]$ReadyPort = 0,
        [int]$ReadyTimeoutSec = 30
    )

    $pidFile = Join-Path $RunDir "$Name.pid"
    if (Test-RecordedProcess -PidFile $pidFile) {
        $savedPid = Get-Content -LiteralPath $pidFile | Select-Object -First 1
        Write-Host "[skip] $Name already running (pid=$savedPid)" -ForegroundColor DarkYellow
        return
    }

    if ($ReadyPort -gt 0 -and (Test-TcpPort -Port $ReadyPort)) {
        Write-Host "[skip] $Name port $ReadyPort is already in use" -ForegroundColor DarkYellow
        return
    }

    $stdout = Join-Path $LogDir "$Name.out.log"
    $stderr = Join-Path $LogDir "$Name.err.log"
    Write-Host "[start] $Name" -ForegroundColor Cyan

    $process = Start-Process `
        -FilePath $script:Python `
        -ArgumentList $Arguments `
        -WorkingDirectory $ProjectRoot `
        -WindowStyle Hidden `
        -RedirectStandardOutput $stdout `
        -RedirectStandardError $stderr `
        -PassThru

    Set-Content -LiteralPath $pidFile -Value $process.Id -Encoding ASCII
    Write-Host "        pid=$($process.Id), logs=$stdout / $stderr"

    Start-Sleep -Milliseconds 500
    if ($process.HasExited) {
        throw "$Name exited during startup (exit=$($process.ExitCode)). Check $stderr"
    }

    if ($ReadyPort -gt 0) {
        $ready = Wait-TcpPort -Name $Name -Port $ReadyPort -TimeoutSec $ReadyTimeoutSec
        if (-not $ready) {
            Write-Host "       stderr: $stderr" -ForegroundColor Yellow
        }
    }
}

if (-not (Test-Path -LiteralPath (Join-Path $ProjectRoot ".env"))) {
    throw ".env not found. Create it from .env.example and configure providers first."
}

$script:Python = Resolve-PythonPath -RequestedPath $PythonPath
Write-Host "[run_all] project=$ProjectRoot"
Write-Host "[run_all] python=$script:Python"

& $script:Python --version
if ($LASTEXITCODE -ne 0) {
    throw "Python interpreter check failed: $script:Python"
}

if (-not $SkipInfra) {
    if (-not (Test-DockerCompose)) {
        throw "Docker Compose is unavailable. Start Docker Desktop and verify 'docker version'."
    }

    Write-Host "[run_all] starting Docker infrastructure..." -ForegroundColor Cyan
    docker compose up -d redis postgres etcd minio standalone open-websearch
    if ($LASTEXITCODE -ne 0) {
        throw "Docker infrastructure startup failed. Run 'docker compose ps -a' and inspect the service logs."
    }

    Wait-TcpPort -Name "Redis" -Port 6379 -TimeoutSec 45 | Out-Null
    Wait-TcpPort -Name "Postgres" -Port 5432 -TimeoutSec 45 | Out-Null
    Wait-TcpPort -Name "Milvus" -Port 19530 -TimeoutSec 120 | Out-Null
    Wait-TcpPort -Name "open-webSearch" -Port 3310 -TimeoutSec 120 | Out-Null
} else {
    Write-Host "[skip] Docker infrastructure startup disabled" -ForegroundColor DarkYellow
}

# MCP must start before the API because the API loads MCP tools during lifespan startup.
if (-not $SkipMcp) {
    $mcpServers = @(
        @{ Name = "mcp-system"; Script = "mcp_servers/system_server.py"; Port = 9105 },
        @{ Name = "mcp-websearch"; Script = "mcp_servers/websearch_server.py"; Port = 9106 },
        @{ Name = "mcp-winlog"; Script = "mcp_servers/winlog_server.py"; Port = 9108 },
        @{ Name = "mcp-network"; Script = "mcp_servers/network_server.py"; Port = 9109 },
        @{ Name = "mcp-docker"; Script = "mcp_servers/docker_server.py"; Port = 9111 }
    )

    foreach ($server in $mcpServers) {
        Start-PythonProcess `
            -Name $server.Name `
            -Arguments @($server.Script) `
            -ReadyPort $server.Port `
            -ReadyTimeoutSec 30
    }
} else {
    Write-Host "[skip] local MCP startup disabled" -ForegroundColor DarkYellow
}

Start-PythonProcess `
    -Name "api" `
    -Arguments @(
        "-m", "uvicorn", "app.main:app",
        "--host", "0.0.0.0",
        "--port", "$AppPort",
        "--workers", "$UvicornWorkers"
    ) `
    -ReadyPort $AppPort `
    -ReadyTimeoutSec 120

for ($index = 1; $index -le $Workers; $index++) {
    $workerName = "worker-$index"
    Start-PythonProcess `
        -Name $workerName `
        -Arguments @("-m", "app.diagnosis_worker", "--name", $workerName)
}

Write-Host ""
Write-Host "[run_all] all requested services have been started." -ForegroundColor Green
Write-Host "  Web UI:      http://localhost:$AppPort"
Write-Host "  API docs:    http://localhost:$AppPort/docs"
Write-Host "  Queue state: http://localhost:$AppPort/api/v1/queue/status"
Write-Host "  MCP ports:   9105, 9106, 9108, 9109, 9111"
Write-Host "  Logs:        Get-Content .\logs\api.err.log -Wait"
Write-Host "               Get-Content .\logs\worker-1.err.log -Wait"
Write-Host "  Status:      docker compose ps"
