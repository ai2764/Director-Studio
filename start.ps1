# Director Studio — start backend + frontend (dev)
# Spawns fully detached children so THIS terminal stays interactive.
#
# Usage:
#   .\start.ps1
#   .\start.ps1 -BackendOnly
#   .\start.ps1 -FrontendOnly

param(
    [switch]$BackendOnly,
    [switch]$FrontendOnly,
    [int]$BackendPort = 8790,
    [int]$FrontendPort = 5173
)

$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
$RunDir = Join-Path $Root ".run"
$BackendDir = Join-Path $Root "backend"
$FrontendDir = Join-Path $Root "frontend"

New-Item -ItemType Directory -Force -Path $RunDir | Out-Null

function Test-PortInUse([int]$Port) {
    try {
        $client = [System.Net.Sockets.TcpClient]::new()
        $iar = $client.BeginConnect("127.0.0.1", $Port, $null, $null)
        $ok = $iar.AsyncWaitHandle.WaitOne(200)
        $alive = $ok -and $client.Connected
        if ($alive) { try { $client.EndConnect($iar) } catch {} }
        $client.Close()
        return $alive
    } catch {
        return $false
    }
}

function Get-PythonCmd {
    $candidates = [System.Collections.Generic.List[string]]::new()

    foreach ($path in @(
        $env:DS_PYTHON_EXE,
        (Join-Path $BackendDir ".venv\Scripts\python.exe"),
        (Join-Path $Root ".venv\Scripts\python.exe"),
        $(if ($env:VIRTUAL_ENV) { Join-Path $env:VIRTUAL_ENV "Scripts\python.exe" }),
        $(if ($env:CONDA_PREFIX) { Join-Path $env:CONDA_PREFIX "python.exe" })
    )) {
        if ($path) { $candidates.Add($path) }
    }

    $pyenvVersions = Join-Path $env:USERPROFILE ".pyenv\pyenv-win\versions"
    if (Test-Path $pyenvVersions) {
        Get-ChildItem $pyenvVersions -Directory -ErrorAction SilentlyContinue |
            Sort-Object Name -Descending |
            ForEach-Object {
                $candidates.Add((Join-Path $_.FullName "python.exe"))
            }
    }

    Get-Command python -All -ErrorAction SilentlyContinue |
        Where-Object { $_.Source } |
        ForEach-Object { $candidates.Add($_.Source) }

    $seen = @{}
    foreach ($candidate in $candidates) {
        if (-not $candidate -or $seen.ContainsKey($candidate)) { continue }
        $seen[$candidate] = $true
        if (-not (Test-Path $candidate -PathType Leaf)) { continue }
        try {
            $probeCode = "import sys; sys.path.insert(0, r'$BackendDir'); import uvicorn, app.main; print(sys.executable)"
            $probe = & $candidate -c $probeCode 2>$null
            if ($LASTEXITCODE -eq 0 -and $probe) {
                return (Resolve-Path $candidate).Path
            }
        } catch {
            continue
        }
    }
    throw "No usable Python interpreter found. Set DS_PYTHON_EXE or create backend\.venv; the interpreter must import uvicorn and app.main."
}

function Get-NodeCmd {
    $cmd = Get-Command node -ErrorAction SilentlyContinue
    if ($cmd -and $cmd.Source -and (Test-Path $cmd.Source)) {
        return $cmd.Source
    }
    throw "node not found. Install Node.js and ensure it is on PATH."
}

