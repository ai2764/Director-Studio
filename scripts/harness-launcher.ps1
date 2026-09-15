# Pure launcher helpers. Dot-sourcing this file starts no processes.
function Resolve-AgentRuntime([string]$Requested, [string]$EnvFile) {
    $runtime = $Requested
    if (-not $runtime) { $runtime = $env:DS_DIRECTOR_AGENT_RUNTIME }
    if (-not $runtime -and (Test-Path -LiteralPath $EnvFile)) {
        foreach ($line in Get-Content -LiteralPath $EnvFile) {
            if ($line -match '^\s*(?:export\s+)?DS_DIRECTOR_AGENT_RUNTIME\s*=\s*["'']?(legacy|harness)["'']?\s*(?:#.*)?$') {
                $runtime = $Matches[1]
            } elseif ($line -match '^\s*(?:export\s+)?DS_DIRECTOR_AGENT_RUNTIME\s*=') {
                throw 'Invalid DS_DIRECTOR_AGENT_RUNTIME in backend/.env; expected legacy or harness.'
            }
        }
    }
    if (-not $runtime) { $runtime = 'harness' }
    $runtime = $runtime.Trim().ToLowerInvariant()
    if ($runtime -notin @('legacy', 'harness')) { throw 'Agent runtime must be legacy or harness' }
    return $runtime
}

function Get-HarnessToken([string]$RunDirectory) {
    if ($env:DS_HARNESS_INTERNAL_TOKEN) { return $env:DS_HARNESS_INTERNAL_TOKEN }
    $tokenFile = Join-Path $RunDirectory 'harness.token'
    if (Test-Path -LiteralPath $tokenFile) {
        $existing = (Get-Content -LiteralPath $tokenFile -Raw).Trim()
        if ($existing -match '^[a-f0-9]{64}$') { return $existing }
        throw 'Invalid .run/harness.token; stop the existing services before replacing this file.'
    }
    $bytes = New-Object byte[] 32
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    $token = -join ($bytes | ForEach-Object { $_.ToString('x2') })
    [IO.File]::WriteAllText($tokenFile, $token)
    return $token
}

function Test-HarnessIdentity([int]$Port, [string]$Token) {
    try {
        $response = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" `
            -Headers @{Authorization="Bearer $Token"} -TimeoutSec 2
        return ($response.ok -eq $true -and $response.service -eq 'director-studio-harness' -and $response.protocol -eq 1)
    } catch { return $false }
}

function Get-HarnessEnvironment {
    # Do not inherit provider secrets, proxy URLs, NODE_OPTIONS or app configuration.
    $child = @{}
    foreach ($key in @('SystemRoot', 'WINDIR', 'SystemDrive', 'ComSpec', 'PATH', 'PATHEXT', 'TEMP', 'TMP', 'USERPROFILE', 'LOCALAPPDATA', 'APPDATA', 'NUMBER_OF_PROCESSORS', 'PROCESSOR_ARCHITECTURE', 'DS_HARNESS_INTERNAL_TOKEN', 'DS_HARNESS_PORT')) {
        $value = [Environment]::GetEnvironmentVariable($key, 'Process')
        if ($null -ne $value) { $child[$key] = $value }
    }
    return $child
}

function Wait-HarnessBackend {
    $deadline = [DateTime]::UtcNow.AddSeconds(30)
    while ([DateTime]::UtcNow -lt $deadline) {
        $active = $null
        try {
            $active = Invoke-RestMethod -Uri "http://127.0.0.1:$BackendPort/api/director/runtime?check_sidecar=true" -TimeoutSec 5
        } catch { }
        if ($null -ne $active) {
            if ($active.runtime -ne 'harness' -or $active.harness_port -ne $HarnessPort -or $active.sidecar_ready -ne $true) {
                throw 'Backend cannot authenticate the selected Harness sidecar. Stop the backend before restarting with this runtime/token.'
            }
            return
        }
        Start-Sleep -Milliseconds 250
    }
    throw 'Backend did not become healthy; inspect .run/backend.err.log.'
}

function Start-Harness {
    $token = $env:DS_HARNESS_INTERNAL_TOKEN
    if (Test-PortInUse $HarnessPort) {
        if (-not (Test-HarnessIdentity $HarnessPort $token)) {
            throw "Port $HarnessPort is occupied by a different service or Harness token."
        }
        Write-Host '[harness] existing authenticated sidecar is ready' -ForegroundColor Green
        return
    }
    $harnessDir = Join-Path $Root 'harness'
    if (-not (Test-Path -LiteralPath (Join-Path $harnessDir 'node_modules/tsx/package.json'))) {
        throw 'Harness dependencies missing. Run npm ci in harness/ once, then launch again.'
    }
    $node = Get-NodeCmd
    $processId = Start-DetachedProcess -FilePath $node `
        -ArgumentList @('--import', 'tsx', 'src/server.ts') -WorkingDirectory $harnessDir `
        -LogOut (Join-Path $RunDir 'harness.out.log') -LogErr (Join-Path $RunDir 'harness.err.log') `
        -PidFile (Join-Path $RunDir 'harness.pid') -ChildEnvironment (Get-HarnessEnvironment)
    $script:StartedHarnessProcessId = $processId
    $deadline = [DateTime]::UtcNow.AddSeconds(20)
    while ([DateTime]::UtcNow -lt $deadline) {
        if (Test-HarnessIdentity $HarnessPort $token) {
            Write-Host "[harness] ready on 127.0.0.1:$HarnessPort" -ForegroundColor Green
            return
        }
        Start-Sleep -Milliseconds 250
    }
    throw 'Harness did not become healthy; inspect .run/harness.err.log.'
}
