# Director Studio — stop backend + frontend started by start.ps1
# Usage: .\kill.ps1
#        .\kill.ps1 -PortsOnly   # only free ports (ignore pid files)
#        .\kill.ps1 -Force       # also kill anything listening on known ports

param(
    [switch]$PortsOnly,
    [switch]$Force,
    [int]$BackendPort = 8790,
    [int]$FrontendPort = 5173
)

$ErrorActionPreference = "Continue"
$Root = $PSScriptRoot
$RunDir = Join-Path $Root ".run"

function Stop-PidTree([int]$ProcessId, [string]$Label) {
    if ($ProcessId -le 0) { return $false }
    $proc = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    if (-not $proc) {
        Write-Host "[$Label] pid $ProcessId already gone" -ForegroundColor DarkGray
        return $false
    }

    Write-Host "[$Label] stopping pid $ProcessId ($($proc.ProcessName)) ..." -ForegroundColor Cyan
    # Kill process tree (uvicorn --reload / npm spawn children)
    & taskkill /PID $ProcessId /T /F 2>$null | Out-Null
    Start-Sleep -Milliseconds 300
    if (Get-Process -Id $ProcessId -ErrorAction SilentlyContinue) {
        Write-Host "[$Label] still alive after taskkill" -ForegroundColor Yellow
        return $false
    }
    Write-Host "[$Label] stopped" -ForegroundColor Green
    return $true
}

function Stop-FromPidFile([string]$Name) {
    $pidFile = Join-Path $RunDir "$Name.pid"
    if (-not (Test-Path $pidFile)) {
        Write-Host "[$Name] no pid file ($pidFile)" -ForegroundColor DarkGray
        return
    }
    $raw = (Get-Content -Path $pidFile -Raw -ErrorAction SilentlyContinue).Trim()
    $processId = 0
    if (-not [int]::TryParse($raw, [ref]$processId)) {
        Write-Host "[$Name] invalid pid file content: $raw" -ForegroundColor Yellow
        Remove-Item -Force $pidFile -ErrorAction SilentlyContinue
        return
    }
    Stop-PidTree -ProcessId $processId -Label $Name | Out-Null
    Remove-Item -Force $pidFile -ErrorAction SilentlyContinue
}

function Stop-ListenersOnPort([int]$Port, [string]$Label) {
    $conns = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    if (-not $conns) {
        Write-Host "[$Label] nothing listening on port $Port" -ForegroundColor DarkGray
        return
    }
    $pids = $conns | Select-Object -ExpandProperty OwningProcess -Unique
    foreach ($processId in $pids) {
        if ($processId -le 0) { continue }
        Stop-PidTree -ProcessId $processId -Label "$Label :$Port" | Out-Null
    }
}

Write-Host "=== Director Studio kill ===" -ForegroundColor White

if (-not $PortsOnly) {
    Stop-FromPidFile "backend"
    Stop-FromPidFile "frontend"
}

if ($PortsOnly -or $Force) {
    Stop-ListenersOnPort -Port $BackendPort -Label "backend"
    Stop-ListenersOnPort -Port $FrontendPort -Label "frontend"
}

# Fallback: if pid files missing / stale, still free default ports when not PortsOnly
if (-not $PortsOnly -and -not $Force) {
    $backendAlive = Get-NetTCPConnection -LocalPort $BackendPort -State Listen -ErrorAction SilentlyContinue
    $frontendAlive = Get-NetTCPConnection -LocalPort $FrontendPort -State Listen -ErrorAction SilentlyContinue
    if ($backendAlive -or $frontendAlive) {
        Write-Host "Some ports still in use; cleaning with -Force style..." -ForegroundColor Yellow
        if ($backendAlive) { Stop-ListenersOnPort -Port $BackendPort -Label "backend" }
        if ($frontendAlive) { Stop-ListenersOnPort -Port $FrontendPort -Label "frontend" }
    }
}

Write-Host "Done." -ForegroundColor Green