function Start-DetachedProcess {
    <#
      Start an executable completely detached from this console.
      - CreateNoWindow + redirected stdout/stderr to log files
      - RedirectStandardInput so the child cannot read this terminal
      - Async discard of stream buffers via File.Write in background is
        handled by .NET Process StartInfo redirect to files via shell=false
        and BeginOutputReadLine writing to files.
    #>
    param(
        [Parameter(Mandatory)][string]$FilePath,
        [Parameter(Mandatory)][string[]]$ArgumentList,
        [Parameter(Mandatory)][string]$WorkingDirectory,
        [Parameter(Mandatory)][string]$LogOut,
        [Parameter(Mandatory)][string]$LogErr,
        [Parameter(Mandatory)][string]$PidFile
    )

    # Truncate logs
    [System.IO.File]::WriteAllText($LogOut, "")
    [System.IO.File]::WriteAllText($LogErr, "")

    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $FilePath
    # ProcessStartInfo.Arguments is a single string on Windows
    $psi.Arguments = ($ArgumentList | ForEach-Object {
        if ($_ -match '[\s"]') { '"' + ($_ -replace '"', '\"') + '"' } else { $_ }
    }) -join ' '
    $psi.WorkingDirectory = $WorkingDirectory
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.RedirectStandardInput = $true
    $psi.StandardOutputEncoding = [System.Text.Encoding]::UTF8
    $psi.StandardErrorEncoding = [System.Text.Encoding]::UTF8

    $proc = New-Object System.Diagnostics.Process
    $proc.StartInfo = $psi
    $proc.EnableRaisingEvents = $false

    # Stream log writers (must keep references so they aren't GC'd)
    $outWriter = New-Object System.IO.StreamWriter($LogOut, $false, [System.Text.Encoding]::UTF8)
    $errWriter = New-Object System.IO.StreamWriter($LogErr, $false, [System.Text.Encoding]::UTF8)
    $outWriter.AutoFlush = $true
    $errWriter.AutoFlush = $true

    $outHandler = [System.Diagnostics.DataReceivedEventHandler] {
        param($sender, $e)
        if ($null -ne $e.Data) { $outWriter.WriteLine($e.Data) }
    }
    $errHandler = [System.Diagnostics.DataReceivedEventHandler] {
        param($sender, $e)
        if ($null -ne $e.Data) { $errWriter.WriteLine($e.Data) }
    }

    # Problem: script-scoped writers in event handlers can be tricky in PS.
    # Simpler approach below: redirect via cmd.exe to files, CreateNoWindow.
    $outWriter.Close(); $errWriter.Close()
    $proc = $null

    # --- Reliable Windows pattern: cmd /c with file redirection in a hidden window ---
    # Parent is cmd; it owns the child; this shell never inherits I/O.
    $argString = ($ArgumentList | ForEach-Object {
        if ($_ -match '[\s"&<>|^%]') { '"' + ($_ -replace '"', '""') + '"' } else { $_ }
    }) -join ' '

    # cmd line:  cd /d workdir && "exe" args > out 2> err
    $cmdLine = '/c cd /d "' + $WorkingDirectory + '" && "' + $FilePath + '" ' + $argString +
        ' > "' + $LogOut + '" 2> "' + $LogErr + '"'

    $p = Start-Process -FilePath "cmd.exe" `
        -ArgumentList $cmdLine `
        -WindowStyle Hidden `
        -PassThru

    if (-not $p) { throw "Start-Process returned null for $FilePath" }
    Set-Content -Path $PidFile -Value $p.Id -Encoding ascii
    return $p.Id
}

function Start-Backend {
    if (Test-PortInUse $BackendPort) {
        Write-Host "[backend] port $BackendPort already in use — skip start" -ForegroundColor Yellow
        return
    }

    $python = Get-PythonCmd
    $logOut = Join-Path $RunDir "backend.out.log"
    $logErr = Join-Path $RunDir "backend.err.log"
    $pidFile = Join-Path $RunDir "backend.pid"

    Write-Host "[backend] starting uvicorn on 127.0.0.1:$BackendPort ..." -ForegroundColor Cyan
    $processId = Start-DetachedProcess `
        -FilePath $python `
        -ArgumentList @(
            "-m", "uvicorn", "app.main:app",
            "--host", "127.0.0.1",
            "--port", "$BackendPort",
            "--reload"
        ) `
        -WorkingDirectory $BackendDir `
        -LogOut $logOut `
        -LogErr $logErr `
        -PidFile $pidFile

    Write-Host "[backend] pid=$processId  logs: $logOut" -ForegroundColor Green
    Write-Host "[backend] API docs: http://127.0.0.1:$BackendPort/docs"
}

function Start-Frontend {
    if (Test-PortInUse $FrontendPort) {
        Write-Host "[frontend] port $FrontendPort already in use — skip start" -ForegroundColor Yellow
        return
    }

    $node = Get-NodeCmd
    $viteJs = Join-Path $FrontendDir "node_modules\vite\bin\vite.js"
    if (-not (Test-Path $viteJs)) {
        throw "Vite not found at $viteJs — run 'npm install' in frontend/"
    }

    $logOut = Join-Path $RunDir "frontend.out.log"
    $logErr = Join-Path $RunDir "frontend.err.log"
    $pidFile = Join-Path $RunDir "frontend.pid"

    Write-Host "[frontend] starting Vite on 0.0.0.0:$FrontendPort (LAN) ..." -ForegroundColor Cyan
    $processId = Start-DetachedProcess `
        -FilePath $node `
        -ArgumentList @(
            $viteJs,
            "--host", "0.0.0.0",
            "--port", "$FrontendPort"
        ) `
        -WorkingDirectory $FrontendDir `
        -LogOut $logOut `
        -LogErr $logErr `
        -PidFile $pidFile

    Write-Host "[frontend] pid=$processId  logs: $logOut" -ForegroundColor Green
    Write-Host "[frontend] UI (this PC): http://127.0.0.1:$FrontendPort"
    $lan = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object { $_.IPAddress -notlike '127.*' -and $_.PrefixOrigin -ne 'WellKnown' } |
        Select-Object -ExpandProperty IPAddress
    foreach ($ip in $lan) {
        Write-Host "[frontend] UI (LAN):  http://${ip}:$FrontendPort"
    }
}

Write-Host "=== Director Studio start ===" -ForegroundColor White
Write-Host "root: $Root"

if (-not $FrontendOnly) { Start-Backend }
if (-not $BackendOnly) { Start-Frontend }

Write-Host ""
Write-Host "Done. Terminal is free — you can keep typing here." -ForegroundColor Green
Write-Host "Stop:  .\kill.ps1" -ForegroundColor DarkGray
Write-Host "Logs:  .run\" -ForegroundColor DarkGray
