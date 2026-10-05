# Isolated video-context instance helpers. Dot-sourcing starts nothing.
function Get-VideoContextLayout([string]$Root) {
    $run = Join-Path $Root '.run\video-context'
    $data = Join-Path $run 'data'
    [pscustomobject]@{
        Root = $Root
        RunDir = $run
        DataDir = $data
        JobsDir = Join-Path $data 'jobs'
        ProjectsDir = Join-Path $data 'projects'
        LibraryRoot = Join-Path $data 'library'
        LibraryDir = Join-Path $data 'library\actors'
        ProfilesDir = Join-Path $data 'workflow_profiles'
        BackendPort = 8792
        FrontendPort = 5174
        HarnessPort = 8793
        BackendUrl = 'http://127.0.0.1:8792'
    }
}

function Set-VideoContextEnvironment([string]$Root) {
    $layout = Get-VideoContextLayout $Root
    $env:DS_VIDEO_CONTEXT_ENABLED = 'true'
    $env:DS_H3_BUILTIN_WORKFLOW = 'h3_ref2va_fast4.api.json'
    $env:DS_BACKEND_URL = $layout.BackendUrl
    $env:DS_DATA_DIR = $layout.DataDir
    $env:DS_JOBS_DIR = $layout.JobsDir
    $env:DS_PROJECTS_DIR = $layout.ProjectsDir
    $env:DS_LIBRARY_ROOT = $layout.LibraryRoot
    $env:DS_LIBRARY_DIR = $layout.LibraryDir
    $env:DS_WORKFLOW_PROFILES_DIR = $layout.ProfilesDir
    return $layout
}

function Get-ListenerProcessId([int]$Port) {
    $conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if (-not $conn) { return $null }
    return [int]$conn.OwningProcess
}

function Get-ProcessIdentityText([int]$ProcessId) {
    $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId" -ErrorAction SilentlyContinue
    if (-not $proc) { return '' }
    $parentText = ''
    if ($proc.ParentProcessId) {
        $parent = Get-CimInstance Win32_Process -Filter "ProcessId=$($proc.ParentProcessId)" -ErrorAction SilentlyContinue
        if ($parent) { $parentText = [string]$parent.CommandLine }
    }
    return (([string]$proc.CommandLine) + ' ' + $parentText)
}

function Test-VideoContextOwner([int]$ProcessId, [string]$Root, [string]$Role) {
    $text = Get-ProcessIdentityText $ProcessId
    if (-not $text) { return $false }
    if ($text.IndexOf($Root, [System.StringComparison]::OrdinalIgnoreCase) -lt 0) { return $false }
    switch ($Role) {
        'backend' { return ($text -match 'uvicorn' -and $text -match '--port[\s=]+8792\b') }
        'frontend' { return ($text -match 'vite' -and $text -match '--port[\s=]+5174\b') }
        'harness' { return ($text -match 'server\.ts' -and $text -match 'harness') }
        default { return $false }
    }
}

function Assert-VideoContextPort([int]$Port, [string]$Root, [string]$Role) {
    $owner = Get-ListenerProcessId $Port
    if (-not $owner) { return 'free' }
    if (Test-VideoContextOwner $owner $Root $Role) { return 'reuse' }
    throw "Port $Port is occupied by a different process (pid $owner)."
}

function Stop-ProcessTree([int]$ProcessId) {
    & taskkill.exe /PID $ProcessId /T /F | Out-Null
}

function Stop-RecordedProcess([string]$PidFile, [string]$Root, [string]$Role) {
    if (-not (Test-Path -LiteralPath $PidFile)) { return 'missing' }
    $processId = 0
    if (-not [int]::TryParse((Get-Content -LiteralPath $PidFile -Raw).Trim(), [ref]$processId)) {
        throw "Invalid pid file $PidFile"
    }
    $text = Get-ProcessIdentityText $processId
    if (-not $text) {
        Remove-Item -LiteralPath $PidFile -Force
        return 'stale'
    }
    if (-not (Test-VideoContextOwner $processId $Root $Role)) {
        throw "PID $processId was reused by a different command or directory. Refusing to stop it."
    }
    Stop-ProcessTree $processId
    Remove-Item -LiteralPath $PidFile -Force
    return 'stopped'
}

function Stop-VideoContextInstance([string]$Root) {
    $layout = Get-VideoContextLayout $Root
    foreach ($item in @(
        @{ Role = 'backend'; File = 'backend.pid' },
        @{ Role = 'harness'; File = 'harness.pid' },
        @{ Role = 'frontend'; File = 'frontend.pid' }
    )) {
        Stop-RecordedProcess -PidFile (Join-Path $layout.RunDir $item.File) -Root $Root -Role $item.Role
    }
}

function Invoke-DirectorStudioStart($Root, $Layout) {
    & (Join-Path $Root 'start.ps1') `
        -NoReload `
        -BackendPort $Layout.BackendPort `
        -FrontendPort $Layout.FrontendPort `
        -HarnessPort $Layout.HarnessPort `
        -RunDir $Layout.RunDir
}

function Start-VideoContextInstance([string]$Root) {
    $layout = Set-VideoContextEnvironment -Root $Root
    foreach ($role in @('backend', 'harness', 'frontend')) {
        $port = switch ($role) {
            'backend' { $layout.BackendPort }
            'harness' { $layout.HarnessPort }
            'frontend' { $layout.FrontendPort }
        }
        Assert-VideoContextPort -Port $port -Root $Root -Role $role | Out-Null
    }
    foreach ($dir in @(
        $layout.RunDir, $layout.JobsDir, $layout.ProjectsDir,
        $layout.LibraryRoot, $layout.LibraryDir, $layout.ProfilesDir
    )) {
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
    }
    Invoke-DirectorStudioStart -Root $Root -Layout $layout
    return $layout
}
