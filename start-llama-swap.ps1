# Start the local llama-swap proxy independently from Director Studio.
# Usage: .\start-llama-swap.ps1 [-ExePath ...] [-ConfigPath ...] [-Port 11435]
param(
    [string]$ExePath = 'D:\DirectorStudio-llama-swap-eval\llama-swap\llama-swap.exe',
    [string]$ConfigPath = 'D:\DirectorStudio-llama-swap-eval\config.yaml',
    [ValidateRange(1, 65535)][int]$Port = 11435,
    [string]$RunDir = (Join-Path $PSScriptRoot '.run'),
    [switch]$ValidateOnly
)

$ErrorActionPreference = 'Stop'

if (-not $ValidateOnly) {
    $client = [System.Net.Sockets.TcpClient]::new()
    try {
        $pending = $client.BeginConnect('127.0.0.1', $Port, $null, $null)
        if ($pending.AsyncWaitHandle.WaitOne(300) -and $client.Connected) {
            # Reuse a healthy proxy without changing its process, PID or logs.
            $healthy = $false
            try {
                $response = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 2 -UseBasicParsing
                $healthy = $response.StatusCode -eq 200 -and $response.Content.Trim() -eq 'OK'
            } catch { }
            if ($healthy) {
                Write-Host "llama-swap already running at http://127.0.0.1:$Port - skip start"
                return
            }
            throw "Port $Port is already in use. Existing service was not changed."
        }
    } finally {
        $client.Dispose()
    }
}

if (-not (Test-Path -LiteralPath $ExePath -PathType Leaf)) {
    throw "llama-swap executable not found: $ExePath"
}
if (-not (Test-Path -LiteralPath $ConfigPath -PathType Leaf)) {
    throw "llama-swap config not found: $ConfigPath"
}

$exe = (Resolve-Path -LiteralPath $ExePath).Path
$config = (Resolve-Path -LiteralPath $ConfigPath).Path

$validation = & $exe -config $config -validate 2>&1
if ($LASTEXITCODE -ne 0) {
    throw "llama-swap config validation failed: $validation"
}
if ($ValidateOnly) {
    Write-Host "llama-swap config valid: $config"
    exit 0
}

New-Item -ItemType Directory -Force -Path $RunDir | Out-Null
$run = (Resolve-Path -LiteralPath $RunDir).Path
$logOut = Join-Path $run 'llama-swap.out.log'
$logErr = Join-Path $run 'llama-swap.err.log'
$pidFile = Join-Path $run 'llama-swap.pid'
$workDir = Split-Path -Parent $config
# cmd owns the log redirection. Avoid a live process inheriting this script's
# output pipes (which otherwise keeps callers that capture output blocked).
$command = '/d /v:off /c cd /d "' + $workDir + '" && "' + $exe +
    '" -config "' + $config + '" -listen 127.0.0.1:' + $Port +
    ' > "' + $logOut + '" 2> "' + $logErr + '"'
$process = Start-Process -FilePath $env:ComSpec -ArgumentList $command `
    -WindowStyle Hidden -PassThru

try {
    $deadline = [DateTime]::UtcNow.AddSeconds(10)
    $ready = $false
    while ([DateTime]::UtcNow -lt $deadline) {
        if ($process.HasExited) { break }
        try {
            $response = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 1 -UseBasicParsing
            if ($response.StatusCode -eq 200 -and $response.Content.Trim() -eq 'OK') {
                $ready = $true
                break
            }
        } catch { }
        Start-Sleep -Milliseconds 200
    }
    if (-not $ready) {
        throw "llama-swap did not become healthy; inspect $logErr"
    }
    Set-Content -LiteralPath $pidFile -Value $process.Id -Encoding ascii
    Write-Host "llama-swap ready at http://127.0.0.1:$Port (PID $($process.Id))"
    Write-Host "Logs: $logOut ; $logErr"
} catch {
    if (-not $process.HasExited) { & taskkill /PID $process.Id /T /F 2>$null | Out-Null }
    throw
}
